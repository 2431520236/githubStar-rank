#!/usr/bin/env python3
"""每日 star 汇总入库（每天前 N 仓）—— 用户 2026-10 定：要日汇总 + 周汇总，不要逐条。

要点：
  - 按月切片，每片 1 次请求（全年 12 次，而逐日要 283 次）
  - SQL: GROUP BY repo_name, toDate(created_at) ORDER BY c DESC LIMIT N BY d
  - 外部仓自动补 gh_repo（full_name/owner/name，id 走序列）
  - 幂等：ON CONFLICT (repo_id, stat_date) DO UPDATE

用法（容器内跑）:
  docker compose exec -T api python3 tools/import_top_daily.py --per-day-top 3000 --since 2026-01-01 --write
"""
import argparse
import sys
import time
import urllib.request
from datetime import date, datetime, timezone

sys.path.insert(0, "/app")
from app import db  # noqa: E402

CH_URL = "https://play.clickhouse.com/?user=explorer&default_format=TSV"
UA = "githubstar-topdaily/1.0"
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


def month_slices(since, until):
    d = datetime.strptime(since, "%Y-%m-%d").date().replace(day=1)
    stop = datetime.strptime(until, "%Y-%m-%d").date()
    out = []
    while d < stop:
        nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
        a = max(d, datetime.strptime(since, "%Y-%m-%d").date())
        b = min(nxt, stop)
        if a < b:
            out.append((a.isoformat(), b.isoformat()))
        d = nxt
    return out


def fetch_month(a, b, n):
    sql = (
        "SELECT repo_name, toDate(created_at) d, count() c FROM github_events "
        "WHERE event_type='WatchEvent' AND created_at >= '%s 00:00:00' "
        "AND created_at < '%s 00:00:00' "
        "GROUP BY repo_name, d ORDER BY c DESC LIMIT %d BY d FORMAT TSV"
        % (a, b, int(n))
    )
    out = {}
    for line in ch(sql, timeout=600).splitlines():
        p = line.split("\t")
        if len(p) < 3:
            continue
        try:
            out.setdefault(p[0], {})[p[1]] = int(p[2])
        except ValueError:
            continue
    return out


def map_existing(names_lower):
    m = {}
    for i in range(0, len(names_lower), 5000):
        ck = names_lower[i:i + 5000]
        rows = db.query_all(
            "SELECT id, lower(full_name) fn FROM gh_repo WHERE lower(full_name) = ANY(%s)", (ck,))
        for r in rows:
            m[r["fn"]] = r["id"]
    return m


def ensure_repos(names):
    lower_all = sorted({n.lower() for n in names})
    idmap = map_existing(lower_all)
    origin = {}
    for n in names:
        origin.setdefault(n.lower(), n)
    missing = [k for k in lower_all if k not in idmap]
    if missing:
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
        idmap.update(map_existing(missing))
    return idmap


def flush(rows):
    n = 0
    for i in range(0, len(rows), CHUNK):
        ck = rows[i:i + CHUNK]
        db.execute(
            "INSERT INTO gh_repo_star_daily (repo_id, stat_date, delta_stars, source) VALUES "
            + ",".join(["(%s,%s,%s,'clickhouse_topn_daily')"] * len(ck))
            + " ON CONFLICT (repo_id, stat_date) DO UPDATE SET "
            "delta_stars=EXCLUDED.delta_stars, source=EXCLUDED.source",
            [x for r in ck for x in r])
        n += len(ck)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-day-top", type=int, default=3000)
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--until", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    slices = month_slices(args.since, args.until)
    print("区间 %s ~ %s | 每天前 %d | %d 个月片 | %s"
          % (args.since, args.until, args.per_day_top, len(slices),
             "写库" if args.write else "只读试跑"), flush=True)

    t0 = time.time()
    total_rows = written = 0
    failed = []
    for i, (a, b) in enumerate(slices, 1):
        try:
            data = fetch_month(a, b, args.per_day_top)
        except Exception as e:  # noqa: BLE001
            failed.append((a, b, str(e)[:80]))
            print("  片 %d/%d %s~%s 失败: %s" % (i, len(slices), a, b, str(e)[:80]), flush=True)
            continue
        rows = []
        for name, days in data.items():
            for ds, c in days.items():
                rows.append((name, ds, c))
        print("  片 %d/%d %s~%s | 仓 %d | 日记录 %d | %.0fs"
              % (i, len(slices), a, b, len(data), len(rows), time.time() - t0), flush=True)
        if not args.write:
            continue
        idmap = ensure_repos(list(data.keys()))
        out = [(idmap[name.lower()], ds, c) for name, ds, c in rows if name.lower() in idmap]
        written += flush(out)
        total_rows += len(out)
        time.sleep(1)

    print("完成: 片 %d/%d | 日记录 %d | 写入 %d | 失败片 %d | 总耗时 %.0fs"
          % (len(slices) - len(failed), len(slices), total_rows, written,
             len(failed), time.time() - t0), flush=True)
    for a, b, e in failed:
        print("  失败: %s~%s %s" % (a, b, e), flush=True)


if __name__ == "__main__":
    main()
