"""一次性修复脚本(2026-10): 清脏行后重算聚合。用完即删。

1) 重算 gh_stat_period(日/周/月 × total/gain) —— 原 W42 gain_sum 被假增量污染成 8,265 万
2) 重跑 rollup_gains() —— 回填 star_gain_1d/7d/30d/60d 与 daily_velocity
"""
import sys
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.services import facts_service, snapshot_service  # noqa: E402

print("[1/2] 重算 gh_stat_period ...", flush=True)
r = snapshot_service.compute_all_ranks()
print("[1/2] done:", r, flush=True)

print("[2/2] 重跑 rollup_gains ...", flush=True)
r2 = facts_service.rollup_gains()
print("[2/2] done:", r2, flush=True)