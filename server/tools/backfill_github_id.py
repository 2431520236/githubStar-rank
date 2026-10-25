"""回填 gh_repo.github_id —— 业务唯一键（治「改名仓各持一份历史」的重复）。

只调 `GET /repos/{owner}/{repo}`（**1 次请求/仓**，不碰 release/contributors，省额度），
把官方返回的数字 `id` 落到 `github_id`。落库走 `repo_service.upsert_repos()`，
因此会自动触发 `_merge_renames()` 的改名归并。

默认只处理**历史池内**的仓（池外仓不进榜、不进历史，不需要该键）。

用法（容器内）：
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/backfill_github_id.py --limit 20
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/backfill_github_id.py          # 全池
"""
import argparse
import json
import os
import sys
import threading

sys.path.insert(0, os.getenv("APP_DIR", "/app"))

import urllib3  # noqa: E402

urllib3.disable_warnings()

from app import db  # noqa: E402
from app.collectors import base, github_client as gc  # noqa: E402
from app.services import pool_service, repo_service  # noqa: E402


def candidates(limit: int = 0, all_repos: bool = False) -> list[dict]:
    where = "" if all_repos else (" AND " + pool_service.pool_where(""))
    sql = ("SELECT id, full_name FROM gh_repo WHERE github_id IS NULL" + where +
           " ORDER BY stars DESC NULLS LAST LIMIT %s")
    return db.query_all(sql, [limit if limit else 10_000_000])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = 全部候选")
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--all", action="store_true", help="连池外仓一起回填(慢,通常不需要)")
    args = ap.parse_args()

    rows = candidates(args.limit, args.all)
    names = [r["full_name"] for r in rows]
    print("[candidates] %d 仓 (池内 github_id IS NULL)" % len(names), flush=True)
    if not names:
        return

    cli = gc.get_client()
    stop = threading.Event()
    stats = {"ok": 0, "reports": {"renamed": 0, "merged": 0}}

    def work(fn: str) -> dict:
        if gc.RATE.exhausted():
            stop.set()
            raise RuntimeError("额度已耗尽,停止")
        g = cli.repo(fn)
        if not g:
            raise RuntimeError("仓库不存在或无权限: %s" % fn)
        row = gc.GitHubClient.normalize(g)
        # 关键：把**请求时用的名字**带进去。库内这行 github_id 还是 NULL，
        # 只能靠「旧名 → 官方返回的新名」识别改名，否则 upsert 会按新名插一行新的，
        # 旧行永远拿不到 github_id（实测 18 个池内仓卡在这里）。
        row["_prev_full_name"] = fn
        repo_service.upsert_repos([row])       # 内含 _merge_renames 改名归并
        stats["ok"] += 1
        return {"full_name": fn, "github_id": row.get("github_id")}

    out = base.run_threaded(names, work, concurrency=args.concurrency, stop_flag=stop)
    print("[done] ok=%s failed=%s skipped=%s total=%s 用时见下"
          % (out.get("ok"), out.get("failed"), out.get("skipped"), out.get("total")), flush=True)
    if out.get("errors"):
        print("[errors] %s" % json.dumps(out["errors"][:5], ensure_ascii=False), flush=True)
    print("[rate] %s" % json.dumps(gc.RATE.snapshot(), ensure_ascii=False), flush=True)
    left = db.query_scalar(
        "SELECT count(*) FROM gh_repo WHERE github_id IS NULL AND " + pool_service.pool_where(""))
    print("[left] 池内仍无 github_id: %s" % left, flush=True)
    print("[pool] %s" % json.dumps(pool_service.pool_stats(), default=str, ensure_ascii=False),
          flush=True)


if __name__ == "__main__":
    main()
