#!/usr/bin/env python3
"""每周前 N 仓「全库导入」—— 含不在 gh_repo 的外部仓（用户 2026-10 定）。

设计（严格遵守用户硬约束：一次接口尽可能多拉）：
  **1 次请求**拿到全区间「逐周各前 N」的 (repo_name, week_start, count)
  —— `ORDER BY c DESC LIMIT N BY wk`，一行 SQL 返回所有周 × 所有仓，绝无逐仓调用。

全量入库：
  - 不在 gh_repo 的外部仓 → 批量 INSERT gh_repo(full_name/owner/name)，id 由本地序列自增
  - 周历史写 gh_repo_star_weekly

去重由数据库完成：
  - gh_repo 先查后插（lower(full_name) 比对）
  - gh_repo_star_weekly 走 ON CONFLICT (repo_id, week_start) DO UPDATE

用法（容器内跑）:
  docker compose exec -T api python3 tools/import_top_weekly.py --per-week-top 3000 --since 2026-01-01 --write
  docker compose exec -T api python3 tools/import_top_weekly.py --per-week-top 5000 --since 2026-01-01 --write
"""
import argparse
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, "/app")
from app import db  # noqa: E402

CH_URL = "https://play.clickhouse.com/?user=explorer&default_format=TSV"
UA = "githubstar-topn/1.0"
CHUNK = 800


def ch(sql, timeout=900, retries=5):
    last = None
    for i in range(max(1, retries)):
        try:
            req = urllib.request.Request(CH_URL, data=sql.encode("utf-8"),
                                         method="POST", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            last = RuntimeError("%s: %s" % (type(e).__name__, body[:200]))
            if i < retries - 1:
                time.sleep(5 * (i + 1))
    raise last


def fetch_topn(since, until, n):
    """1 次请求：全区间「逐周各前 N」的 (repo_name, week_start, count)。"""
    sql = (
        "SELECT repo_name, toStartOfWeek(created_at,1) wk, count() c FROM github_events "
        "WHERE event_type='WatchEvent' AND created_at >= '%s 00:00:00' "
        "AND created_at < '%s 00:00:00' "
        "GROUP BY repo_name, wk ORDER BY c DESC LIMIT %d BY wk FORMAT TSV"
        % (since, until, int(n))
    )
    t0 = time.time()
    out = []
    for line in ch(sql).splitlines():
        p = line.split("\t")
        if len(p) < 3:
            continue
        try:
            out.append((p[0], p[1], int(p[2])))
        except ValueError:
            continue
    print("Stage1: 1 次请求 | %.1fs | %d 行 | %d 周 | %d 仓"
          % (time.time() - t0, len(out),
             len({x[1] for x in out}), len({x[0] for x in out})), flush=True)
    return out


def _map_existing(names_lower):
    """批量查本库已有的 lower(full_name) -> id。"""
    m = {}
    for i in range(0, len(names_lower), 5000):
        ck = names_lower[i:i + 5000]
        rows = db.query_all(
            "SELECT id, lower(full_name) fn FROM gh_repo WHERE lower(full_name) = ANY(%s)", (ck,))
        for r in rows:
            m[r["fn"]] = r["id"]
    return m


def ensure_repos(names):
    """确保这些仓都在 gh_repo；缺失的批量补齐。返回 lower(name) -> id。"""
    lower_all = sorted({n.lower() for n in names})
    idmap = _map_existing(lower_all)
    # 保持原始大小写
    origin = {}
    for n in names:
        origin.setdefault(n.lower(), n)
    missing = [k for k in lower_all if k not in idmap]
    print("  gh_repo 命中 %d | 缺失 %d（将批量补入）" % (len(idmap), len(missing)), flush=True)
    if not missing:
        return idmap
    for i in range(0, len(missing), CHUNK):
        ck = missing[i:i + CHUNK]
        vals, params = [], []
        for k in ck:
            fn = origin[k]
            owner, _, name = fn.partition("/")
            owner = owner or fn
            name = name or fn
            vals.append("(%s,%s,%s)")
            params += [fn, owner, name]
        db.execute("INSERT INTO gh_repo (full_name, owner, name) VALUES " + ",".join(vals), params)
    idmap.update(_map_existing([k for k in missing]))
    print("  补入完成，现命中 %d" % len(idmap), flush=True)
    return idmap


def flush(rows):
    n = 0
    for i in range(0, len(rows), CHUNK):
        ck = rows[i:i + CHUNK]
        db.execute(
            "INSERT INTO gh_repo_star_weekly "
            "(repo_id, week_start, week_key, week_label, week_index, weekly_new, cumulative, days, source) "
            "VALUES " + ",".join(["( %s,%s,%s,%s,%s,%s,%s,%s,'clickhouse_topn')"] * len(ck)) +
            " ON CONFLICT (repo_id, week_start) DO UPDATE SET "
            "weekly_new=EXCLUDED.weekly_new, cumulative=EXCLUDED.cumulative, "
            "days=EXCLUDED.days, source=EXCLUDED.source, fetched_at=now()",
            [x for r in ck for x in r])
        n += len(ck)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-week-top", type=int, default=3000)
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--until", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    print("区间 %s ~ %s | 每周前 %d | %s"
          % (args.since, args.until, args.per_week_top,
             "写库" if args.write else "只读试跑"), flush=True)

    data = fetch_topn(args.since, args.until, args.per_week_top)
    if not data:
        print("无数据", flush=True)
        return

    names = {x[0] for x in data}
    idmap = ensure_repos(names) if args.write else _map_existing([n.lower() for n in names])

    by_repo = {}
    for name, wk, c in data:
        by_repo.setdefault(name.lower(), {})[wk] = c

    rows = []
    skipped = 0
    for name_l, weeks in by_repo.items():
        rid = idmap.get(name_l)
        if not rid:
            skipped += 1
            continue
        cum = 0
        for ws in sorted(weeks):
            cum += weeks[ws]
            d = datetime.strptime(ws, "%Y-%m-%d").date()
            y, w, _ = d.isocalendar()
            rows.append((rid, ws, "%d-W%02d" % (y, w), "W%02d" % w, w, weeks[ws], cum, "[]"))

    written = flush(rows) if args.write else 0
    print("完成: 仓 %d | 周记录 %d | 写入 %d | 跳过(无id) %d | %s"
          % (len(by_repo), len(rows), written, skipped,
             "已写库" if args.write else "只读试跑"), flush=True)


if __name__ == "__main__":
    main()
