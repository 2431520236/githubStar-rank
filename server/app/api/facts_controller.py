"""历史事实接口(prefix /api/history)。

对应手册《03 · 后端 API 接口》3. facts.py。
"""
from typing import Optional

from fastapi import APIRouter, Query

from ..services import facts_service, job_service, scheduler
from ..utils import csv_list, fail, ok
from .. import cache
from .. import config as cfg
from . import schemas as S

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/daily")
@cache.cached()
def daily(stat_date: Optional[str] = None, repo_id: Optional[int] = None,
          stat_date_from: Optional[str] = None, stat_date_to: Optional[str] = None,
          repo_ids: Optional[str] = None, full_names: Optional[str] = None,
          order: str = "desc",
          limit: int = Query(500), page: int = Query(1)):
    """每日 star 事实查询(支持多项目 + 多天 + 分页 500)。

    - 单日:      ?stat_date=2026-10-08
    - 日期区间:  ?stat_date_from=2026-09-01&stat_date_to=2026-10-08
    - 单项目:    ?repo_id=123
    - 多项目:    ?repo_ids=1,2,3   或   ?full_names=owner/a,owner/b
    - 排序分页:  ?order=asc|desc&limit=500&page=N   (limit 上限 5000)
    """
    try:
        return ok(facts_service.query_daily(
            stat_date, repo_id, limit, page,
            stat_date_from=stat_date_from, stat_date_to=stat_date_to,
            repo_ids=csv_list(repo_ids) or None,
            full_names=csv_list(full_names) or None,
            order=order))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/daily/yesterday")
@cache.cached()
def daily_yesterday(limit: int = Query(30)):
    try:
        return ok(facts_service.yesterday_gain(limit))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/weekly")
@cache.cached()
def weekly(week_start: Optional[str] = None, repo_id: Optional[int] = None,
           week_from: Optional[str] = None, week_to: Optional[str] = None,
           repo_ids: Optional[str] = None, full_names: Optional[str] = None,
           order: str = "desc",
           limit: int = Query(500), page: int = Query(1)):
    """每周 star 事实查询(参数语义同 /daily)。"""
    try:
        return ok(facts_service.query_weekly(
            week_start, repo_id, limit, page,
            week_from=week_from, week_to=week_to,
            repo_ids=csv_list(repo_ids) or None,
            full_names=csv_list(full_names) or None,
            order=order))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/summary")
@cache.cached()
def summary():
    try:
        return ok(facts_service.summary_stats())
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/jobs")
def history_jobs():
    tasks = [t for t in scheduler.scheduler.status()
             if t["name"] in ("collect_history", "daily_snapshot", "rollup")]
    return ok(tasks)


@router.post("/collect")
def collect(req: S.HistReq):
    from ..collectors import history_collector
    try:
        with job_service.job("collect_history", threads=req.concurrency) as box:
            res = history_collector.collect_history(req.limit, req.concurrency,
                                                    req.refresh_after_days, req.only_missing,
                                                    req.ids)
            box["result"] = res
        return ok(res)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/snapshot")
def snapshot(req: S.SnapshotReq):
    """已废弃(2026-10): 手动「当日快照」是混入源, 会让假增量再次入库。

    star 历史唯一数据源 = 官方 stargazers/history, 走 POST /api/facts/collect-history。
    这里保留端点名只为不让老前端报 404, 直接回滚量指标重算。
    """
    try:
        facts_service.rollup_gains()
        return ok({"deprecated": True,
                   "message": "已废弃: 请用 /api/facts/collect-history 采集官方历史"})
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/import")
def import_daily():
    return ok({"message": "请使用 POST /api/system/collect/seed(import_history=true) 导入本地历史"})


@router.post("/import-weekly")
def import_weekly():
    return ok({"message": "请使用 POST /api/system/collect/seed(import_history=true) 导入周聚合"})