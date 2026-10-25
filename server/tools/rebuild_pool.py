"""历史池 CLI —— 重建 / 查看 / 清理 / 核对。

用户口径（2026-10，逐字）：
    「周历史按照排名前 2000 的仓库，总榜可以所有，日榜也必须前两千」
    「必须先统一代码逻辑和业务逻辑，确保代码准确再去治理数据」

**所有池逻辑的唯一来源是 `app/services/pool_service.py`** —— 本脚本只是它的命令行外壳，
不重复实现任何口径（曾经在脚本里又写了一份 purge/verify，导致两处逻辑漂移，已删除）。

三步（默认只做第一步，安全且幂等）：
  1. 无参数            按 stars 重算 gh_repo.in_history_pool / pool_rank
  2. `--purge-outside` 删掉池外仓的周/日历史行并把其滚动增量清零（不可逆，先备份）
  3. `--verify`        核对：池规模、池外残留、唯一性、改名仓重复、github_id 覆盖

采集用 `tools/rebuild_star_history.py`（清库重拉）或 `tools/backfill_pool.py`（续采），
两者都走池内口径；github_id 回填用 `tools/backfill_github_id.py`。

用法（容器内）：
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/rebuild_pool.py
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/rebuild_pool.py --purge-outside
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/rebuild_pool.py --verify
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.getenv("APP_DIR", "/app"))

from app.services import pool_service  # noqa: E402


def dump(tag: str, payload) -> None:
    print("[%s] %s" % (tag, json.dumps(payload, default=str, ensure_ascii=False)), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=0, help="0 = 用 config 的 HISTORY_POOL_TOP_N")
    ap.add_argument("--purge-outside", action="store_true",
                    help="删除池外仓的历史行并清零增量（不可逆）")
    ap.add_argument("--verify", action="store_true", help="只核对")
    args = ap.parse_args()

    if args.verify:
        dump("verify", pool_service.verify())
        return

    dump("before", pool_service.pool_stats())
    dump("rebuild", pool_service.rebuild_pool(args.top_n or None))
    if args.purge_outside:
        dump("purge", pool_service.purge_outside_pool())
    dump("after", pool_service.pool_stats())
    dump("verify", pool_service.verify())


if __name__ == "__main__":
    main()
