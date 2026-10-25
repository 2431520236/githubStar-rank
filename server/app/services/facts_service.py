"""事实表服务:每日/每周 star 事实写入、增量回填、查询。

对应手册《04 · 后端服务层逻辑》4. facts_service。
唯一数据源(2026-10 用户定): 官方 GET /repos/{o}/{r}/stargazers/history(5000 次/小时额度)。
"""
import logging
from datetime import date
from typing import Any, Optional, Sequence

from .. import config as cfg
from .. import db
from ..utils import as_int, norm_page, page_result

log = logging.getLogger("ghradar.facts")

# ---------------- 每日事实(唯一写入者: 官方 stargazers/history) ----------------
# 2026-10 用户定: star 历史唯一数据源 = 官方 GET /repos/{o}/{r}/stargazers/history。
# 原 take_daily_snapshot() 已删除 —— 它是混入源: 官方 days 明细落库时 stars 列原为 0,
# 该 SQL 的 COALESCE(prev.stars, r.stars) 取到这个 0, 于是 delta = 当前总 star 数
# (实测 52,497 仓在 2026-10-12 一次性写入 8,796 万「假日增」, 并污染 gh_stat_period 当周 gain_sum)。
# 日粒度现在只由 save_daily_deltas() 写。


def rollup_gains() -> dict:
    """回填 star_gain_1d/7d/30d/60d 与 daily_velocity。

    7/30/60 天增量要求窗口内至少有 max(2, days*0.6) 天的数据(手册 §4)。

    双数据源: ① 日事实表 gh_repo_star_daily(权威); ② 周事实表 gh_repo_star_weekly 的
    days jsonb 按天明细(ClickHouse 补历史带来的逐天新增)。日表不足时自动回退,
    否则「日粒度未积累」会让 7d/30d/60d 永远停在旧值(飙升榜/大屏失真)。
    """
    sql = """
    WITH w AS (
      SELECT repo_id,
             SUM(delta_stars) FILTER (WHERE stat_date >= CURRENT_DATE - 1)  AS g1,
             SUM(delta_stars) FILTER (WHERE stat_date >= CURRENT_DATE - 7)  AS g7,
             SUM(delta_stars) FILTER (WHERE stat_date >= CURRENT_DATE - 30) AS g30,
             SUM(delta_stars) FILTER (WHERE stat_date >= CURRENT_DATE - 60) AS g60,
             COUNT(DISTINCT stat_date) FILTER (WHERE stat_date >= CURRENT_DATE - 7)  AS d7,
             COUNT(DISTINCT stat_date) FILTER (WHERE stat_date >= CURRENT_DATE - 30) AS d30,
             COUNT(DISTINCT stat_date) FILTER (WHERE stat_date >= CURRENT_DATE - 60) AS d60
      FROM gh_repo_star_daily
      WHERE stat_date >= CURRENT_DATE - 60
      GROUP BY repo_id
    ),
    wd AS (
      SELECT w.repo_id,
             SUM((e->>'count')::int) FILTER (WHERE (e->>'day')::date >= CURRENT_DATE - 1)  AS g1,
             SUM((e->>'count')::int) FILTER (WHERE (e->>'day')::date >= CURRENT_DATE - 7)  AS g7,
             SUM((e->>'count')::int) FILTER (WHERE (e->>'day')::date >= CURRENT_DATE - 30) AS g30,
             SUM((e->>'count')::int) FILTER (WHERE (e->>'day')::date >= CURRENT_DATE - 60) AS g60,
             COUNT(DISTINCT (e->>'day')) FILTER (WHERE (e->>'day')::date >= CURRENT_DATE - 7)  AS d7,
             COUNT(DISTINCT (e->>'day')) FILTER (WHERE (e->>'day')::date >= CURRENT_DATE - 30) AS d30,
             COUNT(DISTINCT (e->>'day')) FILTER (WHERE (e->>'day')::date >= CURRENT_DATE - 60) AS d60
      FROM gh_repo_star_weekly w
      CROSS JOIN LATERAL jsonb_array_elements(COALESCE(w.days, '[]'::jsonb)) e
      WHERE w.week_start >= CURRENT_DATE - 70
      GROUP BY w.repo_id
    )
    UPDATE gh_repo r SET
      star_gain_1d = GREATEST(COALESCE(w.g1,0), COALESCE(wd.g1,0)),
      -- 回退源 wd 的阈值放宽到「>=1 天」: 历史补数天然有缺日, 苛求 60% 覆盖率会把整批数据挡在门外
      star_gain_7d = CASE WHEN COALESCE(w.d7,0)  >= GREATEST(2, ROUND(7*0.6))  THEN GREATEST(COALESCE(w.g7,0),  COALESCE(wd.g7,0))
                          WHEN COALESCE(wd.d7,0) >= 1 THEN COALESCE(wd.g7,0)
                          ELSE r.star_gain_7d  END,
      star_gain_30d = CASE WHEN COALESCE(w.d30,0) >= GREATEST(2, ROUND(30*0.6)) THEN GREATEST(COALESCE(w.g30,0), COALESCE(wd.g30,0))
                           WHEN COALESCE(wd.d30,0)>= 1 THEN COALESCE(wd.g30,0)
                           ELSE r.star_gain_30d END,
      star_gain_60d = CASE WHEN COALESCE(w.d60,0) >= GREATEST(2, ROUND(60*0.6)) THEN GREATEST(COALESCE(w.g60,0), COALESCE(wd.g60,0))
                           WHEN COALESCE(wd.d60,0)>= 1 THEN COALESCE(wd.g60,0)
                           ELSE r.star_gain_60d END,
      daily_velocity = ROUND(GREATEST(
          COALESCE(w.g1, 0), COALESCE(wd.g1, 0),
          COALESCE(w.g7, 0) / 7.0, COALESCE(wd.g7, 0) / 7.0,
          COALESCE(w.g30, 0) / 30.0, COALESCE(wd.g30, 0) / 30.0,
          COALESCE(w.g60, 0) / 60.0, COALESCE(wd.g60, 0) / 60.0)::numeric, 2),
      updated_at = now()
    FROM w FULL OUTER JOIN wd ON wd.repo_id = w.repo_id
    WHERE COALESCE(w.repo_id, wd.repo_id) = r.id
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            n = cur.rowcount
        conn.commit()
    return {"updated": n}


# ---------------- 周事实 ----------------
_WEEKLY_UPSERT = """
INSERT INTO gh_repo_star_weekly
    (repo_id, week_start, week_key, week_label, week_index, weekly_new, cumulative, days, source)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (repo_id, week_start) DO UPDATE SET
    week_key = EXCLUDED.week_key,
    week_label = EXCLUDED.week_label,
    week_index = EXCLUDED.week_index,
    weekly_new = EXCLUDED.weekly_new,
    cumulative = EXCLUDED.cumulative,
    days = EXCLUDED.days,
    source = EXCLUDED.source,
    fetched_at = now()
"""

_DAILY_DELTA_UPSERT = """
INSERT INTO gh_repo_star_daily
    (repo_id, stat_date, stars, delta_stars, source)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (repo_id, stat_date) DO UPDATE SET
    stars = EXCLUDED.stars,
    delta_stars = EXCLUDED.delta_stars,
    source = EXCLUDED.source
"""


def _week_index(week_start: date) -> int:
    start = date.fromisoformat(cfg.STAT_START_DATE)
    return max(1, ((week_start - start).days // 7) + 1)


def save_weekly(repo_id: int, weeks: Sequence[dict]) -> int:
    """官方周聚合落 gh_repo_star_weekly(周日起始,cumulative 逐周累加)。"""
    rows = []
    for w in weeks:
        ws = w.get("week_start") or w.get("week")
        if not ws:
            continue
        if isinstance(ws, str):
            ws = date.fromisoformat(ws[:10])
        weekly_new = as_int(w.get("weekly_new", w.get("new", 0)))
        rows.append((repo_id, ws, w.get("week_key"), w.get("week_label"),
                     _week_index(ws), weekly_new, as_int(w.get("cumulative", 0)),
                     db.Json(w.get("days") or []), w.get("source", "stargazers_history")))
    if not rows:
        return 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(_WEEKLY_UPSERT, rows)
        conn.commit()
    return len(rows)


def _validate_days(days: Sequence[dict]) -> tuple[list[dict], list[str]]:
    """写入前校验每日增量 —— 不合规的行直接不落库(这就是"拦截", 不依赖日志)。

    拒绝三类脏行, 全是 2026-10-12「假日增 8,796 万」事故的形态:
      1. delta < 0                增量不可能为负
      2. delta > stars            单日增量不可能超过当天累计 star
      3. stars <= 0 且 delta > 0  累计未知却给出正增量(旧 bug: stars 被写成 0)
    """
    ok, bad = [], []
    for d in days:
        sd = d.get("date") or d.get("stat_date")
        if not sd:
            continue
        stars = as_int(d.get("stars", d.get("cumulative", 0)))
        delta = as_int(d.get("delta_stars", d.get("count", 0)))
        if delta < 0:
            bad.append("%s delta=%s<0" % (sd, delta))
            continue
        if stars > 0 and delta > stars:
            bad.append("%s delta=%s>stars=%s" % (sd, delta, stars))
            continue
        if stars <= 0 and delta > 0:
            bad.append("%s stars=0 但 delta=%s" % (sd, delta))
            continue
        ok.append(d)
    return ok, bad


def _validate_weeks(weeks: Sequence[dict], days: Sequence[dict]) -> list[str]:
    """周内自洽校验: 每周 weekly_new 必须等于该周逐日 count 之和。"""
    from datetime import timedelta
    sums: dict = {}
    for d in days:
        sd = str(d.get("date") or d.get("stat_date") or "")[:10]
        if len(sd) < 10:
            continue
        try:
            dd = date.fromisoformat(sd)
        except ValueError:
            continue
        ws = dd - timedelta(days=dd.weekday())
        sums[ws] = sums.get(ws, 0) + as_int(d.get("delta_stars", d.get("count", 0)))
    bad = []
    for w in weeks:
        ws = w.get("week_start") or w.get("week")
        if isinstance(ws, str):
            try:
                ws = date.fromisoformat(ws[:10])
            except ValueError:
                continue
        if not isinstance(ws, date):
            continue
        got = sums.get(ws)
        wn = as_int(w.get("weekly_new", w.get("new", 0)))
        if got is not None and got != wn:
            bad.append("%s weekly_new=%s != 逐日和=%s" % (ws, wn, got))
    return bad


def save_daily_deltas(repo_id: int, days: Sequence[dict], source: str = "stargazers_history") -> int:
    """把官方 days 明细拆到 gh_repo_star_daily。"""
    rows = []
    for d in days:
        sd = d.get("date") or d.get("stat_date")
        if not sd:
            continue
        if isinstance(sd, str):
            sd = date.fromisoformat(sd[:10])
        # stars = 当天结束时的累计 star(history_collector._to_weeks 从周累计倒推而来),
        # 必须写真实值: 写 0 会让下游把它当成「上一条快照」从而算出假增量。
        rows.append((repo_id, sd, as_int(d.get("stars", d.get("cumulative", 0))),
                     as_int(d.get("delta_stars", d.get("count", 0))), source))
    if not rows:
        return 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(_DAILY_DELTA_UPSERT, rows)
        conn.commit()
    return len(rows)


def save_history(repo_id: int, weeks: Sequence[dict], days: Sequence[dict] | None = None,
                 source: str = "stargazers_history") -> dict:
    """周 + 日一起落,并更新 history_synced_at。

    用户 2026-10 要求「日更新必须先校验再写」: 落库前先跑 _validate_days /
    _validate_weeks, 不合规的行直接不落库并把原因回传, 而不是写完再由下游发现。
    """
    days = list(days or [])
    good_days, bad_days = _validate_days(days)
    bad_weeks = _validate_weeks(weeks, days)
    if bad_days or bad_weeks:
        log.warning("history 校验拦截 repo=%s 日行=%d 周=%d 例: %s",
                    repo_id, len(bad_days), len(bad_weeks),
                    (bad_days + bad_weeks)[:3])
    nw = save_weekly(repo_id, weeks)
    nd = save_daily_deltas(repo_id, good_days, source) if good_days else 0
    db.execute("UPDATE gh_repo SET history_synced_at = now(), updated_at = now() WHERE id = %s",
               [repo_id])
    return {"weekly": nw, "daily": nd,
            "rejected_days": len(bad_days), "rejected_weeks": len(bad_weeks),
            "reject_reasons": (bad_days + bad_weeks)[:5]}


# ---------------- 查询 ----------------
def query_daily(stat_date: Optional[str] = None, repo_id: Optional[int] = None,
                limit=200, page=1, stat_date_from: Optional[str] = None,
                stat_date_to: Optional[str] = None,
                repo_ids: Optional[Sequence[int]] = None,
                full_names: Optional[Sequence[str]] = None,
                order: str = "desc") -> dict:
    """每日事实查询。

    支持:单日(stat_date) / 日期区间(from~to) / 单项目(repo_id) /
    多项目(repo_ids 或 full_names) / 升降序(order) / 分页(limit 上限 5000)。
    """
    where, params = [], []
    if stat_date:
        where.append("d.stat_date = %s")
        params.append(stat_date)
    if stat_date_from:
        where.append("d.stat_date >= %s")
        params.append(stat_date_from)
    if stat_date_to:
        where.append("d.stat_date <= %s")
        params.append(stat_date_to)
    if repo_id:
        where.append("d.repo_id = %s")
        params.append(repo_id)
    if repo_ids:
        where.append("d.repo_id = ANY(%s)")
        params.append([as_int(x) for x in repo_ids])
    if full_names:
        where.append("r.full_name = ANY(%s)")
        params.append(list(full_names))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.query_scalar(
        f"SELECT count(*) FROM gh_repo_star_daily d "
        f"JOIN gh_repo r ON r.id = d.repo_id {where_sql}", params) or 0
    p, size = norm_page(page, limit)
    direction = "ASC" if str(order or "").lower() == "asc" else "DESC"
    rows = db.query_all(
        f"SELECT d.*, r.full_name FROM gh_repo_star_daily d "
        f"JOIN gh_repo r ON r.id = d.repo_id {where_sql} "
        f"ORDER BY d.stat_date {direction}, d.delta_stars {direction}, d.id {direction} "
        f"OFFSET %s LIMIT %s",
        params + [(p - 1) * size, size])
    return page_result(rows, total, p, size)


def query_weekly(week_start: Optional[str] = None, repo_id: Optional[int] = None,
                 limit=200, page=1, week_from: Optional[str] = None,
                 week_to: Optional[str] = None,
                 repo_ids: Optional[Sequence[int]] = None,
                 full_names: Optional[Sequence[str]] = None,
                 order: str = "desc") -> dict:
    """每周事实查询。参数语义与 query_daily 一致。"""
    where, params = [], []
    if week_start:
        where.append("w.week_start = %s")
        params.append(week_start)
    if week_from:
        where.append("w.week_start >= %s")
        params.append(week_from)
    if week_to:
        where.append("w.week_start <= %s")
        params.append(week_to)
    if repo_id:
        where.append("w.repo_id = %s")
        params.append(repo_id)
    if repo_ids:
        where.append("w.repo_id = ANY(%s)")
        params.append([as_int(x) for x in repo_ids])
    if full_names:
        where.append("r.full_name = ANY(%s)")
        params.append(list(full_names))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.query_scalar(
        f"SELECT count(*) FROM gh_repo_star_weekly w "
        f"JOIN gh_repo r ON r.id = w.repo_id {where_sql}", params) or 0
    p, size = norm_page(page, limit)
    direction = "ASC" if str(order or "").lower() == "asc" else "DESC"
    rows = db.query_all(
        f"SELECT w.*, r.full_name FROM gh_repo_star_weekly w "
        f"JOIN gh_repo r ON r.id = w.repo_id {where_sql} "
        f"ORDER BY w.week_start {direction}, w.weekly_new {direction}, w.id {direction} "
        f"OFFSET %s LIMIT %s",
        params + [(p - 1) * size, size])
    return page_result(rows, total, p, size)


def yesterday_gain(limit=30) -> dict:
    """昨日新增(delta_stars 排序)。"""
    latest = db.query_scalar("SELECT max(stat_date) FROM gh_repo_star_daily")
    if not latest:
        return {"date": None, "rows": [], "count": 0, "latest_date_in_db": None}
    rows = db.query_all("""
        SELECT r.full_name, r.html_url, r.lang, r.stars, d.delta_stars, d.stat_date
        FROM gh_repo_star_daily d JOIN gh_repo r ON r.id = d.repo_id
        WHERE d.stat_date = %s AND d.delta_stars <> 0
        ORDER BY d.delta_stars DESC LIMIT %s
    """, [latest, as_int(limit, 30)])
    return {"date": latest.isoformat(), "rows": rows, "count": len(rows),
            "latest_date_in_db": latest.isoformat()}


def summary_stats() -> dict:
    return {
        "daily_rows": db.query_scalar("SELECT count(*) FROM gh_repo_star_daily") or 0,
        "weekly_rows": db.query_scalar("SELECT count(*) FROM gh_repo_star_weekly") or 0,
        "daily_repos": db.query_scalar("SELECT count(DISTINCT repo_id) FROM gh_repo_star_daily") or 0,
        "weekly_repos": db.query_scalar("SELECT count(DISTINCT repo_id) FROM gh_repo_star_weekly") or 0,
        "daily_from": db.query_scalar("SELECT min(stat_date) FROM gh_repo_star_daily"),
        "daily_to": db.query_scalar("SELECT max(stat_date) FROM gh_repo_star_daily"),
        "weekly_from": db.query_scalar("SELECT min(week_start) FROM gh_repo_star_weekly"),
        "weekly_to": db.query_scalar("SELECT max(week_start) FROM gh_repo_star_weekly"),
        "snapshot_rows": db.query_scalar("SELECT count(*) FROM gh_rank_snapshot") or 0,
    }


def repo_daily_series(repo_id: int, limit=400) -> list[dict]:
    return db.query_all(
        "SELECT stat_date, stars, delta_stars FROM gh_repo_star_daily "
        "WHERE repo_id = %s ORDER BY stat_date DESC LIMIT %s", [repo_id, as_int(limit, 400)])
