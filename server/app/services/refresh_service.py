"""一键补全 / 立即刷新(需求 R12)。

对应手册《04 · 后端服务层逻辑》8. refresh_service。
三步:star 历史 → GitHub 收口 → 缺口批量补全。
"""
import logging

from .. import config as cfg
from ..utils import as_int
from . import backfill_service, job_service

log = logging.getLogger("ghradar.refresh")


def run_refresh(star_limit: int = 1000, full_history: bool = False, harvest_flag: bool = True,
                min_stars: int = 50, max_age_days: int = 1095, backfill: bool = True,
                ai_group: int = 15, ai_concurrency: int = 10) -> dict:
    # 没配 token → 一键刷新整体跳过(三步全靠 GitHub API); 页面数据不受影响
    if not cfg.has_github_token():
        return {"skipped": True, "reason": "未配置 GitHub Token"}
    steps = {}
    with job_service.job("refresh", trigger="manual", threads=6) as box:
        # 1) star 历史
        try:
            from ..collectors import history_collector
            if full_history:
                steps["history"] = history_collector.collect_all_history(limit=star_limit, concurrency=3)
            else:
                steps["history"] = history_collector.collect_history(
                    limit=star_limit, concurrency=3, refresh_after_days=0, only_missing=False)
        except Exception as e:  # noqa: BLE001
            steps["history"] = {"error": str(e)}

        # 2) GitHub 收口
        if harvest_flag:
            try:
                from ..collectors import search_harvester
                steps["harvest"] = search_harvester.harvest(min_stars=min_stars,
                                                            max_age_days=max_age_days)
            except Exception as e:  # noqa: BLE001
                steps["harvest"] = {"error": str(e)}

        # 3) 缺口批量补全
        if backfill:
            try:
                steps["backfill"] = backfill_service.run_until_complete(
                    detail_limit=100, readme_limit=400, ai_group=ai_group,
                    ai_limit=300, ai_concurrency=ai_concurrency)
            except Exception as e:  # noqa: BLE001
                steps["backfill"] = {"error": str(e)}

        steps["gaps"] = backfill_service.gaps()
        box["result"] = {"ok": 1, "failed": 0, "total": 1, "stats": steps}
    return {"ok": True, "steps": steps}
