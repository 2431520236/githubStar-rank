"""反爬虫防御 —— robots.txt + User-Agent 过滤 + 可写设置。

三层防御（按优先级）:
  1. robots.txt —— 标准爬虫协议,告诉搜索引擎/合规爬虫哪些路径可访
  2. User-Agent 黑名单 —— 拦截已知 Python HTTP 库的默认 UA
  3. 速率限制（预留）—— 未来可加 per-IP 计数

所有开关存在 `gh_app_setting` 中, 可在运行时通过 `POST /api/system/settings`
动态调整, **不需要重启服务**。

设置 key 约定: `anti_scrape.*`

┌─────────────────────────────────────┬────────┬─────────────────────────────┐
│ key                                 │ 缺省   │ 说明                        │
├─────────────────────────────────────┼────────┼─────────────────────────────┤
│ anti_scrape.enabled                 │ false  │ 总开关                     │
│ anti_scrape.block_python_ua          │ true   │ 拦截 Python HTTP 库 UA     │
│ anti_scrape.disallowed_paths         │ ["/api"]│ robots.txt 禁止路径列表     │
│ anti_scrape.crawl_delay              │ 10     │ robots.txt Crawl-Delay 秒  │
└─────────────────────────────────────┴────────┴─────────────────────────────┘
"""
from typing import Optional

import time

from . import config as cfg

# 已知 Python 爬虫/HTTP 库的默认 User-Agent 特征片段（大小写不敏感）
_KNOWN_PYTHON_AGENTS = (
    "python-requests",      # requests 库
    "python/",              # urllib / httpx
    "aiohttp/",             # aiohttp
    "httpx/",               # httpx
    "scrapy",               # Scrapy 框架
    "pyspider",             # pyspider
    "curl/",                # curl（也常被爬虫用）
    "wget/",                # wget
    "java/",                # Java HTTP client
    "okhttp",               # OkHttp（Android爬虫）
    "axios/",               # Node.js axios
    "got/",                 # Node.js got
    "node-fetch",           # Node.js fetch
    "fetch ",               # fetch API
    "selenium",             # Selenium
    "phantomjs",            # PhantomJS（已停维）
    "headless",             # Headless 浏览器
    "slurp",                # 通用爬虫
    "digg",                 # 通用爬虫
    "spider",               # 通用爬虫
    "crawler",              # 通用爬虫
    "bot",                  # 通用爬虫（会误伤搜索引擎, 但robots.txt 已在前置拦截）
)

# 搜索引擎/合规 Bot 白名单（不受 block_python_ua 影响）
_SAFE_AGENTS = (
    "googlebot",
    "bingbot",
    "slurp",                # Yahoo
    "duckduckbot",
    "baiduspider",
    "yandexbot",
    "facebookexternalhit",
    "twitterbot",
)


def is_python_scraper(user_agent: str) -> bool:
    """检查 UA 是否命中已知的 Python 爬虫特征。"""
    ua_lower = (user_agent or "").lower()
    # 先看是否搜索引擎白名单
    for safe in _SAFE_AGENTS:
        if safe in ua_lower:
            return False
    for pattern in _KNOWN_PYTHON_AGENTS:
        if pattern in ua_lower:
            return True
    return False


def robots_txt(enabled: bool, disallowed: list[str] | None = None,
               crawl_delay: int = 10, host: str = "") -> str:
    """生成 robots.txt 内容。根据是否启用反爬返回不同规则。"""
    lines = []
    if enabled:
        lines.append("User-agent: *")
        lines.append("Disallow: /")
        if crawl_delay > 0:
            lines.append(f"Crawl-Delay: {crawl_delay}")
        if host:
            lines.append(f"Host: {host}")
        lines.append("")
        lines.append(f"# 该站点已启用反爬保护。如需数据, 请联系管理员。")
    else:
        lines.append("User-agent: *")
        # 即使不启用反爬, 也禁掉部分敏感路径
        paths = disallowed or []
        if paths:
            for p in paths:
                lines.append(f"Disallow: {p}")
        else:
            lines.append("Disallow:")   # 允许全部
        if crawl_delay > 0:
            lines.append(f"Crawl-Delay: {crawl_delay}")
        if host:
            lines.append(f"Host: {host}")
    return "\n".join(lines) + "\n"


def resolve_setting(db_query_fn) -> dict:
    """从 gh_app_setting 读反爬配置，合并默认值。

    db_query_fn(key) -> value | None  （return None 表示无配置行）
    """
    out = {
        "enabled": cfg.ANTI_SCRAPE_ENABLED,
        "block_python_ua": cfg.ANTI_SCRAPE_BLOCK_UA,
        "disallowed_paths": ["/api"],
        "crawl_delay": cfg.ANTI_SCRAPE_CRAWL_DELAY,
        "rate_limit": False,
        "rate_limit_per_min": 300,
    }
    try:
        row = db_query_fn("anti_scrape.enabled")
        if row is not None:
            out["enabled"] = bool(row)
        row = db_query_fn("anti_scrape.block_python_ua")
        if row is not None:
            out["block_python_ua"] = bool(row)
        row = db_query_fn("anti_scrape.crawl_delay")
        if row is not None:
            out["crawl_delay"] = int(row) if row else 10
        row = db_query_fn("anti_scrape.disallowed_paths")
        if row is not None and isinstance(row, list):
            out["disallowed_paths"] = row
        row = db_query_fn("anti_scrape.rate_limit")
        if row is not None:
            out["rate_limit"] = bool(row)
        row = db_query_fn("anti_scrape.rate_limit_per_min")
        if row is not None:
            out["rate_limit_per_min"] = int(row) if row else 300
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------- 设置读取缓存（中间件每请求都会读, 必须缓存） ----------------
_SETTING_TTL = 30          # 秒
_SETTING_CACHE: dict = {"at": 0.0, "val": None}


def settings_cached(db_query_fn) -> dict:
    """带 TTL 的设置读取：30s 内复用，避免每个 HTTP 请求都打 4 次 DB。"""
    now = time.time()
    val = _SETTING_CACHE.get("val")
    if val is not None and now - float(_SETTING_CACHE.get("at") or 0) < _SETTING_TTL:
        return val
    val = resolve_setting(db_query_fn)
    _SETTING_CACHE["at"] = now
    _SETTING_CACHE["val"] = val
    return val


def clear_settings_cache() -> None:
    """改完 anti_scrape.* 设置后立即生效（不必等 30s TTL）。"""
    _SETTING_CACHE["val"] = None
    _SETTING_CACHE["at"] = 0.0


# ---------------- per-IP 速率限制（可选第三层, 默认关闭） ----------------
_RATE_WINDOW = 60          # 窗口: 60 秒
_RATE_LIMIT = 300          # 默认: 每 IP 每分钟 300 请求
_RATE_MEM: dict[str, list] = {}


def _rate_key(request) -> str:
    """取客户端 IP（走 X-Forwarded-For 首项, 无则用 peer）。"""
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return xff or (request.client.host if request.client else "unknown")


def rate_allowed(request, limit: int = _RATE_LIMIT) -> tuple[bool, int]:
    """滑动窗口计数。返回 (是否放行, 当前窗口请求数)。"""
    now = time.time()
    ip = _rate_key(request)
    stamps = _RATE_MEM.setdefault(ip, [])
    # 清掉窗口外的记录
    while stamps and stamps[0] < now - _RATE_WINDOW:
        stamps.pop(0)
    if len(stamps) >= max(1, int(limit)):
        return False, len(stamps)
    stamps.append(now)
    # 内存上限保护（极端多 IP 时丢弃最老 IP）
    if len(_RATE_MEM) > 10000:
        oldest = min(_RATE_MEM, key=lambda k: (_RATE_MEM[k][-1] if _RATE_MEM[k] else 0))
        _RATE_MEM.pop(oldest, None)
    return True, len(stamps)