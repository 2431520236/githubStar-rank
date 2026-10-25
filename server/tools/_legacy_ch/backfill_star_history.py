#!/usr/bin/env python3
"""从 ClickHouse 公共数据集(github_events)批量补 star 历史。

数据源: play.clickhouse.com (user=explorer) 表 github_events, WatchEvent 即 star。
策略: 一次请求塞一批 repo_name(默认 500 个), 只取 (repo_name, day, count) 三个字段;
      Python 侧按 ISO 周聚合, 幂等 upsert 进 gh_repo_star_weekly。
      批拉批写、流式处理 —— 内存峰值 = 一批的量, 不随库规模增长。

用法(在容器内跑, 能同时访问 db 和外网):
  docker compose exec -T api python3 tools/backfill_star_history.py --limit 500 --dry-run
  docker compose exec -T api python3 tools/backfill_star_history.py --limit 500 --write
  docker compose exec -T api python3 tools/backfill_star_history.py --limit 0 --write          # 全量
  docker compose exec -T api python3 tools/backfill_star_history.py --limit 0 --only-missing --write  # 只补缺失
  docker compose exec -T api python3 tools/backfill_star_history.py --limit 5000 --only-missing --write  # 只补 star 前 5000(推荐: 数据量约 1/10)
  docker compose exec -T api python3 tools/backfill_star_history.py --limit 5000 --order-by gain30d --write  # 按近 30 天新增选仓
"""
import argparse
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
from app import db  # noqa: E402

CH_URL = "https://play.clickhouse.com/"
CH_USER = "explorer"
LOOKBACK_DAYS = 400          # 回溯窗口(天, 仅当 SINCE_DATE 为空时生效)
SINCE_DATE = "2026-01-01"    # 回溯起点; 用户 2026-10 定「从 2026 年开始」, 优先于 LOOKBACK_DAYS
WRITE_CHUNK = 1000           # 单条 INSERT 里最多几行(多值 VALUES)

FLUSH_SQL = (
    "INSERT INTO gh_repo_star_weekly "
    "(repo_id, week_start, week_key, week_label, week_index, weekly_new, cumulative, days, source) "
    "VALUES {v} "
    "ON CONFLICT (repo_id, week_start) DO UPDATE SET "
    "weekly_new=EXCLUDED.weekly_new, cumulative=EXCLUDED.cumulative, "
    "days=EXCLUDED.days, source=EXCLUDED.source, fetched_at=now()"
)
ROW_VALUE = "(%s,%s,%s,%s,%s,%s,%s,%s,'clickhouse_gh_events')"


def ch_query(sql: str, timeout: int = 300, retries: int = 3) -> str:
    """POST SQL 到 ClickHouse playground。

    公共 playground 过载时返回 HTTP 500, 属暂时性故障 —— 内部指数退避重试,
    不让整批任务因一次 500 而空转或中断(运行时兜底, 不依赖调用方处理)。
    """
    last = None
    for i in range(max(1, retries)):
        try:
            req = urllib.request.Request(
                CH_URL + "?user=" + CH_USER + "&default_format=TSV",
                data=sql.encode("utf-8"), method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1:
                time.sleep(2 * (i + 1))
    raise last


def pick_weekly_top(n):
    """从 ClickHouse 取「本周(周一至今) star 新增」倒序前 N 个仓名。

    本周未走完时窗口 = [本周一, min(下周一, 明天))，即「当前不足一周也照算」。
    返回 [(repo_name, 本周新增数)]，已按新增倒序。
    """
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    nxt = min(monday + timedelta(days=7), today + timedelta(days=1))
    sql = (
        "SELECT repo_name, count() AS c FROM github_events "
        "WHERE event_type = 'WatchEvent' "
        "AND created_at >= '%s 00:00:00' AND created_at < '%s 00:00:00' "
        "GROUP BY repo_name ORDER BY c DESC LIMIT %d"
        % (monday.isoformat(), nxt.isoformat(), int(n))
    )
    out = []
    for line in ch_query(sql).splitlines():
        p = line.split("\t")
        if len(p) >= 2 and p[0].strip():
            try:
                out.append((p[0], int(p[1])))
            except ValueError:
                continue
    return out


def pick_per_week_top(n, since, until=None):
    """逐周取「该周 star 新增前 N」的仓名并集(用户 2026-10 定的正取口径)。

    对区间内每一周单独查 ClickHouse 排名再并起来 —— 保证「第一周 / 第二周……
    各自的前 N 热仓」都被覆盖, 而不是固定取全库 star 最多的 N 个仓。
    返回 (仓名集合, 覆盖周数)。
    """
    start = datetime.strptime(since, "%Y-%m-%d").date()
    start = start - timedelta(days=start.weekday())          # 对齐到周一
    stop = (datetime.strptime(until, "%Y-%m-%d").date()
            if until else datetime.now(timezone.utc).date())
    names, weeks, d = set(), 0, start
    while d < stop:
        nxt = min(d + timedelta(days=7), stop)
        sql = ("SELECT repo_name, count() AS c FROM github_events "
               "WHERE event_type = 'WatchEvent' "
               "AND created_at >= '%s 00:00:00' AND created_at < '%s 00:00:00' "
               "GROUP BY repo_name ORDER BY c DESC LIMIT %d"
               % (d.isoformat(), nxt.isoformat(), int(n)))
        for line in ch_query(sql).splitlines():
            p = line.split("\t")
            if len(p) >= 2 and p[0].strip():
                names.add(p[0])
        weeks += 1
        d = nxt
    return names, weeks


def fetch_daily(names):
    """一批 repo -> {full_name: {date_str: count}}"""
    safe = [n.replace("'", "") for n in names]
    inlist = ",".join("'" + n + "'" for n in safe)
    sql = (
        "SELECT repo_name, toDate(created_at) AS d, count() AS n "
        "FROM github_events "
        "WHERE event_type = 'WatchEvent' AND repo_name IN (" + inlist + ") "
        "AND created_at >= '" + SINCE_DATE + " 00:00:00' "
        "GROUP BY repo_name, d FORMAT TSV"
    )
    out = {}
    for line in ch_query(sql).splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, d, n = parts[0], parts[1], parts[2]
        try:
            out.setdefault(name, {})[d] = int(n)
        except ValueError:
            continue
    return out


def to_week_rows(repo_id, stars, daily):
    """{date_str: n} -> [(repo_id, week_start, key, label, idx, weekly_new, cumulative, Json(days))]"""
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
    # cumulative 反推: 当前 stars 视为最新周累计, 往前逐周减
    cum = stars
    rev = []
    for ws, wn, days in reversed(weeks):
        rev.append((ws, wn, days, cum))
        cum -= wn
    out = []
    for ws, wn, days, c in reversed(rev):
        y, w, _ = ws.isocalendar()
        out.append((repo_id, ws, "%d-W%02d" % (y, w), "W%02d" % w, w, wn, c, db.Json(days)))
    return out


def flush(rows):
    """批量 upsert, 每 WRITE_CHUNK 行一条 INSERT。返回写入行数。"""
    n = 0
    for i in range(0, len(rows), WRITE_CHUNK):
        chunk = rows[i:i + WRITE_CHUNK]
        sql = FLUSH_SQL.format(v=",".join([ROW_VALUE] * len(chunk)))
        params = []
        for r in chunk:
            params.extend(r)
        db.execute(sql, params)
        n += len(chunk)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500, help="取 star 最高的前 N 个仓库; 0 = 全量")
    ap.add_argument("--batch", type=int, default=500, help="每批 repo 数(一次请求塞多少个)")
    ap.add_argument("--workers", type=int, default=4, help="并发批数")
    ap.add_argument("--write", action="store_true", help="真正写库")
    ap.add_argument("--only-missing", action="store_true", help="只补库里还没有周记录的仓库")
    ap.add_argument("--order-by", choices=["stars", "gain30d", "gain7d"], default="stars",
                    help="选仓排序字段(倒序): stars=总 star 数(默认, 稳定); gain30d/gain7d=近期新增(未补过的仓为 0)")
    ap.add_argument("--min-gain", type=int, default=0,
                    help="只选「新增 star >= N」的仓(按 --order-by 对应字段); N=1 即只选有新增的仓")
    ap.add_argument("--weekly-top", type=int, default=0,
                    help="按 ClickHouse「本周(周一至今) star 新增」排名取前 N 仓(0=关闭); 倒序, 与库内字段无关")
    ap.add_argument("--per-week-top", type=int, default=0,
                    help="逐周各取「该周 star 新增前 N」的仓并集(0=关闭); 用户 2026-10 定的正取口径")
    ap.add_argument("--since", default="2026-01-01",
                    help="回溯起点 YYYY-MM-DD(默认 2026-01-01); 传空串则退回 LOOKBACK_DAYS")
    args = ap.parse_args()

    global SINCE_DATE
    SINCE_DATE = args.since or ""

    if args.per_week_top > 0:
        # 逐周各自取前 N(用户 2026-10 定): 第一周/第二周……各自的前 N 仓并集
        names_set, nweeks = pick_per_week_top(args.per_week_top, args.since)
        print("逐周前 %d 排名: 覆盖 %d 周, 去重后 %d 仓"
              % (args.per_week_top, nweeks, len(names_set)), flush=True)
        names_all = sorted(names_set)
        rows = db.query_all(
            "SELECT id, full_name, stars FROM gh_repo WHERE lower(full_name) = ANY(%s)",
            ([n.lower() for n in names_all],)) if names_all else []
        by_name = {r["full_name"].lower(): r for r in rows}
        repos = [by_name[n.lower()] for n in names_all if n.lower() in by_name]
        print("  其中 %d 仓已在本库(其余为未收录/已改名)" % len(repos), flush=True)
    elif args.weekly_top > 0:
        # 目标仓由 ClickHouse「本周新增排名」决定(倒序), 与库内字段无关
        ranked = pick_weekly_top(args.weekly_top)
        print("ClickHouse 本周新增排名: 请求前 %d, 实得 %d 仓"
              % (args.weekly_top, len(ranked)), flush=True)
        names_all = [n for n, _ in ranked]
        # GitHub 仓名大小写敏感, ClickHouse 与库内大小写常不一致 → 统一 lower 匹配
        rows = db.query_all(
            "SELECT id, full_name, stars FROM gh_repo WHERE lower(full_name) = ANY(%s)",
            ([n.lower() for n in names_all],)) if names_all else []
        by_name = {r["full_name"].lower(): r for r in rows}
        repos = [by_name[n.lower()] for n, _ in ranked if n.lower() in by_name]
        print("  其中 %d 仓已在本库(其余为未收录/已改名)" % len(repos), flush=True)
    else:
        order_col = {"stars": "r.stars", "gain30d": "r.star_gain_30d",
                     "gain7d": "r.star_gain_7d"}[args.order_by]
        conds = []
        if args.only_missing:
            conds.append("NOT EXISTS (SELECT 1 FROM gh_repo_star_weekly w WHERE w.repo_id = r.id)")
        if args.min_gain > 0:
            conds.append("COALESCE(%s, 0) >= %d" % (order_col, args.min_gain))
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        limit_sql = " LIMIT %s" if args.limit else ""
        repos = db.query_all(
            "SELECT r.id, r.full_name, r.stars FROM gh_repo r" + where +
            " ORDER BY " + order_col + " DESC NULLS LAST" + limit_sql,
            [args.limit] if args.limit else [])
    id_by_name = {r["full_name"]: r["id"] for r in repos}
    stars_by_name = {r["full_name"]: (r["stars"] or 0) for r in repos}
    names = list(id_by_name)
    print("目标仓库: %d 个; 每批 %d, 并发 %d, %s"
          % (len(names), args.batch, args.workers, "写库" if args.write else "只读试跑"), flush=True)
    if not names:
        print("没有需要补的仓库", flush=True)
        return

    batches = [names[i:i + args.batch] for i in range(0, len(names), args.batch)]
    t0 = time.time()
    got_repos = got_weeks = got_days = written = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_daily, b): i for i, b in enumerate(batches)}
        for k, f in enumerate(as_completed(futs), 1):
            try:
                daily = f.result()
            except Exception as e:  # noqa: BLE001
                print("  批 %d 失败: %s" % (futs[f] + 1, e), flush=True)
                continue
            rows = []
            for name, dd in daily.items():
                rows.extend(to_week_rows(id_by_name[name], stars_by_name.get(name, 0), dd))
                got_days += len(dd)
            got_repos += len(daily)
            got_weeks += len(rows)
            if args.write and rows:
                written += flush(rows)
            if k % 5 == 0 or k == len(batches):
                print("  %d/%d 批 | 命中 %d 仓 | 周记录 %d | 写入 %d | %.0fs"
                      % (k, len(batches), got_repos, got_weeks, written, time.time() - t0), flush=True)

    print("完成: 命中 %d/%d 仓 | 周记录 %d 条 | 日记录 %d 条 | 耗时 %.0fs"
          % (got_repos, len(names), got_weeks, got_days, time.time() - t0), flush=True)
    if args.write:
        print("已写入/更新 %d 条周记录 (ON CONFLICT 幂等, 不与旧数据重复)" % written, flush=True)
    else:
        print("(未写库; 加 --write 真正写入)", flush=True)


if __name__ == "__main__":
    main()
