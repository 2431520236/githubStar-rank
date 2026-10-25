"""重算全部派生数据：滚动增量 → 榜单快照 → 周期汇总。

**改了事实表（周表/日表）之后必须跑这个**，否则榜单读到的还是旧值：
`star_gain_*` 由 rollup 从事实表算，`gh_rank_snapshot` / `gh_stat_period` 由
snapshot_service 从榜单口径算 —— 三层都是派生数据，不会自己跟着事实表变。

用法（容器内）：
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/recompute_ranks.py
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/recompute_ranks.py --skip-rollup
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/recompute_ranks.py --only-stat-period
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.getenv("APP_DIR", "/app"))

from app import db  # noqa: E402
from app.services import facts_service, snapshot_service  # noqa: E402


def dump(tag: str, payload) -> None:
    print("[%s] %s" % (tag, json.dumps(payload, default=str, ensure_ascii=False)), flush=True)


def rebuild_stat_period() -> dict:
    """从事实表直接重算 gh_stat_period 的周/月 gain_sum。

    快照路径只覆盖「榜单算过的周期」，而榜单受统一门槛（star≥50）限制，
    池内长尾仓的增量进不了 gain_sum。这里按事实表全量重算，口径与柱图一致。
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO gh_stat_period
                    (period_type, period_key, period_label, period_start, period_end,
                     is_complete, is_locked, repo_count, gain_sum, computed_at)
                SELECT 'week',
                       to_char(w.week_start, 'IYYY-"W"IW'),
                       to_char(w.week_start, 'IYYY-"W"IW'),
                       w.week_start,
                       LEAST((w.week_start + 6), CURRENT_DATE),
                       (w.week_start + 6) < CURRENT_DATE,
                       FALSE,
                       count(DISTINCT w.repo_id),
                       COALESCE(sum(w.weekly_new), 0),
                       now()
                  FROM gh_repo_star_weekly w
                  JOIN gh_repo r ON r.id = w.repo_id
                 WHERE COALESCE(r.in_history_pool, FALSE) = TRUE
                 GROUP BY w.week_start
                ON CONFLICT (period_type, period_key) DO UPDATE SET
                    repo_count = EXCLUDED.repo_count,
                    gain_sum = EXCLUDED.gain_sum,
                    period_end = EXCLUDED.period_end,
                    is_complete = EXCLUDED.is_complete,
                    computed_at = now()
            """)
            wk = cur.rowcount
        conn.commit()
    return {"week_rows": wk}


def purge_stale_gain_snapshots() -> dict:
    """删除全部 `gain` 快照 —— 它们是治理前用旧口径（含池外仓）算出来的。

    完整周期的快照带 `is_final=TRUE`，`compute_rank` 会跳过重算，所以**必须先删再算**，
    否则历史周/月榜会一直显示含池外仓的旧数据（实测周 gain 快照里 17,887 行是池外仓）。
    `total` 快照不动：总榜本就是全量口径，含池外仓是正确的。
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gh_rank_snapshot WHERE metric = 'gain'")
            n = cur.rowcount
        conn.commit()
    return {"deleted_gain_snapshots": n}


def compute_current_periods() -> dict:
    """补算「当前」日/周/月三个 gain 周期（compute_all_ranks 不覆盖 day）。"""
    from app.services import rank_service
    out = {}
    for pt in ("day", "week", "month"):
        start, _end = rank_service.period_bounds(pt, None)
        key = snapshot_service._key_from_bounds(pt, start)
        try:
            r = snapshot_service.compute_rank(pt, key, "gain", force=True)
            out[pt] = r.get("status")
        except Exception as e:  # noqa: BLE001
            out[pt] = "failed: %s" % e
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-rollup", action="store_true")
    ap.add_argument("--skip-ranks", action="store_true")
    ap.add_argument("--only-stat-period", action="store_true")
    ap.add_argument("--purge-stale", action="store_true",
                    help="先删掉全部 gain 快照再重算（治理事实表后必须加）")
    args = ap.parse_args()

    t0 = time.time()
    if args.only_stat_period:
        dump("stat_period", rebuild_stat_period())
        print("[done] %.1fs" % (time.time() - t0), flush=True)
        return

    if args.purge_stale:
        dump("purge_stale", purge_stale_gain_snapshots())

    if not args.skip_rollup:
        dump("rollup", facts_service.rollup_gains())
        print("[rollup] %.1fs" % (time.time() - t0), flush=True)

    if not args.skip_ranks:
        dump("ranks", snapshot_service.compute_all_ranks())
        dump("current", compute_current_periods())

    dump("stat_period", rebuild_stat_period())
    print("[done] %.1fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
