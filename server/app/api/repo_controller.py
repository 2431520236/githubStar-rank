"""仓库接口(prefix /api/repos)。

对应手册《03 · 后端 API 接口》2. repos.py。
"""
from typing import Optional

from fastapi import APIRouter, Query

from ..services import repo_service
from ..utils import as_int, csv_list, fail, ok
from .. import cache
from .. import config as cfg
from . import schemas as S

router = APIRouter(prefix="/api/repos", tags=["repos"])


@router.get("")
@router.get("/")
@cache.cached()
def list_repos(
    q: Optional[str] = None,
    lang: Optional[str] = None,
    langs: Optional[str] = None,
    license: Optional[str] = None,
    topic: Optional[str] = None,
    signal: Optional[str] = None,
    source: Optional[str] = None,
    min_stars: Optional[int] = None,
    max_age_days: Optional[int] = None,
    min_gain: Optional[int] = None,
    major: Optional[bool] = None,
    archived: Optional[bool] = None,
    has_summary: Optional[bool] = None,
    sort: str = "stars",
    order: str = "desc",
    page: int = 1,
    page_size: Optional[int] = None,
    top_n: Optional[int] = None,
):
    try:
        return ok(repo_service.list_repos(
            q=q, lang=lang, langs=csv_list(langs) or None, license_spdx=license, topic=topic,
            source=source, min_stars=min_stars, max_age_days=max_age_days, min_gain=min_gain,
            major=major, archived=archived, has_summary=has_summary, sort=sort, order=order,
            page=page, page_size=page_size, top_n=top_n))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/batch")
def batch(req: S.BatchReq):
    try:
        rows = repo_service.batch_get(req.ids, req.names)
        return ok({"items": rows, "total": len(rows), "page": 1,
                   "page_size": len(rows) or 1, "pages": 1})
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/meta/langs")
@cache.cached()
def meta_langs(min_stars: int = Query(50), limit: int = Query(50)):
    return ok(repo_service.list_langs(min_stars, limit))


@router.get("/meta/licenses")
@cache.cached()
def meta_licenses(min_stars: int = Query(50), limit: int = Query(50)):
    return ok(repo_service.list_licenses(min_stars, limit))


@router.get("/{full_name:path}")
@cache.cached()
def repo_detail(full_name: str):
    try:
        row = repo_service.get_repo(full_name)
        if not row:
            return fail(f"仓库不存在: {full_name}")
        return ok(row)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.get("/{full_name:path}/daily")
@cache.cached()
def repo_daily(full_name: str, limit: int = Query(400)):
    rid = _rid(full_name)
    if rid is None:
        return fail(f"仓库不存在: {full_name}")
    from ..services import facts_service
    return ok(facts_service.repo_daily_series(rid, limit))


@router.get("/{full_name:path}/weekly")
@cache.cached()
def repo_weekly(full_name: str, limit: int = Query(400)):
    from .. import db
    rows = db.query_all("""
        SELECT w.week_start, w.week_key, w.week_label, w.weekly_new, w.cumulative
        FROM gh_repo_star_weekly w JOIN gh_repo r ON r.id = w.repo_id
        WHERE r.full_name = %s ORDER BY w.week_start DESC LIMIT %s
    """, [full_name, as_int(limit, 400)])
    return ok(rows)


@router.get("/{full_name:path}/contributors")
@cache.cached()
def repo_contributors(full_name: str, limit: int = Query(20)):
    from .. import db
    rows = db.query_all("""
        SELECT c.login, c.avatar_url, c.html_url, c.contributions, c.rank_no
        FROM gh_repo_contributor c JOIN gh_repo r ON r.id = c.repo_id
        WHERE r.full_name = %s ORDER BY c.rank_no NULLS LAST, c.contributions DESC LIMIT %s
    """, [full_name, as_int(limit, 20)])
    return ok(rows)


@router.get("/{full_name:path}/releases")
@cache.cached()
def repo_releases(full_name: str, limit: int = Query(20)):
    from .. import db
    rows = db.query_all("""
        SELECT rel.tag_name, rel.name, rel.body, rel.published_at, rel.is_prerelease, rel.is_latest
        FROM gh_repo_release rel JOIN gh_repo r ON r.id = rel.repo_id
        WHERE r.full_name = %s ORDER BY rel.published_at DESC NULLS LAST LIMIT %s
    """, [full_name, as_int(limit, 20)])
    return ok(rows)


@router.post("/{full_name:path}/refresh")
def repo_refresh(full_name: str, with_readme: bool = Query(True),
                 with_contributors: bool = Query(True)):
    from ..collectors import repo_collector
    from ..services import job_service
    if not cfg.has_github_token():
        return fail("未配置 GitHub Token，同步任务已跳过（页面数据照常展示）")
    try:
        with job_service.job("repo_refresh") as box:
            res = repo_collector._collect_one(full_name, with_readme, with_contributors, True)
            box["result"] = {"ok": 1, "total": 1}
        return ok(res)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


@router.post("/{full_name:path}/refresh-history")
def repo_refresh_history(full_name: str):
    from ..collectors import history_collector
    from ..services import job_service
    if not cfg.has_github_token():
        return fail("未配置 GitHub Token，同步任务已跳过（页面数据照常展示）")
    try:
        with job_service.job("repo_refresh_history") as box:
            res = history_collector.collect_one(full_name)
            box["result"] = {"ok": 1, "total": 1}
        return ok(res)
    except Exception as e:  # noqa: BLE001
        return fail(str(e))


def _rid(full_name: str):
    from .. import db
    return db.query_scalar("SELECT id FROM gh_repo WHERE full_name = %s", [full_name])
