"""快照服务:榜单快照计算与冻结 + 周期按需补齐。

对应手册《04 · 后端服务层逻辑》3. snapshot_service。
完整周期首次计算即冻结(is_final=TRUE);未走完的当前周期可反复更新。
"""
import logging
import threading
from typing import Optional, Sequence

from psycopg2.extras import execute_values

from .. import config as cfg
from .. import db
from ..utils import as_int
from . import rank_service
from . import facts_service

log = logging.getLogger("ghradar.snapshot")

_ENSURE_LOCK = threading.Lock()
_ENSURE_INFLIGHT: set[tuple] = set()

HISTORY_LOOKBACK_DAYS = cfg.HISTORY_LOOKBACK_DAYS
_RESERVE = 50


def _is_complete(period_type: str, start: str, end: str) -> bool:
    """周期是否完整走完(结束日 < 今天)。"""
    from datetime import date
    try:
        return date.fromisoformat(end) < date.today()
    except ValueError:
        return False


def _snapshot_rows(period_type: str, metric: str, period_key: str, start: str, end: str,
                   min_stars: Optional[int] = None, exclude_major: bool = False,
                   limit: int = 2000, label: Optional[str] = None) -> list[dict]:
    """按榜单口径取前 N 作为快照内容。"""
    res = rank_service.live_rank(period_type, metric, period_key,
                                 min_stars=min_stars if min_stars is not None else cfg.DEFAULT_MIN_STARS,
                                 exclude_major=exclude_major, top_n=limit)
    rows = []
    for i, r in enumerate(res.get("items") or []):
        rows.append((
            period_type, period_key, label, start, end, metric,
            i + 1, r.get("id"), r.get("full_name"), r.get("stars"), as_int(r.get("gain")),
            r.get("lang"), r.get("license_spdx"),
            (r.get("ai_summary") or r.get("summary") or r.get("description")),
            r.get("html_url"),
            db.Json({"star_gain_1d": r.get("star_gain_1d"), "star_gain_7d": r.get("star_gain_7d"),
                     "star_gain_30d": r.get("star_gain_30d"), "star_gain_60d": r.get("star_gain_60d"),
                     "daily_velocity": r.get("daily_velocity"), "is_major": r.get("is_major")}),
        ))
    return rows


def snapshot_metric(metric: str) -> str:
    """归一化快照口径。表约束只允许 total / gain(见 01-schema.sql ck_gh_rank_metric),
    实时榜单的 gain_index(近 N 天滚动口径) 落库时归到 gain,其余一律 total。"""
    return "gain" if (metric or "").lower() in ("gain", "gain_index") else "total"


def compute_rank(period_type: str = "week", period_key: Optional[str] = None,
                 metric: str = "total", force: bool = False, min_stars: Optional[int] = None,
                 exclude_major: bool = False) -> dict:
    """计算某周期快照并入库;完整周期首次即冻结。"""
    metric = snapshot_metric(metric)
    start, end = rank_service.period_bounds(period_type, period_key)
    if not period_key:
        period_key = _key_from_bounds(period_type, start)
    complete = _is_complete(period_type, start, end)

    existing = db.query_one(
        "SELECT count(*) AS n, max(is_final::int) AS is_final FROM gh_rank_snapshot "
        "WHERE period_type = %s AND period_key = %s AND metric = %s",
        [period_type, period_key, metric])
    if existing and as_int(existing["n"]) > 0 and as_int(existing["is_final"]) == 1 and not force:
        return {"status": "skipped", "reason": "周期已冻结", "period_key": period_key,
                "metric": metric, "rows": existing["n"]}

    rows = _snapshot_rows(period_type, metric, period_key, start, end, min_stars, exclude_major,
                          label=_label(period_type, start))
    is_final = bool(complete)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gh_rank_snapshot WHERE period_type = %s AND period_key = %s "
                        "AND metric = %s AND is_final = FALSE",
                        [period_type, period_key, metric])
            if rows:
                execute_values(cur, """
                    INSERT INTO gh_rank_snapshot
                        (period_type, period_key, period_label, period_start, period_end, metric,
                         rank_no, repo_id, full_name, stars, gain, lang, license_spdx, summary,
                         html_url, payload, is_final)
                    VALUES %s
                    ON CONFLICT (period_type, period_key, metric, full_name) DO UPDATE SET
                        rank_no = EXCLUDED.rank_no, stars = EXCLUDED.stars, gain = EXCLUDED.gain,
                        lang = EXCLUDED.lang, license_spdx = EXCLUDED.license_spdx,
                        summary = EXCLUDED.summary, html_url = EXCLUDED.html_url,
                        payload = EXCLUDED.payload, is_final = EXCLUDED.is_final,
                        computed_at = now()
                """, [r[:16] + (is_final,) for r in rows], page_size=500)
            cur.execute("""
                INSERT INTO gh_stat_period
                    (period_type, period_key, period_label, period_start, period_end,
                     is_complete, is_locked, repo_count, gain_sum, computed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (period_type, period_key) DO UPDATE SET
                    is_complete = EXCLUDED.is_complete, is_locked = EXCLUDED.is_locked,
                    repo_count = EXCLUDED.repo_count, gain_sum = EXCLUDED.gain_sum,
                    computed_at = now()
            """, [period_type, period_key, _label(period_type, start), start, end,
                  complete, is_final, len(rows), sum(as_int(r[10]) for r in rows)])
        conn.commit()
    return {"status": "computed", "period_key": period_key, "metric": metric,
            "rows": len(rows), "is_final": is_final, "period_start": start, "period_end": end}


def _key_from_bounds(period_type: str, start: str) -> str:
    if period_type == "day":
        return start
    if period_type == "month":
        return start[:7]
    return _iso_week(start)


def _iso_week(d: str) -> str:
    import datetime as dt
    y, m, dd = (int(x) for x in d.split("-")[:3])
    iso = dt.date(y, m, dd).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _label(period_type: str, start: str) -> str:
    if period_type == "day":
        return start
    if period_type == "month":
        return start[:7]
    return _iso_week(start)


def ensure_period(period_type: str = "week", metric: str = "gain", period_key: Optional[str] = None,
                  min_stars: Optional[int] = None, exclude_major: bool = False,
                  collect: bool = True, max_collect: int = 20) -> dict:
    """周期按需补齐:无事实数据时先补采,再计算入库。"""
    metric = snapshot_metric(metric)
    key = (period_type, metric, period_key)
    with _ENSURE_LOCK:
        if key in _ENSURE_INFLIGHT:
            return {"status": "busy", "reason": "该周期正在补齐中"}
        _ENSURE_INFLIGHT.add(key)
    try:
        start, end = rank_service.period_bounds(period_type, period_key)
        collected = 0
        if collect and metric == "gain":
            has = db.query_scalar(
                "SELECT count(*) FROM gh_repo_star_daily WHERE stat_date BETWEEN %s AND %s",
                [start, end]) or 0
            if not has:
                from ..collectors import history_collector
                res = history_collector.collect_history(limit=max_collect, concurrency=3,
                                                        refresh_after_days=0, only_missing=False)
                collected = as_int(res.get("ok"))
                facts_service.rollup_gains()
        out = compute_rank(period_type, period_key, metric, force=False,
                           min_stars=min_stars, exclude_major=exclude_major)
        out["collected"] = collected
        return out
    finally:
        with _ENSURE_LOCK:
            _ENSURE_INFLIGHT.discard(key)


def compute_all_ranks(start_date: Optional[str] = None, min_stars: Optional[int] = None) -> dict:
    """自 STAT_START_DATE 起批量计算(日/周/月 × total/gain)。"""
    start = start_date or cfg.STAT_START_DATE
    computed = skipped = 0
    weeks = db.query_all("SELECT DISTINCT week_start FROM gh_repo_star_weekly "
                         "WHERE week_start >= %s ORDER BY week_start", [start])
    for w in weeks:
        key = _iso_week(str(w["week_start"]))
        for metric in ("total", "gain"):
            r = compute_rank("week", key, metric, min_stars=min_stars)
            if r.get("status") == "skipped":
                skipped += 1
            else:
                computed += 1
    months = db.query_all("SELECT DISTINCT to_char(stat_date,'YYYY-MM') AS k FROM gh_repo_star_daily "
                          "WHERE stat_date >= %s ORDER BY k", [start])
    for m in months:
        for metric in ("total", "gain"):
            r = compute_rank("month", m["k"], metric, min_stars=min_stars)
            if r.get("status") == "skipped":
                skipped += 1
            else:
                computed += 1
    return {"computed": computed, "skipped": skipped, "weeks": len(weeks), "months": len(months)}

