"""运行时调试开关 —— 可在**不重启**、**不改代码**的情况下分档控制各项优化。

用户 2026-10 要求：「参考分级思路，最好可以增加调试开关，要便于开发，不仅仅生产性能」。
即：缓存 / 限流 / 压缩 / 304 / 调试响应头 每一层都能单独开合，
开发时一键关掉（拿最新数据、不被 429、看原始响应），生产时全开。

读写：`gh_app_setting`（key 前缀 `debug.*`），改完**立即生效**（update_setting 会 clear 本模块缓存）。
默认值可通过同名环境变量给（`GH_DEBUG_*`），但**库里的值优先级更高**。

历史：环境变量仍受支持（`GH_CACHE_ENABLED` 等），但运行时可改的开关统一走这里。
"""
import time

from . import db

_TTL = 30.0          # 库读取缓存（秒）；改设置时会被显式清掉，正常不会等这么久
_cache: dict[str, tuple[float, bool]] = {}

# key -> 默认值（生产友好：全开）
_DEFAULTS: dict[str, bool] = {
    "debug.cache_enabled": True,          # 服务端内存缓存
    "debug.rate_limit_enabled": True,     # IP 频率限制
    "debug.gzip_enabled": True,           # 响应压缩
    "debug.etag_enabled": True,           # ETag / 304
    "debug.static_cache": True,           # 静态资源缓存头（关掉便于反复验证首屏）
    "debug.response_headers": True,       # 响应头里带 X-Cache 等调试信息
}

DESCRIPTIONS = {
    "debug.cache_enabled": "服务端内存缓存开关（关=每次查库，开发看最新数据）",
    "debug.rate_limit_enabled": "IP 频率限制开关（关=不返回 429，便于压测/开发）",
    "debug.gzip_enabled": "gzip 压缩开关（关=看原始未压缩响应）",
    "debug.etag_enabled": "ETag/304 开关（关=每次返回 200 全量，便于看完整响应）",
    "debug.static_cache": "静态资源缓存头开关（关=no-store，便于反复验证首屏）",
    "debug.response_headers": "调试响应头（X-Cache: HIT/MISS 等）",
}


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def get(key: str) -> bool:
    """读一个运行时开关（带 30s 读缓存；改设置时会被 clear 立即生效）。"""
    now = time.time()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _TTL:
        return hit[1]
    val = _DEFAULTS.get(key, False)
    try:
        row = db.query_one("SELECT value FROM gh_app_setting WHERE key = %s", [key])
        if row is not None and row.get("value") is not None:
            val = _to_bool(row["value"])
    except Exception:  # noqa: BLE001
        pass  # 库不可用时用默认值，不阻塞请求
    _cache[key] = (now, val)
    return val


def cache_enabled() -> bool:
    return get("debug.cache_enabled")


def rate_limit_enabled() -> bool:
    return get("debug.rate_limit_enabled")


def gzip_enabled() -> bool:
    return get("debug.gzip_enabled")


def etag_enabled() -> bool:
    return get("debug.etag_enabled")


def static_cache_enabled() -> bool:
    return get("debug.static_cache")


def response_headers_enabled() -> bool:
    return get("debug.response_headers")


def clear() -> None:
    """清本模块读缓存（改设置后调用，立即生效）。"""
    _cache.clear()


def snapshot() -> dict:
    """当前全部开关状态（供调试端点/页面展示）。"""
    return {k: get(k) for k in _DEFAULTS}


def defaults() -> dict:
    return dict(_DEFAULTS)


def ensure_rows() -> int:
    """把默认开关写进 gh_app_setting（幂等，便于在设置页看到并逐项调）。"""
    n = 0
    for k, v in _DEFAULTS.items():
        try:
            rows = db.execute(
                "INSERT INTO gh_app_setting (key, value, description, updated_at) "
                "VALUES (%s, %s, %s, now()) ON CONFLICT (key) DO NOTHING RETURNING key",
                [k, db.Json(v), DESCRIPTIONS.get(k, "")], returning=True)
            n += len(rows or [])
        except Exception:  # noqa: BLE001
            pass
    return n
