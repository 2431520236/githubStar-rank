"""GitHub Search 全量收口:按 star 区间递归拆半,直到每段 ≤1000 条。

对应手册《05 · 采集器与多线程机制》6. search_harvester.py。
Search 结果自带 star/语言/简介/推送时间,直接入库,无需逐仓详情请求。
"""
import logging
import threading
import time
from datetime import date, timedelta
from typing import Any, Optional

from .. import db
from ..services import repo_service
from ..utils import as_int
from . import github_client

log = logging.getLogger("ghradar.harvester")

_PACE_S = 2.1  # 控制 Search 请求 < 30 次/分
_pace_lock = threading.Lock()
_last_at = 0.0


def _pace() -> None:
    global _last_at
    with _pace_lock:
        delta = time.time() - _last_at
        if delta < _PACE_S:
            time.sleep(_PACE_S - delta)
        _last_at = time.time()


def _map(item: dict) -> dict:
    """Search 结果 → gh_repo 行。"""
    owner = (item.get("owner") or {}).get("login") or ""
    lic = item.get("license") or {}
    return {
        "full_name": item.get("full_name") or "",
        "owner": owner,
        "name": item.get("name") or "",
        "html_url": item.get("html_url") or "",
        "api_url": item.get("url"),
        "homepage": item.get("homepage"),
        "description": item.get("description"),
        "lang": item.get("language"),
        "license_spdx": lic.get("spdx_id") if lic.get("spdx_id") not in (None, "NOASSERTION") else None,
        "license_name": lic.get("name"),
        "topics": item.get("topics") or [],
        "stars": item.get("stargazers_count") or 0,
        "forks": item.get("forks_count") or 0,
        "watchers": item.get("watchers_count") or 0,
        "open_issues": item.get("open_issues_count") or 0,
        "size_kb": item.get("size") or 0,
        "default_branch": item.get("default_branch"),
        "is_archived": bool(item.get("archived")),
        "is_fork": bool(item.get("fork")),
        "repo_created_at": item.get("created_at"),
        "repo_pushed_at": item.get("pushed_at"),
        "repo_updated_at": item.get("updated_at"),
        "sources": ["github_search"],
        "seed_metrics": {"search_stars": item.get("stargazers_count") or 0},
    }


def _harvest_range(lo: int, hi: int, since: str, sink: list, depth: int = 0) -> None:
    """按 star 区间递归拆半收口。"""
    cli = github_client.get_client()
    q = f"stars:{lo}..{hi} pushed:>={since} fork:false archived:false"
    page = 1
    got = 0
    while page <= 10:  # 每查询最多 1000 条
        if github_client.RATE.exhausted():
            log.warning("额度耗尽,停止收口")
            return
        _pace()
        data = cli.search_repos(q, per_page=100, page=page)
        items = data.get("items") or []
        total = as_int(data.get("total_count"))
        if not items:
            break
        for it in items:
            sink.append(_map(it))
        got += len(items)
        if total <= 1000 or len(items) < 100:
            break
        page += 1
    if got >= 1000 and depth < 40 and hi > lo:
        mid = (lo + hi) // 2
        _harvest_range(lo, mid, since, sink, depth + 1)
        _harvest_range(mid + 1, hi, since, sink, depth + 1)
    elif got >= 1000 and depth >= 40:
        log.warning("深度超限(>40),取前 1000 条:stars:%s..%s", lo, hi)


def harvest(min_stars: int = 50, max_age_days: int = 1095, hi_bound: int = 5_000_000) -> dict:
    since = (date.today() - timedelta(days=as_int(max_age_days, 1095))).isoformat()
    sink: list[dict] = []
    _harvest_range(as_int(min_stars, 50), hi_bound, since, sink)
    # 去重(同仓可能落多段)
    uniq: dict[str, dict] = {}
    for r in sink:
        uniq[r["full_name"]] = r
    rows = [v for k, v in uniq.items() if "/" in k]
    rep = repo_service.upsert_repos_report(rows)
    return {"upserted": rep["upserted"], "new": rep["new"], "since": since,
            "scanned": len(sink)}
