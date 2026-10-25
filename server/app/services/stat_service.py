"""大屏统计服务:overview / 趋势 / 分布 / 体量 / 最新发布。

对应手册《04 · 后端服务层逻辑》5. stat_service。
"""
from typing import Optional
from datetime import date, timedelta

from .. import config as cfg
from .. import db
from ..utils import as_int
from . import pool_service


def _meaningful() -> str:
    """全站统一口径: 有意义项目 = star ≥ 50 且 3 年内有更新(用户 2026-10 定)。

    总榜筛选 / 核心指标 / AI 概要提取三处共用同一条件。原来 overview 用 count(*)
    把 72 万条零 star、多年停更的仓也算进分母, 导致「事实库覆盖 2%」这种虚低数字
    (真实口径下是 25.9%)。
    """
    return ("stars >= %d AND COALESCE(repo_pushed_at, repo_updated_at, repo_created_at, "
            "first_seen_at) >= now() - interval '%d days'"
            % (cfg.DEFAULT_MIN_STARS, cfg.DEFAULT_MAX_AGE_DAYS))


def overview() -> dict:
    M = _meaningful()
    row = db.query_one(f"""
        SELECT
          (SELECT count(*) FROM gh_repo WHERE {M}) AS repos,
          (SELECT COALESCE(sum(stars),0) FROM gh_repo WHERE {M}) AS stars,
          (SELECT COALESCE(sum(forks),0) FROM gh_repo WHERE {M}) AS forks,
          (SELECT count(*) FROM gh_repo WHERE {M} AND COALESCE(ai_summary,'') <> '') AS summarized,
          (SELECT count(*) FROM gh_repo WHERE {M} AND detail_synced_at IS NOT NULL) AS detail_done,
          (SELECT count(*) FROM gh_repo WHERE {M} AND EXISTS (
              SELECT 1 FROM gh_repo_star_weekly w WHERE w.repo_id = gh_repo.id)) AS history_done,
          (SELECT count(*) FROM gh_repo WHERE {M} AND is_major) AS major_repos,
          (SELECT count(*) FROM gh_repo_star_daily) AS daily_rows,
          (SELECT count(*) FROM gh_repo_star_weekly) AS weekly_rows,
          (SELECT count(*) FROM gh_rank_snapshot) AS snapshot_rows,
          (SELECT max(stat_date) FROM gh_repo_star_daily) AS daily_to,
          (SELECT count(DISTINCT repo_id) FROM gh_repo_star_daily) AS daily_repos,
          (SELECT count(*) FROM gh_watch_event WHERE NOT is_read) AS unread_alerts,
          (SELECT COALESCE(sum(delta_stars),0) FROM gh_repo_star_daily
             WHERE stat_date = (SELECT max(stat_date) FROM gh_repo_star_daily)) AS today_gain,
          (SELECT COALESCE(sum(delta_stars),0) FROM gh_repo_star_daily
             WHERE stat_date >= %s::date) AS gain_since_start
    """, [cfg.STAT_START_DATE])
    out = dict(row or {})
    out["stat_start_date"] = cfg.STAT_START_DATE
    out["coverage"] = {
        "detail": _ratio(out.get("detail_done"), out.get("repos")),
        "history": _ratio(out.get("history_done"), out.get("repos")),
        "summary": _ratio(out.get("summarized"), out.get("repos")),
    }
    return out


def _ratio(a, b) -> float:
    a, b = as_int(a), as_int(b)
    return round(a / b, 4) if b else 0.0


def _weekly_years() -> list:
    """库里每周数据实际覆盖的年份(事实表 ∪ 周期元信息表), 降序。"""
    rows = db.query_all("""
        SELECT DISTINCT EXTRACT(YEAR FROM week_start)::int AS y
        FROM gh_repo_star_weekly
        UNION
        SELECT DISTINCT EXTRACT(YEAR FROM period_start)::int AS y
        FROM gh_stat_period WHERE period_type = 'week'
        ORDER BY y DESC
    """)
    return [r["y"] for r in rows if r.get("y")]


def weekly_trend(exclude_major: bool = False, limit: int = 60,
                 year: Optional[int] = None) -> dict:
    """每周新增趋势(大屏柱状图)。

    - `year` 缺省 = 库里最近有数据的一年(即「当前年份」);`year=0` = 全部年份。
    - 优先事实表(可精确过滤知名);事实表为空时回退周期元信息表,否则柱图恒为空。
    - 响应额外回传 `year`(实际生效年)与 `years`(可选年份列表), 供前端年份下拉。
    """
    years = _weekly_years()
    cur = int(year) if year is not None else (years[0] if years else 0)
    extra = "AND COALESCE(r.is_major, FALSE) = FALSE" if exclude_major else ""
    ycond, params = "", [cfg.STAT_START_DATE]
    if cur:
        ycond = "AND EXTRACT(YEAR FROM w.week_start) = %s"
        params.append(cur)
    params.append(as_int(limit, 60))
    pw = pool_service.pool_where("r")
    rows = db.query_all(f"""
        SELECT w.week_start, w.week_key,
               COALESCE(SUM(w.weekly_new),0) AS gain,
               count(DISTINCT w.repo_id) AS repos
        FROM gh_repo_star_weekly w JOIN gh_repo r ON r.id = w.repo_id
        WHERE w.week_start >= %s::date {extra} {ycond} AND {pw}
        GROUP BY w.week_start, w.week_key ORDER BY w.week_start DESC LIMIT %s
    """, params)
    source = "fact"
    if not rows:
        # 回退:周期元信息(源资料自带 14 周真实 gain_sum,含 gain=0 的空周期)
        y2, p2 = "", []
        if cur:
            y2 = "AND EXTRACT(YEAR FROM period_start) = %s"
            p2.append(cur)
        p2.append(as_int(limit, 60))
        rows = db.query_all("""
            SELECT period_start AS week_start, period_key AS week_key,
                   COALESCE(gain_sum,0)::bigint AS gain,
                   COALESCE(repo_count,0) AS repos
            FROM gh_stat_period
            WHERE period_type = 'week' """ + y2 + """
            ORDER BY period_start DESC LIMIT %s
        """, p2)
        source = "stat_period"
    rows = _fill_weeks(list(rows))
    return {"weeks": rows, "source": source, "year": cur, "years": years}


def _fill_weeks(rows: list) -> list:
    """把周序列补成连续(缺失周按 0 补),避免柱状图 x 轴跳空。

    源资料可能只导出部分周(如缺 W21~W27),但用户要看"所有周",
    因此以起止周为界逐周补齐,缺失周 gain/repos 记 0 并标 filled=True。
    """
    if not rows:
        return rows
    by_key = {r.get("week_key"): r for r in rows if r.get("week_key")}
    starts = sorted(r["week_start"] for r in rows if r.get("week_start"))
    if not starts:
        return rows
    cur, end, out = starts[0], starts[-1], []   # 升序输出(前端再按需处理)
    while cur <= end:
        iso = cur.isocalendar()
        key = "%d-W%02d" % (iso[0], iso[1])
        row = by_key.get(key)
        if row:
            row = dict(row)
            row["filled"] = False
        else:
            row = {"week_start": cur, "week_key": key, "gain": 0, "repos": 0, "filled": True}
        out.append(row)
        cur = cur + timedelta(days=7)
    return out


def rank_trend(period_type: str = "week", metric: str = "gain", limit: int = 30) -> dict:
    rows = db.query_all("""
        SELECT period_key, period_label, period_start, sum(gain)::bigint AS gain, count(*) AS repos
        FROM gh_rank_snapshot WHERE period_type = %s AND metric = %s
        GROUP BY period_key, period_label, period_start
        ORDER BY period_start DESC LIMIT %s
    """, [period_type, metric, as_int(limit, 30)])
    rows.reverse()
    return {"points": rows}


def lang_distribution(limit: int = 12, min_stars: Optional[int] = None) -> list[dict]:
    ms = cfg.DEFAULT_MIN_STARS if min_stars is None else as_int(min_stars)
    return db.query_all("""
        SELECT COALESCE(NULLIF(lang,''),'未标注') AS name, count(*) AS value,
               COALESCE(sum(stars),0)::bigint AS stars
        FROM gh_repo WHERE stars >= %s GROUP BY 1 ORDER BY value DESC LIMIT %s
    """, [ms, as_int(limit, 12)])


def license_distribution(limit: int = 10, min_stars: Optional[int] = None) -> list[dict]:
    ms = cfg.DEFAULT_MIN_STARS if min_stars is None else as_int(min_stars)
    return db.query_all("""
        SELECT COALESCE(NULLIF(license_spdx,''),'未标注') AS name, count(*) AS value
        FROM gh_repo WHERE stars >= %s GROUP BY 1 ORDER BY value DESC LIMIT %s
    """, [ms, as_int(limit, 10)])


def star_buckets() -> list[dict]:
    """star 体量分布。**必须带全站统一口径**(star≥50 且 3 年内更新) —— 原来统计全库,
    71 万个 0-star/停更仓全落在 <1k 桶里, 一根 71 万的柱子把其余几百的桶全压没了。
    """
    M = _meaningful()
    rows = db.query_one(f"""
        SELECT
          count(*) FILTER (WHERE stars >= 100000) AS b100k,
          count(*) FILTER (WHERE stars >= 50000 AND stars < 100000) AS b50k,
          count(*) FILTER (WHERE stars >= 20000 AND stars < 50000) AS b20k,
          count(*) FILTER (WHERE stars >= 10000 AND stars < 20000) AS b10k,
          count(*) FILTER (WHERE stars >= 5000  AND stars < 10000) AS b5k,
          count(*) FILTER (WHERE stars >= 1000  AND stars < 5000)  AS b1k,
          count(*) FILTER (WHERE stars < 1000) AS blt1k
        FROM gh_repo WHERE {M}
    """)
    order = [("≥100k", "b100k"), ("50k-100k", "b50k"), ("20k-50k", "b20k"),
             ("10k-20k", "b10k"), ("5k-10k", "b5k"), ("1k-5k", "b1k"), ("<1k", "blt1k")]
    return [{"name": n, "value": as_int((rows or {}).get(k))} for n, k in order]


def major_vs_normal(period_type: str = "week") -> list[dict]:
    """知名 vs 新兴。优先按事实表统计近期活跃;无事实数据时按主表滚动增量统计。"""
    start, end = db.query_one("SELECT CURRENT_DATE - 7 AS s, CURRENT_DATE AS e").values()
    rows = db.query_all(f"""
        SELECT CASE WHEN COALESCE(r.is_major,FALSE) THEN '知名项目' ELSE '新兴项目' END AS name,
               count(DISTINCT r.id) AS value
        FROM gh_repo r JOIN gh_repo_star_daily d ON d.repo_id = r.id
        WHERE d.stat_date BETWEEN %s AND %s AND COALESCE(r.stars,0) >= %s
          AND {pool_service.pool_where("r")}
        GROUP BY 1 ORDER BY value DESC
    """, [start, end, cfg.DEFAULT_MIN_STARS])
    if not rows:
        # 回退:用主表 7 天滚动增量(有增量的才算"本周活跃")
        rows = db.query_all("""
            SELECT CASE WHEN COALESCE(is_major,FALSE) THEN '知名项目' ELSE '新兴项目' END AS name,
                   count(*) AS value
            FROM gh_repo
            WHERE COALESCE(stars,0) >= %s AND COALESCE(star_gain_7d,0) > 0
            GROUP BY 1 ORDER BY value DESC
        """, [cfg.DEFAULT_MIN_STARS])
    return rows


def recent_releases(limit: int = 10) -> list[dict]:
    return db.query_all("""
        SELECT full_name, html_url, latest_version, version_note, latest_release_at, lang, stars
        FROM gh_repo WHERE latest_release_at IS NOT NULL
        ORDER BY latest_release_at DESC LIMIT %s
    """, [as_int(limit, 10)])


def top_by_lang(limit: int = 3, langs: int = 8, min_stars: Optional[int] = None) -> dict:
    ms = cfg.DEFAULT_MIN_STARS if min_stars is None else as_int(min_stars)
    rows = db.query_all("""
        WITH top_langs AS (
            SELECT lang FROM gh_repo WHERE lang IS NOT NULL AND lang <> '' AND stars >= %s
            GROUP BY lang ORDER BY count(*) DESC LIMIT %s
        )
        SELECT r.lang, r.full_name, r.stars, r.html_url FROM gh_repo r
        JOIN top_langs t ON t.lang = r.lang
        WHERE r.stars >= %s
        ORDER BY r.lang, r.stars DESC
    """, [ms, as_int(langs, 8), ms])
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["lang"], [])
        if len(out[r["lang"]]) < as_int(limit, 3):
            out[r["lang"]].append(r)
    return {"groups": out}


def dashboard() -> dict:
    return {
        "overview": overview(),
        "weekly_trend": weekly_trend(),
        "lang_distribution": lang_distribution(),
        "license_distribution": license_distribution(),
        "star_buckets": star_buckets(),
        "major_vs_normal": major_vs_normal(),
        "recent_releases": recent_releases(10),
    }
