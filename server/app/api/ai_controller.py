"""模型与任务接口(prefix /api/ai)。

对应手册《03 · 后端 API 接口》6. ai.py。
"""
from typing import Optional

from fastapi import APIRouter, Query

from ..services import llm_config, llm_service, job_service
from ..utils import fail, ok
from . import schemas as S

router = APIRouter(prefix="/api/ai", tags=["ai"])


# ---------------- 供应商 ----------------
@router.get("/providers")
def providers():
    return ok(llm_config.list_providers(mask=True))


@router.post("/providers")
def add_provider(req: S.ProviderReq):
    try:
        return ok(llm_config.add_provider(req.model_dump(exclude_none=True)))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.put("/providers/{pid}")
def update_provider(pid: int, req: S.ProviderReq):
    try:
        row = llm_config.update_provider(pid, req.model_dump(exclude_none=True))
        return ok(row) if row else fail("供应商不存在")
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.delete("/providers/{pid}")
def delete_provider(pid: int):
    return ok({"deleted": llm_config.delete_provider(pid)})


@router.post("/providers/{pid}/test")
def test_provider(pid: int):
    try:
        return ok(llm_config.test_provider(pid))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/providers/{pid}/remote-models")
def remote_models(pid: int):
    try:
        return ok(llm_config.fetch_remote_models(pid))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/pull-models")
def pull_models(req: S.ProviderReq):
    """按传入配置实时拉取远端模型(未保存也可)。

    已保存的供应商只需传 id —— 密钥回落到后端已存的那份(前端拿不到密钥)。
    """
    try:
        cfg = llm_config.resolve_provider(req.model_dump(exclude_none=True))
        return ok(llm_config.probe_models(cfg))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


# ---------------- 模型 ----------------
@router.get("/models")
def models():
    return ok(llm_config.list_models())


@router.post("/models")
def add_model(req: S.ModelReq):
    try:
        return ok(llm_config.save_model(req.model_dump()))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.put("/models/{mid}")
def update_model(mid: int, req: S.ModelReq):
    try:
        payload = req.model_dump()
        payload["id"] = mid
        # 直接改指定模型
        for p in llm_config.list_providers(mask=False):
            for m in p.get("models", []):
                if int(m["id"]) == int(mid):
                    payload["provider_id"] = p["id"]
                    return ok(llm_config.save_model(payload))
        return fail("模型不存在")
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.delete("/models/{mid}")
def delete_model(mid: int):
    return ok({"deleted": llm_config.delete_model(mid)})


# ---------------- 任务 ----------------
@router.get("/tasks")
def tasks():
    return ok(llm_service.list_tasks())


@router.post("/tasks")
def add_task(req: S.TaskReq):
    try:
        return ok(llm_service.save_task(req.model_dump(exclude_none=True)))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.put("/tasks/{tid}")
def update_task(tid: int, req: S.TaskReq):
    try:
        payload = req.model_dump(exclude_none=True)
        payload["id"] = tid
        return ok(llm_service.save_task(payload))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/tasks/run")
def run_task(req: S.TaskRunReq):
    try:
        with job_service.job(f"ai_{req.task_key}", threads=req.concurrency) as box:
            res = llm_service.run_task(req.task_key, req.model_id, req.limit, req.ids,
                                       req.concurrency, req.group_size, req.allow_overwrite)
            box["result"] = {"ok": res.get("processed", 0), "failed": res.get("failed", 0),
                             "total": res.get("processed", 0)}
        return ok(res)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/runs")
def runs(limit: int = Query(100), task_key: Optional[str] = None, status: Optional[str] = None):
    return ok(llm_service.list_runs(limit, task_key, status))


@router.get("/run-stats")
def run_stats():
    return ok(llm_service.run_stats())


@router.get("/default-model")
def default_model():
    return ok(llm_config.get_default_model())
