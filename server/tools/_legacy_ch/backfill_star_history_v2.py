#!/usr/bin/env python3
"""补 star 周历史 v2 —— ClickHouse 全库切片，一次请求覆盖多仓多周。

与 v1 的本质区别（v1 = 逐周定仓 + 按 500 仓 IN 分批，5 万仓要上百次请求）：
  Stage 1 定仓：**1 次请求**用 `ORDER BY c DESC LIMIT N BY wk` 拿到「逐周各前 N」的并集。
  Stage 2 拉数：**按时间片拉全库**（默认每 10 天 1 次请求，返回该片全库按天明细），
               本地过滤出目标仓 —— 不再拼 `repo_name IN (500个)`。
  合计：取一年数据 = 1 + ~29 次请求（v1 同口径需 41 + 87 = 128 次）。

为什么默认 10 天而不是整月：playground 单次响应约 28MB 就断流（IncompleteRead），
  实测 2026-01 整月按天明细 56MB → 必挂；切半月 28.3MB 险过；切 10 天稳定。

数据源: play.clickhouse.com (user=explorer, 免密码) 表 github_events, WatchEvent = star。
写入: gh_repo_star_weekly (uq_gh_star_weekly UNIQUE(repo_id, week_start) + ON CONFLICT DO UPDATE)。

用法（容器内跑，同时能连 db 与外网）:
  # 只定仓 + 只读试跑（不写库）
  docker compose exec -T api python3 tools/backfill_star_history_v2.py --per-week-top 3000 --dry-run
  # 周新增各前 3000 的仓，补 2026 全年（写库）
  docker compose exec -T api python3 tools/backfill_star_history_v2.py --per-week-top 3000 --write
  # 只补某区间（失败片重跑）
  docker compose exec -T api python3 tools/backfill_star_history_v2.py --per-week-top 3000 --since 2026-01-01 --until 2026-03-01 --write
  # 全库全仓（不筛仓）
  docker compose exec -T api python3 tools/backfill_star_history_v2.py --all --write
"""
import argparse
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, "/app")
from app import db  # noqa: E402

CH_URL = "https://play.clickhouse.com/?user=explorer&default_format=TSV"
WRITE_CHUNK = 1000
UA = "githubstar-backfill/2.0"

FLUSH_SQL = (
    "INSERT INTO gh_repo_star_weekly "
    "(repo_id, week_start, week_key, week_label, week_index, weekly_new, cumulative, days, source) "
    "VALUES {v} "
    "ON CONFLICT (repo_id, week_start) DO UPDATE SET "
    "weekly_new=EXCLUDED.weekly_new, cumulative=EXCLUDED.cumulative, "
    "days=EXCLUDED.days, source=EXCLUDED.source, fetched_at=now()"
)
ROW_VALUE = "(%s,%s,%s,%s,%s,%s,%s,%s,'clickhouse_gh_events')"


def ch(sql, timeout=300, retries=4):
    """POST SQL 到 playground；断流/过载走指数退避重试。"""
    last = None
    for i in range(max(1, retries)):
        try:
            req = urllib.request.Request(CH_URL, data=sql.encode("utf-8"),
                                         method="POST", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1:
                time.sleep(2 * (i + 1))
    raise last


def date_slices(since: str, until: str, step_days: int = 10):
    """[since, until) 按 step_days 天切片；step_days<=0 时按月切。

    片长必须够小：playground 单响应约 28MB 就断流（IncompleteRead），
    整月按天明细可达 56MB，必挂。
    """
    start = datetime.strptime(since, "%Y-%m-%d").date()
    stop = datetime.strptime(until, "%Y-%m-%d").date()
    out, d = [], start
    while d < stop:
        if step_days and step_days > 0:
            nxt = d + timedelta(days=step_days)
        else:
            nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
        b = min(nxt, stop)
        out.append((d.isoformat(), b.isoformat()))
        d = b
    return out


def stage1_pick(per_week_top, since, until):
    """1 次请求：逐周各取 star 新增前 N 的仓名并集。"""
    sql = (
        "SELECT repo_name, toStartOfWeek(created_at,1) wk, count() c FROM github_events "
        "WHERE event_type='WatchEvent' AND created_at >= '%s 00:00:00' "
        "AND created_at < '%s 00:00:00' "
        "GROUP BY repo_name, wk ORDER BY c DESC LIMIT %d BY wk FORMAT TSV"
        % (since, until, int(per_week_top))
    )
    t0 = time.time()
    names, weeks = set(), set()
    for line in ch(sql, timeout=600).splitlines():
        p = line.split("\t")
        if len(p) >= 2 and p[0].strip():
            names.add(p[0])
            weeks.add(p[1])
    print("Stage1 定仓: 1 次请求, %.1fs, 覆盖 %d 周, 仓 %d 个"
          % (time.time() - t0, len(weeks), len(names)), flush=True)
    return names


def stage2_fetch_range(a, b, targets=None):
    """1 次请求：某时间片全库 (repo_name, day, count)。targets 非空则本地过滤。"""
    sql = (
        "SELECT repo_name, toDate(created_at) d, count() n FROM github_events "
        "WHERE event_type='WatchEvent' AND created_at >= '%s 00:00:00' "
        "AND created_at < '%s 00:00:00' GROUP BY repo_name, d FORMAT TSV" % (a, b)
    )
    out = {}
    for line in ch(sql, timeout=600).splitlines():
        p = line.split("\t")
        if len(p) < 3:
            continue
        name, ds, n = p[0], p[1], p[2]
        if targets is not None and name not in targets:
            continue
        try:
            out.setdefault(name, {})[ds] = int(n)
        except ValueError:
            continue
    return out


def to_week_rows(repo_id, stars, daily):
    """{date: count} -> 周记录元组列表（cumulative 由当前 stars 反推）。"""
    buckets = {}
    for ds, n in daily.items():
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        ws = d - timedelta(days=d.weekday())
        b = buckets.setdefault(ws, {"n": 0, "days": []})
        b["n"] += n
        b["days"].append({"day": ds, "count": n})
    weeks = []
    for ws in sorted(buckets):
        b = buckets[ws]
        b["days"].sort(key=lambda x: x["day"])
        weeks.append((ws, b["n"], b["days"]))
    cum, rev = stars, []
    for ws, wn, days in reversed(weeks):
        rev.append((ws, wn, days, cum))
        cum -= wn
    out = []
    for ws, wn, days, c in reversed(rev):
        y, w, _ = ws.isocalendar()
        out.append((repo_id, ws, "%d-W%02d" % (y, w), "W%02d" % w, w, wn, c, db.Json(days)))
    return out


def flush(rows):
    n = 0
    for i in range(0, len(rows), WRITE_CHUNK):
        chunk = rows[i:i + WRITE_CHUNK]
        db.execute(FLUSH_SQL.format(v=",".join([ROW_VALUE] * len(chunk))),
                   [x for r in chunk for x in r])
        n += len(chunk)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-week-top", type=int, default=3000,
                    help="逐周各取 star 新增前 N 的仓（0=关闭，配合 --all 用全库）")
    ap.add_argument("--all", action="store_true", help="不筛仓，写入全库所有仓")
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--until", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--slice-days", type=int, default=10,
                    help="Stage2 时间片长度（天）；playground 单响应约 28MB 断流，默认 10")
    ap.add_argument("--write", action="store_true", help="真写库；缺省为只读试跑")
    ap.add_argument("--dry-run", action="store_true", help="只跑不写（缺省行为，显式给个对称参数）")
    args = ap.parse_args()

    until = args.until
    slices = date_slices(args.since, until, args.slice_days)
    print("目标区间 %s ~ %s，切 %d 个片（%d 天/片）"
          % (args.since, until, len(slices), args.slice_days), flush=True)

    targets = None
    if not args.all and args.per_week_top > 0:
        targets = stage1_pick(args.per_week_top, args.since, until)
        rows = db.query_all(
            "SELECT id, full_name, stars FROM gh_repo WHERE lower(full_name) = ANY(%s)",
            ([n.lower() for n in targets],))
        by_name = {r["full_name"].lower(): r for r in rows}
        print("  其中 %d 仓已在本库（其余为未收录/改名）" % len(rows), flush=True)

    if targets is None:
        rows = db.query_all("SELECT id, full_name, stars FROM gh_repo")
        by_name = {r["full_name"].lower(): r for r in rows}
        print("  全库 %d 仓" % len(rows), flush=True)

    t0 = time.time()
    got_days = got_weeks = written = failed = 0
    for i, (a, b) in enumerate(slices, 1):
        tm = time.time()
        try:
            daily = stage2_fetch_range(a, b, targets)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("  片 %d/%d %s~%s 失败: %s" % (i, len(slices), a, b, e), flush=True)
            continue
        # 按 repo_id 合并：ClickHouse 的 repo_name 大小写/改名变体（如 openclaw/moltbot/clawdbot）
        # 会映射到同一个库内 repo_id，直接拼进同一批 INSERT 会撞
        # 「ON CONFLICT DO UPDATE cannot affect row a second time」。
        merged = {}
        for name, dd in daily.items():
            r = by_name.get(name.lower())
            if not r:
                continue
            m = merged.setdefault(r["id"], {"daily": {}, "stars": r["stars"] or 0, "n": 0})
            for ds, cnt in dd.items():
                m["daily"][ds] = m["daily"].get(ds, 0) + cnt
            m["n"] += len(dd)
        batch = []
        for rid, m in merged.items():
            batch.extend(to_week_rows(rid, m["stars"], m["daily"]))
            got_days += m["n"]
        got_weeks += len(batch)
        if args.write and batch:
            written += flush(batch)
        print("  片 %d/%d %s ~ %s | 命中 %d 仓 | 周记录 +%d | 累计写入 %d | %.0fs"
              % (i, len(slices), a, b, len(daily), len(batch), written, time.time() - tm), flush=True)
        time.sleep(1)  # playground 公共资源，片间小睡

    print("完成: 请求 %d 次 | 日记录 %d | 周记录 %d | 写入 %d | 失败片 %d | 总耗时 %.0fs"
          % (1 + len(slices) + (1 if targets else 0), got_days, got_weeks, written, failed,
             time.time() - t0), flush=True)
    if not args.write:
        print("(未写库；加 --write 真写)", flush=True)


if __name__ == "__main__":
    main()
