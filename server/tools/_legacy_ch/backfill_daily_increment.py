#!/usr/bin/env python3
"""每日 star 增量补充 —— ClickHouse 一次请求取全库「某日 delta」落 gh_repo_star_daily。

设计要点（用户 2026-10 定）：
- **一次调用覆盖全库**：不做逐仓、不做 IN(...) 分批；ClickHouse 服务端 GROUP BY。
- **日数据每日补充**：默认补「昨天」，也可 --since/--until 补任意区间（切片 ≤3 天，避免断流）。
- **幂等**：先按 stat_date 区间 DELETE，再批量 INSERT；重复跑不产生重复行。
- 接口返回一律不打印进上下文，只回一行统计。

用法（容器内跑）:
  docker compose exec -T api python3 tools/backfill_daily_increment.py --days 1 --write
  docker compose exec -T api python3 tools/backfill_daily_increment.py --since 2026-10-01 --until 2026-10-10 --write
"""
import argparse
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
from app import db  # noqa: E402

CH_URL = "https://play.clickhouse.com/?user=explorer&default_format=TSV"
UA = "githubstar-daily/1.0"
CHUNK = 1000


def ch(sql, timeout=300, retries=4):
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


def fetch_range(a, b):
    """一次请求：区间内全库 (repo_name, day, count)。"""
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
        try:
            out.setdefault(p[1], {})[p[0]] = int(p[2])
        except ValueError:
            continue
    return out


def run(since="", until="", days=1, write=True):
    """补 [since,until) 或最近 days 天的全库 star 增量。返回统计 dict。

    scheduler 每日调用 run(days=1, write=True)；也可手动指定区间。
    """
    today = datetime.now(timezone.utc).date()
    if since and until:
        start = datetime.strptime(since, "%Y-%m-%d").date()
        stop = datetime.strptime(until, "%Y-%m-%d").date()
    else:
        stop = today
        start = today - timedelta(days=days)
    if start >= stop:
        return {"rows": 0, "days": 0, "slices": 0, "note": "empty range"}

    slices, d = [], start
    while d < stop:
        nxt = min(d + timedelta(days=3), stop)
        slices.append((d.isoformat(), nxt.isoformat()))
        d = nxt

    rows_all = {}
    for a, b in slices:
        try:
            part = fetch_range(a, b)
        except Exception:  # noqa: BLE001
            continue
        for day, m in part.items():
            rows_all.setdefault(day, {}).update(m)
        time.sleep(1)

    names = {n.lower() for m in rows_all.values() for n in m}
    if not names:
        return {"rows": 0, "days": 0, "slices": len(slices), "note": "no data"}
    repo_rows = db.query_all(
        "SELECT id, full_name, stars FROM gh_repo WHERE lower(full_name) = ANY(%s)",
        (list(names),))
    by_name = {r["full_name"].lower(): r for r in repo_rows}

    days_list = sorted(rows_all)
    if write:
        db.execute("DELETE FROM gh_repo_star_daily WHERE stat_date >= %s AND stat_date <= %s",
                   (days_list[0], days_list[-1]))
    n_rows = 0
    for day in days_list:
        batch = []
        for name, cnt in rows_all[day].items():
            r = by_name.get(name.lower())
            if not r:
                continue
            batch.append((r["id"], day, r["stars"] or 0, cnt))
        if write and batch:
            for i in range(0, len(batch), CHUNK):
                ck = batch[i:i + CHUNK]
                db.execute(
                    "INSERT INTO gh_repo_star_daily "
                    "(repo_id, stat_date, stars, delta_stars, source) VALUES "
                    + ",".join(["(%s,%s,%s,%s,'clickhouse_gh_events')"] * len(ck)),
                    [x for r in ck for x in r])
        n_rows += len(batch)
    return {"rows": n_rows, "days": len(days_list), "slices": len(slices),
            "start": days_list[0], "end": days_list[-1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, help="补最近 N 天（默认 1 = 昨天）")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    res = run(args.since, args.until, args.days, args.write)
    print("完成: %s | %s | %.0fs"
          % (res, "已写库" if args.write else "只读试跑", time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
