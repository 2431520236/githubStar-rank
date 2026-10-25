"""清库重拉 star 历史 —— 数据源: GitHub 官方 /repos/{owner}/{repo}/stargazers/history

接口局限(实测):
  * 一次只能查一个仓;
  * 每页固定 30 周(≈7 个月)且忽略 per_page, 更早历史须跟 Link rel="next" 翻页;
  * 响应每项 = {"week": 周日起点 unix 秒, "total": 该周新增, "days": [7 个整数]}。
所以策略 = 逐仓拉、每仓 1 页(最新 30 周), 用 token 额度(5000/小时)换覆盖面。

候选池(兼顾总量代表 + 近期热门, 用户口径「每周前 2000」):
  A = stars 倒序前 --pool-stars
  B = GREATEST(star_gain_7d, star_gain_30d) 倒序前 --pool-gain
  并集去重后逐仓采集。

用法(容器内):
  python3 tools/rebuild_star_history.py --truncate --pool-stars 2000 --pool-gain 500 --concurrency 12
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.getenv("APP_DIR", "/app"))

from app import db  # noqa: E402
from app.collectors import history_collector  # noqa: E402


def pick_pool(top_stars: int, top_gain: int) -> list[dict]:
    """候选池 = **历史池**（唯一口径来源 `pool_service`）。

    用户 2026-10 口径：「周历史按照排名前 2000 的仓库」—— 池的判定只能有一处。
    参数 `top_stars` / `top_gain` 仅为向后兼容保留（旧调用点仍传），**不再参与筛选**；
    要改池规模请改 `GH_HISTORY_POOL_TOP_N` 并重跑 `tools/rebuild_pool.py`。
    """
    from app.services import pool_service
    return db.query_all(
        "SELECT id, full_name, stars FROM gh_repo WHERE " + pool_service.pool_where("") +
        " ORDER BY stars DESC NULLS LAST")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truncate", action="store_true", help="先清空 weekly/daily 两表")
    ap.add_argument("--pool-stars", type=int, default=2000)
    ap.add_argument("--pool-gain", type=int, default=500)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="0=全量候选(调试可用小值)")
    ap.add_argument("--no-collect", action="store_true", help="只清库+算候选, 不采集")
    args = ap.parse_args()

    if args.truncate:
        db.execute("TRUNCATE gh_repo_star_weekly, gh_repo_star_daily")
        print("[truncate] gh_repo_star_weekly / gh_repo_star_daily 已清空", flush=True)

    pool = pick_pool(args.pool_stars, args.pool_gain)
    if args.limit:
        pool = pool[: args.limit]
    print("[pool] stars前%d ∪ gain前%d → 去重 %d 仓"
          % (args.pool_stars, args.pool_gain, len(pool)), flush=True)
    if args.no_collect:
        return

    ids = [r["id"] for r in pool]
    t0 = time.time()
    res = history_collector.collect_history(
        limit=len(ids), concurrency=args.concurrency,
        refresh_after_days=0, only_missing=False, ids=ids)
    print("[collect] ok=%s failed=%s total=%s 用时=%.0fs"
          % (res.get("ok"), res.get("failed"), res.get("total"), time.time() - t0), flush=True)
    print("[rate] %s" % json.dumps(res.get("rate") or {}, ensure_ascii=False), flush=True)
    if res.get("errors"):
        print("[errors] %s" % json.dumps(res["errors"][:5], ensure_ascii=False), flush=True)

    w = db.query_all(
        "SELECT count(*) AS rows, count(DISTINCT repo_id) AS repos, "
        "count(DISTINCT week_start) AS weeks, min(week_start) AS wmin, max(week_start) AS wmax "
        "FROM gh_repo_star_weekly")
    print("[weekly] %s" % json.dumps(w[0], default=str, ensure_ascii=False), flush=True)
    d = db.query_all(
        "SELECT count(*) AS rows, count(DISTINCT repo_id) AS repos FROM gh_repo_star_daily")
    print("[daily] %s" % json.dumps(d[0], default=str, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
