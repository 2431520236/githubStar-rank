"""系统 / 采集 / 调度接口(prefix /api/system)。

对应手册《03 · 后端 API 接口》1. system.py。
"""
import logging
import threading
from typing import Optional

from fastapi import APIRouter, Query

from .. import config as cfg
from .. import db
from ..collectors import github_client
from ..services import backfill_service, job_service, refresh_service, repo_service, scheduler
from ..utils import as_int, fail, ok
from .. import cache
from .. import anti_scrape
from .. import runtime_config
from . import schemas as S

log = logging.getLogger("ghradar.api.system")
router = APIRouter(prefix="/api/system", tags=["system"])

# 后台线程 + 单例标志防重入
_bg_lock = threading.Lock()
_bg_running: set[str] = set()


def _spawn_bg(name: str, fn, *args, **kwargs) -> dict:
    with _bg_lock:
        if name in _bg_running:
            return {"ok": False, "message": f"{name} 正在运行中,请稍候"}
        _bg_running.add(name)

    def wrap() -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log.error("后台任务 %s 失败", name, exc_info=True)
        finally:
            with _bg_lock:
                _bg_running.discard(name)
            # 后台任务结束 → 清一次页面查询缓存(用户 2026-10 口径)
            try:
                cache.invalidate()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=wrap, name=f"bg-{name}", daemon=True).start()
    return {"ok": True, "message": f"{name} 已在后台启动"}


@router.get("/health")
def health():
    h = db.health()
    return ok({
        "db": h,
        "database": h.get("database"),
        "server_version": h.get("version"),
        "timezone": h.get("timezone"),
        "web_dir_exists": cfg.WEB_DIR.exists(),
        "scheduler": cfg.SCHEDULER_ENABLED,
        # 是否配置了 GitHub Token: 前端顶栏据此提示「未配置 token, 同步任务已暂停」。
        # 只回布尔, 绝不回传 token 本身。
        "token_configured": cfg.has_github_token(),
    })


@router.get("/config")
def get_config():
    return ok(cfg.as_dict())


@router.get("/settings")
def list_settings():
    return ok(db.query_all("SELECT key, value, description, updated_at FROM gh_app_setting ORDER BY key"))


@router.post("/settings")
def update_setting(req: S.SettingReq):
    try:
        rows = db.execute("""
            INSERT INTO gh_app_setting (key, value, updated_at) VALUES (%s, %s, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            RETURNING *
        """, [req.key, db.Json(req.value)], returning=True)
        # 改反爬 / 调试开关后立即生效（跳过设置读取的 TTL）
        if req.key.startswith("anti_scrape."):
            anti_scrape.clear_settings_cache()
        if req.key.startswith("debug."):
            runtime_config.clear()
        return ok(rows[0] if rows else None)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/jobs")
def jobs():
    return ok(scheduler.scheduler.status())


@router.post("/jobs/{name}/run")
def run_job(name: str):
    return ok(scheduler.scheduler.run_task_now(name))


@router.get("/logs")
def logs(limit: int = Query(50)):
    return ok(job_service.recent(limit))


@router.get("/rate-limit")
def rate_limit():
    cli = github_client.get_client()
    snap = cli.rate_limit() or github_client.RATE.snapshot()
    return ok({"remaining": snap.get("remaining"), "limit": snap.get("limit"),
               "reset": snap.get("reset") or snap.get("reset_at"),
               "token_configured": bool(cfg.GITHUB_TOKEN)})


@router.post("/collect/seed")
def collect_seed(req: S.SeedReq):
    from ..collectors import seed_importer
    out: dict = {}
    try:
        if req.import_index:
            out["index"] = seed_importer.import_index(req.seed_dir)
        if req.import_history:
            out["history"] = seed_importer.import_local_history(req.seed_dir or "")
        if not req.import_index and not req.import_history:
            out["message"] = "未指定 import_index / import_history"
        return ok(out)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/collect/details")
def collect_details(req: S.DetailReq):
    from ..collectors import repo_collector
    try:
        with job_service.job("collect_details", threads=req.concurrency) as box:
            res = repo_collector.collect_details(req.limit, req.ids, req.only_missing,
                                                 req.concurrency, req.with_readme,
                                                 req.with_contributors, req.with_release)
            box["result"] = res
        return ok(res)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/collect/readme")
def collect_readme(req: S.ReadmeReq):
    from ..collectors import readme_collector
    try:
        with job_service.job("collect_readme", threads=req.concurrency) as box:
            res = readme_collector.collect_readmes(req.limit, None, req.concurrency,
                                                   req.refresh_after_days)
            box["result"] = res
        return ok(res)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/backfill/gaps")
def backfill_gaps():
    return ok({"gaps": backfill_service.gaps()})


@router.post("/harvest-search")
def harvest_search(min_stars: int = Query(50), max_age_days: int = Query(1095)):
    from ..collectors import search_harvester
    return ok(_spawn_bg("harvest", search_harvester.harvest, min_stars, max_age_days))


@router.post("/refresh")
def refresh(req: S.RefreshReq):
    return ok(_spawn_bg("refresh", refresh_service.run_refresh, req.star_limit, req.full_history,
                        req.harvest, req.min_stars, req.max_age_days, req.backfill,
                        req.ai_group, req.ai_concurrency))


@router.post("/backfill")
def backfill(full: bool = Query(False), ai_group: int = Query(15),
             ai_concurrency: int = Query(10)):
    if full:
        return ok(_spawn_bg("backfill", backfill_service.run_until_complete,
                            20, 100, 400, ai_group, 300, ai_concurrency, True))
    return ok(_spawn_bg("backfill_round", backfill_service.run_round,
                        100, 400, ai_group, 300, ai_concurrency, True))


@router.post("/refresh-major")
def refresh_major():
    return ok({"updated": repo_service.refresh_major_flags()})


@router.post("/cache/invalidate")
def invalidate_cache(pattern: Optional[str] = Query(None, description="可选模式: 清含此子串的键, 缺省=全清")):
    """手动使缓存失效。数据采集/rollup 后自动调用; 此端点用于管理员手动清缓存。"""
    n = cache.invalidate(pattern)
    return ok({"cleared": n, "remaining": cache.size()})


# ---------------- 调试开关（2026-10 用户要求：便于开发，不仅生产性能） ----------------
@router.get("/debug/status")
def debug_status():
    """一眼看清当前生效的开关 + 缓存运行状况（开发排查用）。

    - `flags`：每一层优化的当前开关（缓存/限流/压缩/304/静态缓存/调试头）
    - `cache`：条目数、命中/未命中/直通次数、命中率
    - `ttl`：默认视图与带条件查询的两档 TTL
    - `rate_limit`：同接口 / 全局限额
    """
    return ok({
        "flags": runtime_config.snapshot(),
        "flag_descriptions": runtime_config.DESCRIPTIONS,
        "cache": cache.stats(),
        "ttl": {"default": cfg.CACHE_TTL_DEFAULT, "conditional": cfg.CACHE_TTL_COND},
        "rate_limit": {"enabled": cfg.RATE_LIMIT_ENABLED,
                       "same_path_per_sec": cfg.RATE_LIMIT_PATH_PER_SEC,
                       "all_paths_per_sec": cfg.RATE_LIMIT_GLOBAL_PER_SEC},
    })


@router.get("/debug/cache/entries")
def debug_cache_entries(limit: int = Query(50)):
    """当前缓存条目（key 截断 + 剩余 TTL），用于确认「哪个接口真被缓存了」。"""
    rows = cache.entries()
    return ok({"total": len(rows), "items": rows[:max(1, min(int(limit), 512))]})


@router.post("/debug/cache/reset-stats")
def debug_cache_reset_stats():
    cache.reset_stats()
    return ok({"reset": True, "cache": cache.stats()})


@router.post("/debug/flags")
def debug_set_flag(req: S.SettingReq):
    """快速切换调试开关（等价于 POST /api/system/settings，但只允许 debug.* 且立即生效）。"""
    if not req.key.startswith("debug."):
        return fail("仅允许修改 debug.* 开关")
    rows = db.execute("""
        INSERT INTO gh_app_setting (key, value, description, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        RETURNING *
    """, [req.key, db.Json(req.value),
          runtime_config.DESCRIPTIONS.get(req.key, "")], returning=True)
    runtime_config.clear()
    return ok({"flags": runtime_config.snapshot(), "row": rows[0] if rows else None})


@router.get("/counts")
@cache.cached()
def counts():
    return ok(repo_service.counts())
