"""榜单服务:实时榜单口径(total/gain/gain_index)、周期概览、白名单 CRUD。

对应手册《04 · 后端服务层逻辑》2. rank_service。
"""
from typing import Any, Optional, Sequence

from .. import config as cfg
from .. import db
from ..utils import as_int, norm_page, page_result
from . import pool_service

# 榜单排序白名单
_SORT_COLS = {
    "stars": "stars DESC",
    "gain": "gain DESC",
    "gain_1d": "star_gain_1d DESC",
    "gain_7d": "star_gain_7d DESC",
    "gain_30d": "star_gain_30d DESC",
    "velocity": "daily_velocity DESC",
    "release": "latest_release_at DESC",
    "updated": "repo_updated_at DESC",
    "name": "full_name ASC",
}

_PERIOD_METRIC = {"day": "star_gain_1d", "week": "star_gain_7d", "month": "star_gain_30d",
                  # 滚动窗口(近 N 天): 与「本周/本月」不同 —— 不随星期几漂移。
                  "rolling7": "star_gain_7d", "rolling30": "star_gain_30d"}
# 走滚动字段、**不**走每日事实表的周期类型
_ROLLING = ("rolling7", "rolling30")

# 滚动字段是否有非零值的 TTL 缓存(col -> (时间戳, 是否存在))
_ROLLUP_CACHE: dict[str, tuple[float, bool]] = {}


def _has_rollup_col(col: str, ttl: float = 300.0) -> bool:
    """库内该滚动字段是否存在非零值。带 TTL 缓存,避免每次榜单请求都扫一遍全表。"""
    import time as _t
    now = _t.time()
    hit = _ROLLUP_CACHE.get(col)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        found = bool(db.query_scalar(
            f"SELECT EXISTS (SELECT 1 FROM gh_repo WHERE COALESCE({col},0) > 0)"))
    except Exception:  # noqa: BLE001
        found = False
    _ROLLUP_CACHE[col] = (now, found)
    return found


def _strip_alias(order_sql: str) -> str:
    """子查询外层排序:去掉 r. 前缀(外层别名是 t,内层列已是裸名)。"""
    return order_sql.replace("r.", "")


def _base_filter(min_stars: Optional[int], max_age_days: Optional[int]) -> tuple[str, list[Any]]:
    """基础过滤。注意:live_rank 的 gain 口径要 JOIN gh_repo_star_daily,
    两张表都有 stars 列,所以这里一律带 r. 前缀消歧。"""
    ms = cfg.DEFAULT_MIN_STARS if min_stars is None else as_int(min_stars)
    mad = cfg.DEFAULT_MAX_AGE_DAYS if max_age_days is None else as_int(max_age_days)
    where, params = [], []
    if ms > 0:
        where.append("r.stars >= %s")
        params.append(ms)
    if mad > 0:
        where.append("COALESCE(r.repo_pushed_at, r.repo_updated_at, r.repo_created_at, "
                     "r.first_seen_at) >= now() - make_interval(days => %s)")
        params.append(mad)
    return (" AND ".join(where) if where else "TRUE"), params


def _extra_filters(q=None, langs=None, license_spdx=None, exclude_major=None) -> tuple[list[str], list[Any]]:
    where, params = [], []
    if langs:
        l = [x for x in langs if x]
        if l:
            where.append("r.lang = ANY(%s)")
            params.append(l)
    if license_spdx:
        where.append("r.license_spdx = %s")
        params.append(license_spdx)
    if exclude_major:
        where.append("COALESCE(r.is_major, FALSE) = FALSE")
    if q:
        where.append("(r.full_name ILIKE %s OR COALESCE(r.description,'') ILIKE %s "
                     "OR COALESCE(r.ai_summary,'') ILIKE %s)")
        params.extend([f"%{q}%"] * 3)
    return where, params


def _has_daily_fact(start: str, end: str) -> bool:
    """区间内是否存在**有效**的每日事实数据(至少一条非零 delta)。

    只判断「区间里有没有行」是不够的:每日快照表若只有一天数据,delta 会全为 0,
    此时走事实口径会被 `HAVING SUM(delta_stars) > 0` 全部滤掉,榜单直接变成 0 条。
    因此要求存在非零 delta 才认定有事实数据,否则降级到滚动字段。
    """
    try:
        return bool(db.query_scalar(
            "SELECT EXISTS (SELECT 1 FROM gh_repo_star_daily "
            "WHERE stat_date BETWEEN %s::date AND %s::date "
            "AND COALESCE(delta_stars,0) <> 0)", [start, end]))
    except Exception:  # noqa: BLE001
        return False


def live_rank(period_type: str = "week", metric: str = "gain", period_key: Optional[str] = None,
              q=None, langs=None, license_spdx=None, min_stars=None, exclude_major=None,
              max_age_days=None, sort=None, order="desc", page=1, page_size=100,
              top_n=None) -> dict:
    """实时榜单。metric ∈ total / gain / gain_index。"""
    period_type = (period_type or "week").lower()
    metric = (metric or "gain").lower()
    base, params = _base_filter(min_stars, max_age_days)
    extra, eparams = _extra_filters(q, langs, license_spdx, exclude_major)
    where = [base] + extra
    params += eparams

    # 口径（用户 2026-10 重定，唯一来源 pool_service）：
    #   日/周/月榜等一切**增量口径**只在「历史池」内取数；
    #   **总榜(total) 不受池限制**（全量按 stars 排序，沿用统一门槛）。
    if metric in ("gain", "gain_index"):
        where.append(pool_service.pool_where("r"))

    start, end = period_bounds(period_type, period_key)
    # 滚动窗口不走每日事实表: 事实表的区间是「本周一~今天」, 周一当天只有 1 天数据,
    # 会把「近 7 天飙升」显示成个位数(用户 2026-10 实测反馈)。滚动口径直接读滚动字段。
    if metric == "gain" and period_type not in _ROLLING and _has_daily_fact(start, end):
        # 事实口径:区间 SUM(delta_stars) HAVING > 0
        where.append("d.stat_date BETWEEN %s AND %s")
        params += [start, end]
        from_sql = ("FROM gh_repo r JOIN gh_repo_star_daily d ON d.repo_id = r.id "
                    "WHERE " + " AND ".join(where))
        agg = ("SELECT r.*, COALESCE(SUM(d.delta_stars),0) AS gain "
               + from_sql + " GROUP BY r.id HAVING COALESCE(SUM(d.delta_stars),0) > 0")
        sort_col = "gain DESC" if (sort in (None, "", "gain")) else _SORT_COLS.get(sort, "gain DESC")
        source = "daily"
    else:
        # total 走 gh_repo.stars;gain(无事实数据时)/gain_index 走滚动字段
        if metric in ("gain", "gain_index"):
            col = _PERIOD_METRIC.get(period_type, "star_gain_7d")
            if period_type == "day" and not _has_rollup_col("star_gain_1d"):
                # 日粒度增量尚未积累(需连续两日的快照才能算出 delta)。
                # 直接按 star_gain_1d 过滤会让日榜永远是 0 条 → 退用「近 7 天日均」折算,
                # 响应里以 source=rollup_avg7 明确标注口径, 前端据此提示用户。
                where.append("COALESCE(r.star_gain_7d,0) > 0")
                gain_expr = "ROUND(COALESCE(r.star_gain_7d,0) / 7.0, 2)"
                source = "rollup_avg7"
            else:
                where.append(f"COALESCE(r.{col},0) > 0")
                gain_expr = f"COALESCE(r.{col},0)"
                source = "rollup"
        else:
            gain_expr = "0"
            source = "repo"
        from_sql = "FROM gh_repo r WHERE " + " AND ".join(where)
        agg = (f"SELECT r.*, {gain_expr} AS gain " + from_sql)
        default_sort = "r.stars DESC" if metric == "total" else "gain DESC"
        sort_col = _SORT_COLS.get(sort, default_sort) if sort else default_sort

    order_sql = sort_col
    if (order or "desc").lower() == "asc" and "DESC" in sort_col:
        order_sql = sort_col.replace("DESC", "ASC")
    order_sql = f"{order_sql}, r.stars DESC, r.full_name ASC"

    if top_n:
        rows = db.query_all(f"SELECT * FROM ({agg}) t ORDER BY {_strip_alias(order_sql)} LIMIT %s",
                            params + [as_int(top_n)])
        return page_result(rows, len(rows), 1, as_int(top_n, 1))

    total = db.query_scalar(f"SELECT count(*) FROM ({agg}) t", params) or 0
    p, size = norm_page(page, page_size, 100)
    rows = db.query_all(
        f"SELECT * FROM ({agg}) t ORDER BY {_strip_alias(order_sql)} OFFSET %s LIMIT %s",
        params + [(p - 1) * size, size])
    res = page_result(rows, total, p, size)
    res.update({
        "metric": metric,
        "metric_label": (
            "飙升榜·事实口径" if (metric == "gain" and source == "daily")
            else {"total": "总榜", "gain": ("近 7 天飙升" if period_type == "rolling7" else ("近 30 天飙升" if period_type == "rolling30" else "飙升榜·滚动口径")),
                  "gain_index": "飙升榜·滚动口径"}.get(metric, metric)),
        "source": source,
        "period_type": period_type,
        "period_key": period_key,
        "period_start": start,
        "period_end": end,
        "coverage": _coverage(rows),
        "latest_gain_date": db.query_scalar("SELECT max(stat_date) FROM gh_repo_star_daily"),
    })
    return res


def _coverage(rows: Sequence[dict]) -> dict:
    """各可选列的数据覆盖度,前端据此智能隐藏空列。"""
    if not rows:
        return {}
    n = len(rows)
    keys = ["lang", "license_spdx", "gain", "star_gain_7d", "star_gain_30d",
            "latest_version", "repo_updated_at"]
    cov = {}
    for k in keys:
        filled = sum(1 for r in rows if r.get(k) not in (None, "", 0))
        cov[k] = round(filled / n, 3)
    return cov


def period_bounds(period_type: str, period_key: Optional[str] = None) -> tuple[str, str]:
    """按周期类型与周期键求区间。

    口径(用户 2026-10 重定, 日榜已恢复):
    - 日: 单日窗口 = 指定日; 未指定时取「今天」, 今天还没有事实数据就回退到
          最近一个有非零 delta 的日期(否则日榜会恒为 0 条)。
    - 周: 周一 ~ min(周日, 今天) —— 当前周未走完就截到今天(周五 = 周一~周五), 不空等到周日;
    - 月: 本月 1 日 ~ min(月末, 今天), 同理。
    """
    period_type = (period_type or "week").lower()
    if period_type in ("rolling7", "rolling30"):
        n = 7 if period_type == "rolling7" else 30
        first = db.query_scalar("SELECT CURRENT_DATE - %s", [n - 1])
        last = db.query_scalar("SELECT CURRENT_DATE")
        return (str(first), str(last))
    if period_type == "day":
        if period_key:
            return (str(period_key), str(period_key))
        d = db.query_scalar("SELECT CURRENT_DATE")
        has = db.query_scalar(
            "SELECT EXISTS (SELECT 1 FROM gh_repo_star_daily WHERE stat_date = %s "
            "AND COALESCE(delta_stars,0) <> 0)", [d])
        if not has:
            d = (db.query_scalar("SELECT max(stat_date) FROM gh_repo_star_daily "
                                 "WHERE COALESCE(delta_stars,0) <> 0") or d)
        return (str(d), str(d))
    if period_type == "month":
        if period_key:
            y, m = period_key.split("-")[:2]
        else:
            y, m = db.query_one("SELECT to_char(CURRENT_DATE,'YYYY') AS y, "
                                "to_char(CURRENT_DATE,'MM') AS m").values()
        first = f"{y}-{int(m):02d}-01"
        last = db.query_scalar(
            "SELECT LEAST((date_trunc('month', %s::date) + interval '1 month - 1 day')::date, "
            "CURRENT_DATE)", [first])
        return (first, str(last))
    # week: ISO 周一 ~ min(周日, 今天)
    if period_key:
        if "-W" in period_key:
            first = db.query_scalar("SELECT to_date(%s, 'IYYY-\"W\"IW')::date", [period_key])
        else:
            first = db.query_scalar("SELECT date_trunc('week', %s::date)::date", [period_key])
    else:
        first = db.query_scalar("SELECT date_trunc('week', CURRENT_DATE)::date")
    if first is None:
        first = db.query_scalar("SELECT date_trunc('week', CURRENT_DATE)::date")
    last = db.query_scalar("SELECT LEAST((%s::date + interval '6 days')::date, CURRENT_DATE)", [first])
    return (str(first), str(last))


def period_overview(period_type="week", metric="gain", period_key=None, **kw) -> dict:
    """周期概览:优先读快照,无快照回退实时。"""
    snap = db.query_one(
        "SELECT count(*) AS n, max(is_final::int) AS is_final FROM gh_rank_snapshot "
        "WHERE period_type = %s AND metric = %s AND (%s IS NULL OR period_key = %s)",
        [period_type, metric, period_key, period_key])
    if snap and as_int(snap.get("n")) > 0:
        return get_snapshot(period_type, period_key, metric, page=1, page_size=100)
    return live_rank(period_type, metric, period_key, **kw)


def top_movers(days: int = 7, limit: int = 12, min_stars: Optional[int] = None) -> list[dict]:
    """近 N 天飙升(大屏右列)。优先每日事实表;无事实数据时回退主表滚动增量。

    增量口径 → **限定在历史池内**（与日/周/月榜同口径，用户 2026-10 定）。
    """
    ms = cfg.DEFAULT_MIN_STARS if min_stars is None else as_int(min_stars)
    pw = pool_service.pool_where("r")
    rows = db.query_all(f"""
        SELECT r.full_name, r.html_url, r.lang, r.stars, r.ai_summary, r.summary,
               r.description, r.is_major, COALESCE(SUM(d.delta_stars),0) AS gain
        FROM gh_repo r JOIN gh_repo_star_daily d ON d.repo_id = r.id
        WHERE d.stat_date >= CURRENT_DATE - %s AND r.stars >= %s AND {pw}
        GROUP BY r.id HAVING COALESCE(SUM(d.delta_stars),0) > 0
        ORDER BY gain DESC LIMIT %s
    """, [as_int(days, 7), ms, as_int(limit, 12)])
    if rows:
        return rows
    # 回退:主表滚动增量(源资料只导主表时唯一可用口径)
    col = {1: "star_gain_1d", 7: "star_gain_7d", 30: "star_gain_30d",
           60: "star_gain_60d"}.get(as_int(days, 7), "star_gain_7d")
    return db.query_all(f"""
        SELECT r.full_name, r.html_url, r.lang, r.stars, r.ai_summary, r.summary,
               r.description, r.is_major, COALESCE(r.{col},0) AS gain
        FROM gh_repo r
        WHERE r.stars >= %s AND COALESCE(r.{col},0) > 0 AND {pw}
        ORDER BY gain DESC LIMIT %s
    """, [ms, as_int(limit, 12)])


def list_periods(period_type: str = "week", limit: int = 60) -> list[dict]:
    if period_type == "day":
        rows = db.query_all("SELECT DISTINCT stat_date AS period_key, stat_date AS period_start, "
                            "stat_date AS period_end FROM gh_repo_star_daily "
                            "ORDER BY stat_date DESC LIMIT %s", [as_int(limit, 60)])
        if rows:
            return rows
        return db.query_all("""
            SELECT period_key, period_start, period_end FROM gh_stat_period
            WHERE period_type = 'day' ORDER BY period_start DESC LIMIT %s
        """, [as_int(limit, 60)])
    if period_type == "month":
        rows = db.query_all("""
            SELECT DISTINCT to_char(stat_date,'YYYY-MM') AS period_key,
                   date_trunc('month', stat_date)::date AS period_start,
                   (date_trunc('month', stat_date) + interval '1 month - 1 day')::date AS period_end
            FROM gh_repo_star_daily ORDER BY period_start DESC LIMIT %s
        """, [as_int(limit, 60)])
        if rows:
            return rows
        return db.query_all("""
            SELECT period_key, period_start, period_end FROM gh_stat_period
            WHERE period_type = 'month' ORDER BY period_start DESC LIMIT %s
        """, [as_int(limit, 60)])
    rows = db.query_all("""
        SELECT DISTINCT week_start AS period_key, week_start AS period_start,
               (week_start + 6) AS period_end
        FROM gh_repo_star_weekly ORDER BY week_start DESC LIMIT %s
    """, [as_int(limit, 60)])
    if rows:
        return rows
    # 回退:周期元信息表(源资料自带)
    return db.query_all("""
        SELECT period_key, period_start, period_end FROM gh_stat_period
        WHERE period_type = 'week' ORDER BY period_start DESC LIMIT %s
    """, [as_int(limit, 60)])


# ---------------- 快照读取(计算在 snapshot_service) ----------------
def get_snapshot(period_type: str, period_key: Optional[str], metric: str = "total",
                 page=1, page_size=100, **kw) -> dict:
    where = ["period_type = %s", "metric = %s"]
    params: list[Any] = [period_type, metric]
    if period_key:
        where.append("period_key = %s")
        params.append(period_key)
    else:
        pk = db.query_scalar(
            "SELECT period_key FROM gh_rank_snapshot WHERE period_type = %s AND metric = %s "
            "ORDER BY period_key DESC LIMIT 1", [period_type, metric])
        if not pk:
            return page_result([], 0, 1, page_size)
        where.append("period_key = %s")
        params.append(pk)
    where_sql = " AND ".join(where)
    total = db.query_scalar(f"SELECT count(*) FROM gh_rank_snapshot WHERE {where_sql}", params) or 0
    p, size = norm_page(page, page_size, 100)
    rows = db.query_all(
        f"SELECT rank_no, full_name, stars, gain, lang, license_spdx, summary, html_url, "
        f"payload, is_final, period_key, period_label, period_start, period_end, computed_at "
        f"FROM gh_rank_snapshot WHERE {where_sql} ORDER BY rank_no "
        f"OFFSET %s LIMIT %s", params + [(p - 1) * size, size])
    res = page_result(rows, total, p, size)
    if rows:
        res.update({"period_key": rows[0]["period_key"], "period_label": rows[0]["period_label"],
                    "period_start": rows[0]["period_start"], "period_end": rows[0]["period_end"],
                    "is_final": rows[0]["is_final"]})
    res["metric"] = metric
    return res


def snapshot_periods(period_type: str = "week", metric: Optional[str] = None,
                     limit: int = 100) -> list[dict]:
    where = ["period_type = %s"]
    params: list[Any] = [period_type]
    if metric:
        where.append("metric = %s")
        params.append(metric)
    return db.query_all(
        f"SELECT period_key, period_label, period_start, period_end, metric, "
        f"count(*) AS n, max(is_final::int) AS is_final, max(computed_at) AS computed_at "
        f"FROM gh_rank_snapshot WHERE {' AND '.join(where)} "
        f"GROUP BY period_key, period_label, period_start, period_end, metric "
        f"ORDER BY period_start DESC LIMIT %s", params + [as_int(limit, 100)])


def stat_periods(period_type: Optional[str] = None, limit: int = 100) -> list[dict]:
    if period_type:
        return db.query_all("SELECT * FROM gh_stat_period WHERE period_type = %s "
                            "ORDER BY period_start DESC LIMIT %s", [period_type, as_int(limit, 100)])
    return db.query_all("SELECT * FROM gh_stat_period ORDER BY period_start DESC LIMIT %s",
                        [as_int(limit, 100)])


# ---------------- 白名单 ----------------
def whitelist_list(limit: int = 200, list_type: Optional[str] = None) -> list[dict]:
    if list_type:
        return db.query_all("SELECT * FROM gh_rank_whitelist WHERE list_type = %s "
                            "ORDER BY created_at DESC LIMIT %s", [list_type, as_int(limit, 200)])
    return db.query_all("SELECT * FROM gh_rank_whitelist ORDER BY created_at DESC LIMIT %s",
                        [as_int(limit, 200)])


def whitelist_add(full_name: str, list_type: str = "major", note: Optional[str] = None,
                  source: str = "manual") -> dict:
    rows = db.execute("""
        INSERT INTO gh_rank_whitelist (full_name, list_type, note, source)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (list_type, full_name) DO UPDATE SET note = EXCLUDED.note, updated_at = now()
        RETURNING *
    """, [full_name, list_type, note, source], returning=True)
    from .repo_service import refresh_major_flags
    refresh_major_flags()
    return rows[0] if rows else {}


def whitelist_remove(ids: Sequence[int]) -> int:
    if not ids:
        return 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gh_rank_whitelist WHERE id = ANY(%s)", [list(ids)])
            n = cur.rowcount
        conn.commit()
    from .repo_service import refresh_major_flags
    refresh_major_flags()
    return n


def whitelist_toggle(wid: int, enabled: bool) -> dict:
    rows = db.execute("UPDATE gh_rank_whitelist SET enabled = %s, updated_at = now() "
                      "WHERE id = %s RETURNING *", [bool(enabled), as_int(wid)], returning=True)
    from .repo_service import refresh_major_flags
    refresh_major_flags()
    return rows[0] if rows else {}
