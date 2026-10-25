"""仓库详情采集(含参与者、版本、可选 README)。

对应手册《05 · 采集器与多线程机制》3. repo_collector.py。
"""
import logging
from typing import Optional, Sequence

from .. import config as cfg
from .. import db
from ..services import repo_service
from ..utils import as_int
from . import base, github_client

log = logging.getLogger("ghradar.collector.repo")


def _collect_one(full_name: str, with_readme: bool = False, with_contributors: bool = True,
                 with_release: bool = True) -> dict:
    cli = github_client.get_client()
    g = cli.repo(full_name)
    if not g:
        raise RuntimeError(f"仓库不存在或无权限: {full_name}")
    row = github_client.GitHubClient.normalize(g)

    if with_release:
        rel = cli.latest_release(full_name)
        if rel:
            row["latest_version"] = rel.get("tag_name")
            row["version_note"] = (rel.get("name") or "")[:500] or None
            row["latest_release_at"] = rel.get("published_at")
        else:
            tags = cli.tags(full_name, limit=1)
            if tags:
                row["latest_version"] = tags[0].get("name")

    n = repo_service.upsert_repos([row])
    rid = db.query_scalar("SELECT id FROM gh_repo WHERE full_name = %s", [full_name])

    if with_contributors:
        cs = cli.contributors(full_name, limit=30)
        if cs:
            repo_service.save_contributors(rid, cs)
            db.execute("UPDATE gh_repo SET contributors_count = %s WHERE id = %s", [len(cs), rid])

    if with_release:
        rels = cli.releases(full_name, limit=10)
        if rels:
            repo_service.save_releases(rid, rels)

    if with_readme:
        collect_readme_for(full_name, row.get("default_branch"))

    db.execute("UPDATE gh_repo SET detail_synced_at = now(), updated_at = now() WHERE id = %s", [rid])
    return {"full_name": full_name, "id": rid, "upserted": n}


def collect_details(limit: int = 100, ids: Optional[Sequence[int]] = None, only_missing: bool = True,
                    concurrency: int = 6, with_readme: bool = False, with_contributors: bool = True,
                    with_release: bool = True) -> dict:
    # 没配 token → 同步任务立即跳过(不报错); 页面/库里已有数据不受影响
    if not cfg.has_github_token():
        return {"ok": 0, "failed": 0, "total": 0, "errors": [],
                "skipped": True, "reason": "未配置 GitHub Token"}
    cands = repo_service.candidates_for_detail(limit, ids, only_missing)
    names = [c["full_name"] for c in cands]

    def work(fn: str) -> dict:
        return _collect_one(fn, with_readme, with_contributors, with_release)

    out = base.run_threaded(names, work, concurrency=concurrency)
    return {"ok": out["ok"], "failed": out["failed"], "total": out["total"],
            "errors": out["errors"][:20]}


def collect_readme_for(full_name: str, path: Optional[str] = None) -> str:
    """取 README 原文(供 AI 概要复用);走 raw CDN,不计 API 额度。"""
    cli = github_client.get_client()
    branch = path or "HEAD"
    txt = cli.readme_raw(full_name, branch)
    if not txt:
        txt = cli.readme(full_name)
    return txt or ""
