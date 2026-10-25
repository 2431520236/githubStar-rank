"""GitHub 客户端 + 全局限速感知。

对应手册《05 · 采集器与多线程机制》2. github_client.py。
要点:每线程独立 Session 且 trust_env=False;403/429 退避;404 不算错误。
"""
import logging
import random
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from .. import config as cfg

log = logging.getLogger("ghradar.github")

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


class RateLimit:
    """全局 GitHub 额度管理(多线程共享)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.remaining = -1
        self.limit = -1
        self.reset_at: Optional[int] = None
        self.used = -1
        self._fetched_at = 0.0

    def update(self, headers: dict) -> None:
        try:
            rem = int(headers.get("X-RateLimit-Remaining", -1))
            lim = int(headers.get("X-RateLimit-Limit", -1))
            used = lim - rem if lim >= 0 and rem >= 0 else -1
            reset = int(headers.get("X-RateLimit-Reset", 0))
        except (TypeError, ValueError):
            return
        with self._lock:
            fresh = (time.time() - self._fetched_at) > 60
            # 仅当 used 更大或进入新窗口才采纳,避免误判
            if fresh or used > self.used or (reset and self.reset_at != reset):
                self.remaining, self.limit, self.used = rem, lim, used
                self.reset_at = reset or None
                self._fetched_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            reset_in = None
            if self.reset_at:
                reset_in = max(0, int(self.reset_at - time.time()))
            return {"remaining": self.remaining, "limit": self.limit, "used": self.used,
                    "reset_at": (datetime.fromtimestamp(self.reset_at, tz=timezone.utc).isoformat()
                                 if self.reset_at else None),
                    "reset_in_s": reset_in}

    def exhausted(self) -> bool:
        with self._lock:
            return self.remaining == 0

    def wait_if_needed(self, budget: int = 0) -> None:
        """额度不足则等待到窗口重置(睡眠,避免空转)。"""
        with self._lock:
            if self.remaining < 0 or self.reset_at is None:
                return
            if self.remaining <= budget:
                wait = max(0, int(self.reset_at - time.time())) + 2
        if wait > 0:
            log.warning("GitHub 额度不足,等待 %ss 至窗口重置", wait)
            time.sleep(min(wait, 3600))


RATE = RateLimit()


def _last_page(link_header: str) -> Optional[int]:
    """从 Link 头解析 rel="last" 的页码(GitHub 分页总页数)。"""
    if not link_header:
        return None
    for part in str(link_header).split(","):
        if 'rel="last"' in part:
            m = re.search(r"[?&]page=(\d+)", part)
            if m:
                return int(m.group(1))
    return None


def _next_link(link_header: str) -> Optional[str]:
    """从 Link 头解析 rel="next" 的完整 URL(翻页用)。"""
    if not link_header:
        return None
    for part in str(link_header).split(","):
        if 'rel="next"' in part:
            m = re.search(r"<([^>]+)>", part)
            if m:
                return m.group(1)
    return None


class GitHubClient:
    """GitHub API 客户端。每线程独立 Session,屏蔽环境代理。"""

    def __init__(self, token: Optional[str] = None, proxy: Optional[str] = None) -> None:
        self.token = token if token is not None else cfg.GITHUB_TOKEN
        self.proxy = proxy if proxy is not None else cfg.PROXY
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.trust_env = False  # 屏蔽环境 all_proxy 干扰
            s.headers.update({
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "GitHubStarRadar/1.0",
            })
            if self.token:
                s.headers["Authorization"] = f"Bearer {self.token}"
            self._local.session = s
        return s

    def _proxies(self) -> Optional[dict]:
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def raw(self, method: str, url: str, params: Optional[dict] = None,
            max_retries: Optional[int] = None, absolute: bool = True,
            headers: Optional[dict] = None) -> tuple[int, Any]:
        """底层请求:返回 (status_code, json|None)。404 不视为错误。"""
        code, data, _ = self.raw_full(method, url, params, max_retries, absolute, headers)
        return code, data

    def raw_full(self, method: str, url: str, params: Optional[dict] = None,
                 max_retries: Optional[int] = None, absolute: bool = True,
                 headers: Optional[dict] = None) -> tuple[int, Any, dict]:
        """同 raw,但额外返回响应头(用于读 Link 分页信息)。"""
        retries = cfg.MAX_RETRIES if max_retries is None else max_retries
        # 相对路径自动拼 API_BASE;绝对 URL(如翻页 Link 给的)原样使用。
        # 修复:原实现用 `if not absolute` 判断,导致默认值下相对路径永不拼 base,
        # 所有 API 调用直接抛 Invalid URL '/xxx': No scheme supplied。
        if not str(url).lower().startswith("http"):
            url = f"{cfg.API_BASE.rstrip('/')}/{str(url).lstrip('/')}"
        for attempt in range(max(1, retries)):
            try:
                r = self.session.request(method, url, params=params, timeout=cfg.REQUEST_TIMEOUT,
                                         proxies=self._proxies(), verify=False, headers=headers)
            except Exception as e:  # noqa: BLE001
                if attempt < retries - 1:
                    time.sleep(min(5 * (2 ** attempt), 60) + random.uniform(0, 1))
                    continue
                log.warning("请求失败 %s: %s", url, e)
                return 0, None, {}
            RATE.update(dict(r.headers))
            if r.status_code == 404:
                return 404, None, dict(r.headers)
            if r.status_code in (403, 429) and "rate limit" in r.text.lower():
                if attempt < retries - 1:
                    time.sleep(min(5 * (2 ** attempt), 60) + random.uniform(0, 1))
                    continue
                return r.status_code, None, dict(r.headers)
            if 500 <= r.status_code < 600:
                if attempt < retries - 1:
                    time.sleep(min(5 * (2 ** attempt), 60) + random.uniform(0, 1))
                    continue
                return r.status_code, None, dict(r.headers)
            if r.status_code >= 400:
                return r.status_code, None, dict(r.headers)
            try:
                return r.status_code, r.json(), dict(r.headers)
            except ValueError:
                return r.status_code, None, dict(r.headers)
        return 0, None, {}

    # ---------------- 业务方法 ----------------
    def rate_limit(self) -> dict:
        code, data = self.raw("GET", "/rate_limit")
        if code == 200 and data:
            return data.get("resources", {}).get("core", {})
        return RATE.snapshot()

    def repo(self, full_name: str) -> Optional[dict]:
        code, data = self.raw("GET", f"/repos/{full_name}")
        return data if code == 200 else None

    def readme_raw(self, full_name: str, branch: str = "HEAD") -> str:
        """走 raw CDN 取 README 原文(不计 API 额度)。"""
        for name in ("README.md", "README.MD", "README.rst", "readme.md", "README"):
            url = f"{RAW}/{full_name}/{branch}/{name}"
            try:
                r = self.session.get(url, timeout=cfg.REQUEST_TIMEOUT, proxies=self._proxies(),
                                     verify=False)
                if r.status_code == 200 and r.text:
                    return r.text
            except Exception:  # noqa: BLE001
                continue
        return ""

    def readme(self, full_name: str) -> str:
        code, data = self.raw("GET", f"/repos/{full_name}/readme")
        if code == 200 and data and data.get("content"):
            import base64
            try:
                return base64.b64decode(data["content"]).decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                return ""
        return ""

    def contributors(self, full_name: str, limit: int = 30) -> list[dict]:
        code, data = self.raw("GET", f"/repos/{full_name}/contributors",
                              params={"per_page": min(100, limit), "anon": "false"})
        return (data or []) if code == 200 else []

    def latest_release(self, full_name: str) -> Optional[dict]:
        code, data = self.raw("GET", f"/repos/{full_name}/releases/latest")
        return data if code == 200 else None

    def releases(self, full_name: str, limit: int = 10) -> list[dict]:
        code, data = self.raw("GET", f"/repos/{full_name}/releases",
                              params={"per_page": min(100, limit)})
        return (data or []) if code == 200 else []

    def tags(self, full_name: str, limit: int = 5) -> list[dict]:
        code, data = self.raw("GET", f"/repos/{full_name}/tags",
                              params={"per_page": min(100, limit)})
        return (data or []) if code == 200 else []

    def contents(self, full_name: str, path: str = "", ref: Optional[str] = None) -> list[dict]:
        params = {}
        if ref:
            params["ref"] = ref
        code, data = self.raw("GET", f"/repos/{full_name}/contents/{path}".rstrip("/"), params=params)
        if code == 200 and isinstance(data, list):
            return data
        return []

    STAR_ACCEPT = "application/vnd.github.star+json"

    def stargazers_history(self, full_name: str, max_pages: int = 1,
                           weeks: Optional[int] = None) -> list[dict]:
        """官方 star 历史:GET /repos/{owner}/{repo}/stargazers/history

        服务端**每页固定 30 周(≈7 个月)且忽略 per_page**;要更早历史须跟响应
        `Link: rel="next"` 翻页。响应每项 = {"week": <周日起点 unix 秒>,
        "total": <该周新增>, "days": [7 个整数, 周日→周六]}。

        这里把 7 天明细展开后**按 ISO 周(周一起)重新聚合**, 与库内 week_start 口径一致。
        默认 max_pages=1 → 只取最新 30 周(用户口径:最新 7 个月足够)。

        返回 _to_weeks 可直接解析的格式:
          [{"week":"2026-10-05","total":N,
            "days":[{"day":"2026-10-05","count":x}, ...]}, ...]
        """
        pages_limit = max(1, int(max_pages or 1))
        daily: dict[str, int] = {}
        url: Optional[str] = f"/repos/{full_name}/stargazers/history"
        used = 0
        while url and used < pages_limit:
            if RATE.exhausted():
                log.warning("额度耗尽,star 历史采集中断: %s (已用 %s 页)", full_name, used)
                break
            code, data, rh = self.raw_full("GET", url)
            if code != 200 or not isinstance(data, list):
                break
            used += 1
            for it in data:
                if not isinstance(it, dict):
                    continue
                ts = it.get("week")
                arr = it.get("days")
                if ts is None or not isinstance(arr, list):
                    continue
                try:
                    base = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
                except (TypeError, ValueError, OSError):
                    continue
                for i, c in enumerate(arr[:7]):
                    try:
                        n = int(c)
                    except (TypeError, ValueError):
                        continue
                    if n:
                        d = (base + timedelta(days=i)).isoformat()
                        daily[d] = daily.get(d, 0) + n
            url = _next_link(rh.get("Link", ""))

        if not daily:
            return []
        # 按 ISO 周(周一起)聚合,兼容 _to_weeks 与库内 week_start
        buckets: dict[str, dict] = {}
        for d in sorted(daily):
            dd = date.fromisoformat(d)
            monday = (dd - timedelta(days=dd.weekday())).isoformat()
            b = buckets.setdefault(monday, {"week": monday, "total": 0, "days": []})
            b["total"] += daily[d]
            b["days"].append({"day": d, "count": daily[d]})
        out = [buckets[k] for k in sorted(buckets)]
        if weeks:
            out = out[-int(weeks):]
        return out

    def search_repos(self, q: str, per_page: int = 100, page: int = 1) -> dict:
        code, data = self.raw("GET", "/search/repositories",
                              params={"q": q, "per_page": per_page, "page": page,
                                      "sort": "stars", "order": "desc"})
        return data if code == 200 and data else {"total_count": 0, "items": []}

    # ---------------- 归一化 ----------------
    @staticmethod
    def normalize(github_json: dict) -> dict:
        """GitHub 仓库 JSON → gh_repo 可写字段。"""
        g = github_json or {}
        owner = (g.get("owner") or {}).get("login") or ""
        lic = g.get("license") or {}
        return {
            "full_name": g.get("full_name") or "",
            "owner": owner,
            "name": g.get("name") or "",
            "html_url": g.get("html_url") or "",
            "api_url": g.get("url") or (f"{API}/repos/{g.get('full_name')}" if g.get("full_name") else None),
            "homepage": g.get("homepage"),
            "description": g.get("description"),
            "lang": g.get("language"),
            "license_spdx": lic.get("spdx_id") if lic.get("spdx_id") not in (None, "NOASSERTION") else None,
            "license_name": lic.get("name"),
            "topics": g.get("topics") or [],
            "stars": g.get("stargazers_count") or 0,
            "forks": g.get("forks_count") or 0,
            "watchers": g.get("subscribers_count") or g.get("watchers_count") or 0,
            "open_issues": g.get("open_issues_count") or 0,
            "size_kb": g.get("size") or 0,
            "default_branch": g.get("default_branch"),
            "is_archived": bool(g.get("archived")),
            "is_fork": bool(g.get("fork")),
            "repo_created_at": g.get("created_at"),
            "repo_pushed_at": g.get("pushed_at"),
            "repo_updated_at": g.get("updated_at"),
            # GitHub 仓库的**全局身份**（数字 id）。改名/转移后 id 不变 —— 这是业务唯一键，
            # 用来根治「同一个仓的旧名与新名各持一份 star 历史」的重复（见 pool_service）。
            "github_id": g.get("id"),
            "sources": ["github_api"],
        }


_client: Optional[GitHubClient] = None
_client_lock = threading.Lock()


def get_client() -> GitHubClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = GitHubClient()
        return _client
