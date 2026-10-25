"""历史池服务 —— star 历史与 gain 榜口径的**唯一来源**（2026-10 用户重定）。

用户口径（逐字）：
    「周历史按照排名前 2000 的仓库，总榜可以所有，日榜也必须前两千」

因此本模块是全项目「哪些仓才有 star 历史」这件事的唯一判定处：

* **历史池** = `gh_repo` 按 `stars DESC` 排名前 `cfg.HISTORY_POOL_TOP_N`（默认 2000）。
* `gh_repo_star_weekly` / `gh_repo_star_daily` **只允许写池内仓**。
* 日/周/月榜的 `metric=gain`（事实与滚动两种口径）**只允许在池内取数**。
* **总榜 `metric=total` 不受池限制**（全量按 `stars` 排序，沿用统一门槛）。

任何「按增量取数」的新代码都必须调用 `pool_where()` 之外，还要保证不会把
非池内仓的 `star_gain_*` 算进榜单 —— 这些滚动字段只由 `rollup` 从
「已有周历史的仓」算出，原则上天然只在池内，但不能依赖这一点。

改动池规模后必须重建：`python3 tools/rebuild_pool.py --rebuild-only`。
"""
from typing import Optional

from .. import config as cfg
from .. import db
from ..utils import as_int


def pool_top_n() -> int:
    return max(1, as_int(cfg.HISTORY_POOL_TOP_N, 2000))


def pool_where(alias: str = "r") -> str:
    """池内过滤的 SQL 片段（唯一写法，禁止在别处手写）。"""
    p = f"{alias}." if alias else ""
    return f"COALESCE({p}in_history_pool, FALSE) = TRUE"


def pool_rank_col(alias: str = "r") -> str:
    p = f"{alias}." if alias else ""
    return f"{p}pool_rank"


def rebuild_pool(top_n: Optional[int] = None) -> dict:
    """重算历史池标记与名次。

    排名依据**只有** `stars DESC`（用户口径「排名前 2000」）。`stars` 为 0 的仓
    （详情未回填）不参与排名 —— 它们本就排不进前 N。

    实现为单条 UPDATE，天然幂等：池内标记 + 名次一起刷，落到池外的清标记。
    """
    n = max(1, as_int(top_n, pool_top_n()) or pool_top_n())
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # 要点：必须**扫到每一个候选行**再逐行定值 —— 只 JOIN 池内行的写法
            # 更新不到「上一轮在池内、这一轮掉出」的行，会残留 in_history_pool=TRUE。
            cur.execute("""
                WITH ranked AS (
                    SELECT id, row_number() OVER (ORDER BY stars DESC NULLS LAST, id ASC) AS rk
                    FROM gh_repo WHERE COALESCE(stars, 0) > 0
                )
                UPDATE gh_repo r
                   SET in_history_pool = (k.rk <= %s),
                       pool_rank = CASE WHEN k.rk <= %s THEN k.rk ELSE NULL END
                  FROM ranked k
                 WHERE r.id = k.id
            """, [n, n])
            marked = cur.rowcount
            # stars=0 的仓（详情未回填）不参与排名，一并清标记
            cur.execute(
                "UPDATE gh_repo SET in_history_pool = FALSE, pool_rank = NULL "
                "WHERE COALESCE(in_history_pool, FALSE) = TRUE "
                "  AND COALESCE(stars, 0) = 0")
            cleared = cur.rowcount
        conn.commit()
    return {"pool_top_n": n, "scanned": marked, "cleared": cleared}


def pool_stats() -> dict:
    """池现状：规模、覆盖（有周历史 / 有日历史）、star 门槛。"""
    n = pool_top_n()
    row = db.query_one("""
        SELECT
          count(*) FILTER (WHERE COALESCE(in_history_pool, FALSE)) AS pool_size,
          min(stars) FILTER (WHERE COALESCE(in_history_pool, FALSE)) AS min_stars,
          max(stars) FILTER (WHERE COALESCE(in_history_pool, FALSE)) AS max_stars,
          count(*) FILTER (WHERE COALESCE(in_history_pool, FALSE)
                             AND history_synced_at IS NOT NULL) AS pool_synced,
          count(*) FILTER (WHERE COALESCE(in_history_pool, FALSE)
                             AND EXISTS (SELECT 1 FROM gh_repo_star_weekly w
                                          WHERE w.repo_id = gh_repo.id)) AS pool_with_weekly
        FROM gh_repo
    """) or {}
    out = dict(row)
    out["pool_top_n"] = n
    out["daily_repos"] = db.query_scalar(
        "SELECT count(DISTINCT repo_id) FROM gh_repo_star_daily")
    out["weekly_repos"] = db.query_scalar(
        "SELECT count(DISTINCT repo_id) FROM gh_repo_star_weekly")
    # 池外残留的历史行（治理目标：应为 0）
    out["weekly_outside_pool"] = db.query_scalar(
        "SELECT count(*) FROM gh_repo_star_weekly w JOIN gh_repo r ON r.id = w.repo_id "
        "WHERE " + pool_where("r").replace("= TRUE", "IS NOT TRUE")) or 0
    out["daily_outside_pool"] = db.query_scalar(
        "SELECT count(*) FROM gh_repo_star_daily d JOIN gh_repo r ON r.id = d.repo_id "
        "WHERE " + pool_where("r").replace("= TRUE", "IS NOT TRUE")) or 0
    return out


def purge_outside_pool() -> dict:
    """删除池外仓的 star 历史行，并把它们的滚动增量清零。

    不删 `gh_repo` 行本身（基础信息 + 总榜要全量保留），只清历史事实行与增量字段 ——
    否则池外仓会留着「没有历史却有增量」的矛盾状态，被榜单回退口径读出来。
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM gh_repo_star_weekly w USING gh_repo r "
                "WHERE r.id = w.repo_id AND COALESCE(r.in_history_pool, FALSE) = FALSE")
            wk = cur.rowcount
            cur.execute(
                "DELETE FROM gh_repo_star_daily d USING gh_repo r "
                "WHERE r.id = d.repo_id AND COALESCE(r.in_history_pool, FALSE) = FALSE")
            dy = cur.rowcount
            cur.execute(
                "UPDATE gh_repo SET star_gain_1d = 0, star_gain_7d = 0, star_gain_30d = 0, "
                "star_gain_60d = 0, daily_velocity = 0 "
                "WHERE COALESCE(in_history_pool, FALSE) = FALSE "
                "  AND (COALESCE(star_gain_1d,0) <> 0 OR COALESCE(star_gain_7d,0) <> 0 "
                "       OR COALESCE(star_gain_30d,0) <> 0 OR COALESCE(star_gain_60d,0) <> 0)")
            zeroed = cur.rowcount
        conn.commit()
    return {"deleted_weekly": wk, "deleted_daily": dy, "zeroed_repos": zeroed}


def verify() -> dict:
    """核对池口径是否落实到位（治理后必跑）。"""
    st = pool_stats()
    out = dict(st)
    out["pool_size_matches_target"] = (st.get("pool_size") == st.get("pool_top_n"))
    out["pool_missing_sync"] = db.query_scalar(
        "SELECT count(*) FROM gh_repo WHERE " + pool_where("") +
        " AND history_synced_at IS NULL") or 0
    out["weekly_dup"] = db.query_scalar(
        "SELECT count(*) FROM (SELECT repo_id, week_start FROM gh_repo_star_weekly "
        "GROUP BY 1,2 HAVING count(*) > 1) t") or 0
    out["daily_dup"] = db.query_scalar(
        "SELECT count(*) FROM (SELECT repo_id, stat_date FROM gh_repo_star_daily "
        "GROUP BY 1,2 HAVING count(*) > 1) t") or 0
    # 改名仓重复（指纹法，判据同 references/star-ingest-integrity.md 第 4 节）
    out["rename_dup_groups"] = db.query_scalar("""
        WITH fp AS (
          SELECT repo_id,
                 md5(string_agg(week_start::text || ':' || weekly_new::text, ','
                                ORDER BY week_start)) AS f,
                 count(*) AS wk, sum(weekly_new) AS wn
          FROM gh_repo_star_weekly GROUP BY repo_id
        )
        SELECT count(*) FROM (
          SELECT f FROM fp GROUP BY f
          HAVING count(*) > 1 AND max(wk) >= 5 AND max(wn) >= 500) t
    """) or 0
    out["pool_id_missing"] = db.query_scalar(
        "SELECT count(*) FROM gh_repo WHERE " + pool_where("") +
        " AND github_id IS NULL") or 0
    return out


def pool_id_set() -> set:
    """池内 repo_id 集合（Python 侧用，供脚本过滤）。"""
    rows = db.query_all("SELECT id FROM gh_repo WHERE " + pool_where(""), [])
    return {r["id"] for r in rows}
