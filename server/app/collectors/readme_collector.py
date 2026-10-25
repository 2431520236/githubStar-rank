"""README 地址采集:只列一次仓库根目录 Contents,同时拿到中英文 README 的网页地址。

对应手册《05 · 采集器与多线程机制》5. readme_collector.py。不下载正文,不浪费额度。
"""
import logging
from typing import Optional, Sequence

from .. import db
from ..services import repo_service
from ..utils import as_int
from . import base, github_client

log = logging.getLogger("ghradar.collector.readme")

_ZH_MARKS = ("zh", "cn", "chinese", "中文")
_EN_NAMES = ("readme", "readme.md", "readme.rst", "readme.txt", "readme.markdown")


def classify_readme(name: str) -> Optional[str]:
    """'ch' | 'en' | None"""
    if not name:
        return None
    low = name.lower()
    stem = low.rsplit(".", 1)[0] if "." in low else low
    if not low.startswith("readme") and stem not in _EN_NAMES:
        return None
    if any(m in low for m in _ZH_MARKS):
        return "ch"
    if low in _EN_NAMES or stem == "readme":
        return "en"
    return "en"


def pick_readme_urls(entries: Sequence[dict]) -> tuple[Optional[str], Optional[str]]:
    en = ch = None
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        kind = classify_readme(e.get("name") or "")
        url = e.get("html_url")
        if kind == "ch" and not ch:
            ch = url
        elif kind == "en" and not en:
            en = url
    return en, ch


def collect_readmes(limit: int = 200, ids: Optional[Sequence[int]] = None, concurrency: int = 6,
                    refresh_after_days: int = 30) -> dict:
    cands = repo_service.candidates_for_readme(limit, ids, refresh_after_days)

    def work(c: dict) -> dict:
        cli = github_client.get_client()
        entries = cli.contents(c["full_name"], "", c.get("default_branch"))
        if not entries:
            raise RuntimeError("contents 为空")
        en, ch = pick_readme_urls(entries)
        db.execute("""
            UPDATE gh_repo SET readme_en_url = %s, readme_ch_url = %s, readme_synced_at = now(),
                   updated_at = now() WHERE id = %s
        """, [en, ch, c["id"]])
        return {"full_name": c["full_name"], "en": en, "ch": ch}

    out = base.run_threaded(cands, work, concurrency=concurrency)
    return {"ok": out["ok"], "failed": out["failed"], "total": out["total"],
            "errors": out["errors"][:20]}
