"""统计(大屏)接口(prefix /api/stats)。

应用了轻量 TTL 缓存（cache.py），高流量接口默认缓存 5 分钟。
另提供 `/screen` 聚合接口：把大屏需要的多个模块合并成一次请求，
减少 HTTP 往返与连接池排队（用户 2026-10 要求）。
"""
from typing import Optional

from fastapi import APIRouter, Query

from .. import config as cfg
from ..services import rank_service, stat_service
from ..utils import fail, ok
from .. import cache

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview")
@cache.cached()
def overview():
    try:
        return ok(stat_service.overview())
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/weekly-trend")
@cache.cached()
def weekly_trend(exclude_major: bool = False, limit: int = Query(60),
                 year: Optional[int] = Query(None)):
    """每周新增趋势。year 缺省=当前年(库里有数据的最近一年); year=0=全部年份。"""
    try:
        return ok(stat_service.weekly_trend(exclude_major, limit, year))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/rank-trend")
@cache.cached()
def rank_trend(period_type: str = Query("week"), metric: str = Query("gain"),
               limit: int = Query(30)):
    try:
        return ok(stat_service.rank_trend(period_type, metric, limit))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/lang-distribution")
@cache.cached()
def lang_distribution(limit: int = Query(12), min_stars: int = Query(50)):
    try:
        return ok(stat_service.lang_distribution(limit, min_stars))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/license-distribution")
@cache.cached()
def license_distribution(limit: int = Query(10), min_stars: int = Query(50)):
    try:
        return ok(stat_service.license_distribution(limit, min_stars))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/star-buckets")
@cache.cached()
def star_buckets():
    try:
        return ok(stat_service.star_buckets())
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/major-vs-normal")
@cache.cached()
def major_vs_normal(period_type: str = Query("week")):
    try:
        return ok(stat_service.major_vs_normal(period_type))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/recent-releases")
@cache.cached()
def recent_releases(limit: int = Query(10)):
    try:
        return ok(stat_service.recent_releases(limit))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/top-by-lang")
@cache.cached()
def top_by_lang(limit: int = Query(3), langs: int = Query(8), min_stars: int = Query(50)):
    try:
        return ok(stat_service.top_by_lang(limit, langs, min_stars))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/dashboard")
@cache.cached()
def dashboard():
    try:
        return ok(stat_service.dashboard())
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/screen")
@cache.cached()
def screen(exclude_major: bool = False, year: Optional[int] = Query(None)):
    """大屏聚合接口（用户 2026-10 要求）。

    把首页需要的**共享数据**合并成一次请求：概览 / 周趋势 / 语言分布 / 协议分布 /
    star 分桶 / 最新版本 / 近 7 天飙升。首页因此从 11 个 GET 降到 ~5 个，
    省掉 HTTP 往返与连接池排队；聚合结果整体走同一份缓存，普通用户与管理员共用。

    不含：`/api/ranks`（首页可点柱切换周榜，单独走自己的缓存）、
    `/api/system/health`、`/api/system/rate-limit`（实时状态，不可缓存）、
    `/api/watch/list`（用户相关数据，缓存会串号）。
    """
    try:
        return ok({
            "overview": stat_service.overview(),
            "weekly_trend": stat_service.weekly_trend(exclude_major, 60, year),
            "lang_distribution": stat_service.lang_distribution(12, 50),
            "license_distribution": stat_service.license_distribution(10, 50),
            "star_buckets": stat_service.star_buckets(),
            "recent_releases": stat_service.recent_releases(10),
            "movers": rank_service.live_rank("rolling7", "gain", None, page_size=3,
                                             min_stars=50, exclude_major=exclude_major),
        })
    except Exception as e:  # noqa: BLE001
        return fail(str(e))
