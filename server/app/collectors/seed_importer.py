"""种子导入:合并上游索引 JSON 入库 + 本地历史文件导入。

对应手册《05 · 采集器与多线程机制》7. seed_importer.py。
索引目录结构(seed_dir):
    repos.json / findarepo.json / search-merge.json / official.json
    history/owner__repo.json
"""
import json
import logging
from pathlib import Path
from typing import Optional

from .. import db
from ..services import facts_service, repo_service
from ..utils import as_int
from . import history_collector

log = logging.getLogger("ghradar.seed")

SOURCE_FILES = {
    "repofomo": "repos.json",
    "findarepo": "findarepo.json",
    "search": "search-merge.json",
    "official": "official.json",
}


def _read_json(p: Path):
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("读取失败 %s: %s", p, e)
        return None


def _iter_items(data) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "repos", "data", "list"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    return []


def build_index_rows(seed_dir: str) -> list[dict]:
    """合并 4 个索引文件为 gh_repo 行;各指标取 max。"""
    base = Path(seed_dir)
    merged: dict[str, dict] = {}
    for source, fname in SOURCE_FILES.items():
        f = base / fname
        if not f.exists():
            continue
        for it in _iter_items(_read_json(f)):
            fn = it.get("full_name") or it.get("repo") or it.get("name")
            if not fn or "/" not in str(fn):
                continue
            owner, name = str(fn).split("/", 1)
            row = merged.setdefault(str(fn), {
                "full_name": str(fn), "owner": owner, "name": name,
                "html_url": f"https://github.com/{fn}",
                "api_url": f"https://api.github.com/repos/{fn}",
                "topics": [], "sources": [], "seed_metrics": {},
                "stars": 0, "forks": 0, "watchers": 0,
            })
            if source not in row["sources"]:
                row["sources"].append(source)
            row["stars"] = max(as_int(row.get("stars")), as_int(it.get("stars", it.get("stargazers_count"))))
            row["forks"] = max(as_int(row.get("forks")), as_int(it.get("forks", it.get("forks_count"))))
            for k in ("description", "lang", "language", "homepage", "readme_url"):
                v = it.get(k)
                if v and not row.get(k if k != "language" else "lang"):
                    row[k if k != "language" else "lang"] = v
            if isinstance(it.get("topics"), list):
                row["topics"] = list({*row["topics"], *it["topics"]})
            row["seed_metrics"][source] = {"stars": it.get("stars", it.get("stargazers_count"))}
    # 计算日均增速(索引无历史时置 0)
    for row in merged.values():
        row["daily_velocity"] = 0
    return list(merged.values())


def import_index(seed_dir: Optional[str] = None) -> dict:
    """索引入库 + 同步熟知名单 + 当日快照 + 增量回填。"""
    if not seed_dir:
        return {"ok": False, "error": "未提供 seed_dir"}
    rows = build_index_rows(seed_dir)
    rep = repo_service.upsert_repos_report(rows)
    repo_service.refresh_major_flags()
    # 不再落「当日快照」(2026-10 删): 该快照会把首次见到的仓算成 delta = 全部 star。
    facts_service.rollup_gains()
    return {"ok": True, "upserted": rep["upserted"], "new": rep["new"], "scanned": len(rows)}


def import_local_history(seed_dir: str) -> dict:
    """读 history/owner__repo.json,落周聚合与每日增量。"""
    hdir = Path(seed_dir) / "history"
    if not hdir.is_dir():
        return {"ok": True, "imported": 0, "message": "无 history 目录"}
    ok = failed = 0
    for f in sorted(hdir.glob("*.json")):
        fn = f.stem.replace("__", "/")
        rid = db.query_scalar("SELECT id FROM gh_repo WHERE full_name = %s", [fn])
        if not rid:
            failed += 1
            continue
        data = _read_json(f)
        weeks, days = history_collector._to_weeks(data if isinstance(data, list) else _iter_items(data))
        if not weeks and not days:
            failed += 1
            continue
        facts_service.save_history(rid, weeks, days)
        ok += 1
    return {"ok": True, "imported": ok, "failed": failed}
