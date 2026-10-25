#!/usr/bin/env python3
"""用 ClickHouse 公开数据集补齐缺失周的 star 历史。

数据源: https://play.clickhouse.com/  (只读账号 explorer:)
        表 github_events —— 11 亿+ 条公开事件, WatchEvent 即 star
        覆盖 2011-02-12 至今, 可按 repo_name + 周任意聚合

产出: gh_repo_star_weekly (weekly_new)  +  gh_stat_period (gain_sum / repo_count)

用法:
    python3 backfill_weekly_ch.py --probe 2000          # 小样本探测命中率与口径
    python3 backfill_weekly_ch.py --from 2026-05-18 --to 2026-07-06 --all
"""
import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CH_URL = "https://play.clickhouse.com/"
CH_AUTH = "Basic " + base64.b64encode(b"explorer:").decode()
BATCH = 400          # 每批仓库数
WORKERS = 6          # 并发批次数(playground 是公共资源, 别太猛)


# ---------------------------------------------------------------- ClickHouse
def ch_query(sql, timeout=240, retries=3):
    """POST body 传 SQL(避免 IN 列表把 URL 撑爆)。"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                CH_URL, data=sql.encode("utf-8"),
                headers={"Authorization": CH_AUTH, "Content-Type": "text/plain"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"ClickHouse 查询失败: {last}")


def ch_weekly(names, d_from, d_to):
    """查一批仓库在 [d_from, d_to) 内按 ISO 周(周一起)的 star 数。"""
    inlist = ",".join("'%s'" % n.replace("'", "''") for n in names)
    # 注意: toStartOfWeek 第二参数 1 = 周一, 必须与项目 _to_weeks() 的 ISO 周对齐
    sql = (
        "SELECT repo_name, toStartOfWeek(created_at, 1) AS wk, count() AS stars\n"
        "FROM github_events\n"
        "WHERE event_type = 'WatchEvent'\n"
        f"  AND repo_name IN ({inlist})\n"
        f"  AND created_at >= '{d_from}' AND created_at < '{d_to}'\n"
        "GROUP BY repo_name, wk\n"
        "ORDER BY repo_name, wk\n"
        "FORMAT TSV\n"
    )
    out = ch_query(sql)
    rows = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append({"full_name": parts[0], "week_start": parts[1],
                         "weekly_new": int(parts[2])})
    return rows


def ch_weekly_total(d_from, d_to):
    """区间内全站 star 总量(仅用于对账)。"""
    sql = ("SELECT count() FROM github_events WHERE event_type='WatchEvent'\n"
           f"  AND created_at >= '{d_from}' AND created_at < '{d_to}'\nFORMAT TSV\n")
    return int(ch_query(sql).strip() or 0)


# ------------------------------------------------------------------- Postgres
def psql(sql, quiet=True):
    cmd = ["docker", "exec", "-i", "ghradar-db", "psql", "-U", "postgres",
           "-d", "github", "-tAF", "\t"]
    if quiet:
        cmd.append("-q")
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql 失败: {r.stderr[:400]}")
    return r.stdout


def load_repos(min_stars=50, limit=None):
    sql = ("SELECT id, full_name, stars FROM gh_repo "
           f"WHERE stars >= {int(min_stars)} AND full_name LIKE '%/%' "
           "ORDER BY stars DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    sql += ";"
    rows = []
    for line in psql(sql).strip().split("\n"):
        if not line.strip():
            continue
        p = line.split("\t")
        if len(p) >= 3:
            rows.append({"id": int(p[0]), "full_name": p[1], "stars": int(p[2])})
    return rows


# ------------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2026-05-18",
                    help="ISO 周起始(周一), 含")
    ap.add_argument("--to", dest="d_to", default="2026-07-06",
                    help="ISO 周起始(周一), 不含")
    ap.add_argument("--min-stars", type=int, default=50)
    ap.add_argument("--limit", type=int, default=None, help="只取前 N 个仓库(探测用)")
    ap.add_argument("--probe", type=int, default=0, help="小样本探测模式")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--write", action="store_true", help="真正写库(默认只报告)")
    ap.add_argument("--out", default="/tmp/ch_weekly_rows.jsonl")
    args = ap.parse_args()

    limit = args.probe or args.limit
    repos = load_repos(args.min_stars, limit)
    print(f"== 仓库 {len(repos)} 个 (min_stars={args.min_stars}, limit={limit})")
    print(f"== 目标区间 {args.d_from} ~ {args.d_to} (ISO 周, 周一起)")

    batches = [repos[i:i + args.batch] for i in range(0, len(repos), args.batch)]
    names_by_batch = [[r["full_name"] for r in b] for b in batches]
    print(f"== 分批 {len(batches)} 批 (每批 {args.batch}, 并发 {args.workers})")

    t0 = time.time()
    rows, done, failed = [], 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(ch_weekly, nb, args.d_from, args.d_to): i
                for i, nb in enumerate(names_by_batch)}
        for fu in as_completed(futs):
            try:
                rows.extend(fu.result())
                done += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"   !! 批次失败: {e}")
            if done % 5 == 0 or done == len(batches):
                print(f"   ...{done}/{len(batches)} 批, 命中行 {len(rows)}, "
                      f"{time.time() - t0:.1f}s")
    elapsed = time.time() - t0

    hit_names = {r["full_name"] for r in rows}
    weeks = sorted({r["week_start"] for r in rows})
    print(f"\n== 完成: {len(rows)} 行 / {len(hit_names)} 个仓库有数据 / "
          f"失败 {failed} 批 / {elapsed:.1f}s")
    print(f"== 命中率: {len(hit_names)}/{len(repos)} = "
          f"{100.0 * len(hit_names) / max(1, len(repos)):.1f}%")
    print(f"== 覆盖周: {weeks}")

    if rows:
        with open(args.out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"== 明细已写: {args.out}")

        # 试跑只报告, 不写库
        if not args.write:
            print("\n== [只读模式] 未写库。加 --write 才会落库。")
            return

        print("\n== 开始写库 ...")


if __name__ == "__main__":
    sys.exit(main())
