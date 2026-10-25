"""缺口补齐服务:详情 / README / AI 概要 缺口循环补齐。

对应手册《04 · 后端服务层逻辑》9. backfill_service。
"""
import logging
from typing import Optional

from .. import config as cfg
from .. import db
from ..utils import as_int
from . import job_service

log = logging.getLogger("ghradar.backfill")


def gaps() -> dict:
    return {
        "detail": db.query_scalar("SELECT count(*) FROM gh_repo WHERE detail_synced_at IS NULL") or 0,
        "readme": db.query_scalar("SELECT count(*) FROM gh_repo WHERE readme_synced_at IS NULL") or 0,
        "ai": db.query_scalar("SELECT count(*) FROM gh_repo WHERE stars >= 50 "
                              "AND COALESCE(ai_summary,'') = '' "
                              "AND COALESCE(ai_summary_tries,0) < 3") or 0,
        "hist_empty": db.query_scalar("SELECT count(*) FROM gh_repo WHERE history_synced_at IS NULL") or 0,
    }


def is_complete() -> bool:
    g = gaps()
    return g["detail"] == 0 and g["readme"] == 0 and g["ai"] == 0


def run_round(detail_limit: int = 100, readme_limit: int = 400, ai_group: int = 15,
              ai_limit: int = 300, ai_concurrency: int = 10, fix_history: bool = True) -> dict:
    """一轮顺序补:README → 详情 → 历史空数据修复(仅一次) → AI 概要。"""
    from ..collectors import readme_collector, repo_collector
    from . import llm_service
    # 没配 token → 补全整体跳过(详情/历史都依赖 GitHub API); 页面数据不受影响
    if not cfg.has_github_token():
        return {"skipped": True, "reason": "未配置 GitHub Token", "gaps": gaps()}
    out = {}
    try:
        out["readme"] = readme_collector.collect_readmes(limit=readme_limit)
    except Exception as e:  # noqa: BLE001
        out["readme"] = {"error": str(e)}
    try:
        out["detail"] = repo_collector.collect_details(limit=detail_limit, only_missing=True,
                                                       concurrency=6, with_contributors=True,
                                                       with_release=True)
    except Exception as e:  # noqa: BLE001
        out["detail"] = {"error": str(e)}
    if fix_history:
        try:
            from ..collectors import history_collector
            out["history"] = history_collector.collect_history(limit=50, concurrency=3,
                                                               refresh_after_days=7)
        except Exception as e:  # noqa: BLE001
            out["history"] = {"error": str(e)}
    try:
        out["ai"] = llm_service.run_task("repo_ai_summary", limit=ai_limit, group_size=ai_group,
                                         concurrency=ai_concurrency, allow_overwrite=False)
    except Exception as e:  # noqa: BLE001
        out["ai"] = {"error": str(e)}
    out["gaps"] = gaps()
    return out


def run_until_complete(max_rounds: int = 20, **kw) -> dict:
    rounds = 0
    with job_service.job("backfill", trigger="manual") as box:
        while rounds < max_rounds and not is_complete():
            rounds += 1
            log.info("缺口补齐第 %s 轮", rounds)
            run_round(**kw)
        box["result"] = {"ok": rounds, "failed": 0, "total": rounds, "stats": {"gaps": gaps()}}
    return {"rounds": rounds, "gaps": gaps()}


def run_ai_until_complete(ai_group: int = 15, ai_concurrency: int = 10, ai_limit: int = 300,
                          max_rounds: int = 20) -> dict:
    from . import llm_service
    rounds = 0
    while rounds < max_rounds and gaps()["ai"] > 0:
        rounds += 1
        llm_service.run_task("repo_ai_summary", limit=ai_limit, group_size=ai_group,
                             concurrency=ai_concurrency, allow_overwrite=False)
    return {"rounds": rounds, "gaps": gaps()}
