"""榜单 / 快照 / 白名单接口(prefix /api)。

查询类 GET 接口全部加 TTL 缓存(用户 2026-10 定):
  - 榜单查询最少 5 分钟缓存, **不同查询条件各自缓存**(缓存 key 含全部查询参数);
  - 普通用户与管理员看的是同一份数据, 走同一个缓存, 权限差异只体现在页面显隐。
"""
from typing import Optional

from fastapi import APIRouter, Query

from .. import config as cfg
from ..services import rank_service, snapshot_service
from ..utils import csv_list, fail, ok
from .. import cache
from . import schemas as S

router = APIRouter(prefix="/api", tags=["ranks"])


@router.get("/ranks")
@cache.cached()
def ranks(period_type: str = Query("week"), metric: str = Query("gain"),
          period_key: Optional[str] = None, q: Optional[str] = None, langs: Optional[str] = None,
          license: Optional[str] = None, min_stars: Optional[int] = None,
          max_age_days: Optional[int] = None,
          exclude_major: bool = False, sort: Optional[str] = None, order: str = "desc",
          page: int = Query(1), page_size: int = Query(100), top_n: Optional[int] = None):
    try:
        return ok(rank_service.live_rank(period_type, metric, period_key, q=q,
                                         langs=csv_list(langs) or None, license_spdx=license,
                                         min_stars=min_stars, max_age_days=max_age_days,
                                         exclude_major=exclude_major,
                                         sort=sort, order=order, page=page,
                                         page_size=page_size, top_n=top_n))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/ranks/periods")
@cache.cached()
def rank_periods(period_type: str = Query("week"), limit: int = Query(60)):
    return ok(rank_service.list_periods(period_type, limit))


@router.get("/ranks/snapshot-periods")
@cache.cached()
def snapshot_periods(period_type: str = Query("week"), metric: Optional[str] = None,
                     limit: int = Query(100)):
    return ok(rank_service.snapshot_periods(period_type, metric, limit))


@router.get("/ranks/snapshot")
@cache.cached()
def snapshot_get(period_type: str = Query("week"), period_key: Optional[str] = None,
                 metric: str = Query("total"), page: int = Query(1),
                 page_size: int = Query(100)):
    try:
        return ok(rank_service.get_snapshot(period_type, period_key, metric, page, page_size))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/ranks/stat-periods")
@cache.cached()
def stat_periods(period_type: Optional[str] = None, limit: int = Query(100)):
    return ok(rank_service.stat_periods(period_type, limit))


@router.post("/ranks/compute")
def ranks_compute(req: S.ComputeReq):
    try:
        out = snapshot_service.compute_rank(req.period_type, req.period_key, req.metric,
                                            req.force, req.min_stars, req.exclude_major)
        cache.invalidate()
        return ok(out)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/ranks/ensure")
def ranks_ensure(req: S.EnsureReq):
    try:
        out = snapshot_service.ensure_period(req.period_type, req.metric, req.period_key,
                                             req.min_stars, req.exclude_major, req.collect,
                                             req.max_collect)
        cache.invalidate()
        return ok(out)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/ranks/compute-all")
def ranks_compute_all(start_date: Optional[str] = None, min_stars: Optional[int] = None):
    try:
        out = snapshot_service.compute_all_ranks(start_date, min_stars)
        cache.invalidate()
        return ok(out)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


# ---------------- 白名单 ----------------
@router.get("/whitelist")
@cache.cached()
def whitelist(limit: int = Query(200), list_type: Optional[str] = None):
    return ok(rank_service.whitelist_list(limit, list_type))


@router.post("/whitelist")
def whitelist_add(req: S.WhitelistReq):
    try:
        return ok(rank_service.whitelist_add(req.full_name, req.list_type, req.note))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/whitelist/delete")
def whitelist_delete(req: S.WhitelistDelReq):
    try:
        return ok({"deleted": rank_service.whitelist_remove(req.ids)})
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/whitelist/{wid}/toggle")
def whitelist_toggle(wid: int, enabled: bool = Query(True)):
    try:
        return ok(rank_service.whitelist_toggle(wid, enabled))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))
