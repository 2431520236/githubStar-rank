"""关注提醒接口(prefix /api/watch)。

对应手册《03 · 后端 API 接口》7. watch.py。
"""
from typing import Optional

from fastapi import APIRouter, Query

from ..services import watch_service
from ..utils import fail, ok
from . import schemas as S

router = APIRouter(prefix="/api/watch", tags=["watch"])


@router.get("/list")
def list_watches(limit: int = Query(200)):
    return ok(watch_service.list_watches(limit))


@router.post("/add")
def add(req: S.WatchAddReq):
    try:
        return ok(watch_service.add_watch(req.full_name, req.watch_type, req.threshold,
                                          req.interval_min, req.webhook_url, req.note,
                                          req.channels))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.put("/{wid}")
def update(wid: int, req: S.WatchUpdateReq):
    try:
        return ok(watch_service.update_watch(wid, **req.model_dump(exclude_none=True)))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.delete("/{wid}")
def remove(wid: int):
    try:
        return ok({"deleted": watch_service.remove_watch(wid)})
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/check")
def check(watch_id: Optional[int] = Query(None), force: bool = Query(True)):
    try:
        return ok(watch_service.check_watches(force, watch_id))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/events")
def events(unread_only: bool = False, limit: int = Query(100)):
    return ok(watch_service.list_events(unread_only, limit))


@router.post("/events/read")
def mark_read(req: S.ReadReq):
    try:
        return ok({"updated": watch_service.mark_read(req.all, req.ids)})
    except Exception as e:  # noqa: BLE001
        return fail(str(e))
