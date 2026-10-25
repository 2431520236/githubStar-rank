"""任务日志服务:gh_job_log 上下文管理器(所有采集/统计任务统一走这里)。

对应手册《04 · 后端服务层逻辑》6. job_service。
"""
import contextlib
import logging
import time
import traceback
from typing import Any, Optional

from .. import db
from ..utils import as_int
from .. import cache

log = logging.getLogger("ghradar.job")


@contextlib.contextmanager
def job(name: str, trigger: str = "manual", threads: int = 1, re_raise: bool = False,
        **stats: Any):
    """进入写 running,退出按结果写 success/partial/failed。

    任务收尾（无论成功/失败）统一清一次页面查询缓存 —— 用户 2026-10 口径:
    「触发任务的话,任务执行结束清理一次缓存即可」。
    """
    started = time.time()
    job_id: Optional[int] = None
    try:
        rows = db.execute(
            "INSERT INTO gh_job_log (job_name, trigger_type, status, threads, stats, started_at) "
            "VALUES (%s, %s, 'running', %s, %s, now()) RETURNING id",
            [name, trigger, as_int(threads, 1), db.Json(stats or {})], returning=True)
        job_id = rows[0]["id"] if rows else None
    except Exception:  # noqa: BLE001
        log.warning("写 gh_job_log 失败", exc_info=True)

    box: dict[str, Any] = {"result": None}
    try:
        yield box
    except Exception as e:  # noqa: BLE001
        box["error"] = str(e)
        _finish(job_id, "failed", started, message=str(e), stats=box.get("stats"))
        if re_raise:
            raise
        log.error("任务 %s 失败: %s", name, e)
        log.debug(traceback.format_exc())
    else:
        res = box.get("result") or {}
        failed = as_int(res.get("failed", 0)) if isinstance(res, dict) else 0
        ok_n = as_int(res.get("ok", 0)) if isinstance(res, dict) else 0
        status = "partial" if (failed and ok_n) else ("failed" if (failed and not ok_n) else "success")
        _finish(job_id, status, started,
                total=as_int(res.get("total", 0)) if isinstance(res, dict) else 0,
                succeeded=ok_n, failed=failed,
                message=(res.get("message") if isinstance(res, dict) else None),
                stats=(res.get("stats") if isinstance(res, dict) else None) or box.get("stats"))
    finally:
        # 任务结束(成功或失败) → 清一次页面查询缓存
        try:
            cache.invalidate()
        except Exception:  # noqa: BLE001
            log.warning("清缓存失败", exc_info=True)


def _finish(job_id: Optional[int], status: str, started: float, total: int = 0,
            succeeded: int = 0, failed: int = 0, message: Optional[str] = None,
            stats: Any = None) -> None:
    duration_ms = int((time.time() - started) * 1000)
    if job_id is None:
        return
    with contextlib.suppress(Exception):
        db.execute("""
            UPDATE gh_job_log SET status = %s, total = %s, succeeded = %s, failed = %s,
                   duration_ms = %s, message = %s, stats = COALESCE(%s, stats), finished_at = now()
            WHERE id = %s
        """, [status, total, succeeded, failed, duration_ms, message,
              db.Json(stats) if stats else None, job_id])


def recent(limit: int = 50) -> list[dict]:
    return db.query_all(
        "SELECT id, job_name, trigger_type, status, total, succeeded, failed, threads, "
        "duration_ms, message, started_at, finished_at FROM gh_job_log "
        "ORDER BY started_at DESC LIMIT %s", [as_int(limit, 50)])


def last_run_at(job_name: str):
    return db.query_scalar(
        "SELECT max(started_at) FROM gh_job_log WHERE job_name = %s AND status <> 'running'",
        [job_name])


def abort_stale() -> int:
    """启动时把残留 running 标记为 failed。"""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE gh_job_log SET status = 'failed', finished_at = now(), "
                        "message = COALESCE(message, '进程重启中断') WHERE status = 'running'")
            n = cur.rowcount
        conn.commit()
    return n
