"""轻量 TTL 缓存 —— 零依赖,纯 Python 字典实现。

用法:
    @cache.cached()                        # 默认: 无条件查询 10 分钟 / 带条件查询 2 分钟
    @cache.cached(ttl=600, cond_ttl=120)   # 显式指定

    cache.invalidate()        # 全清
    cache.invalidate("stats") # 清含 "stats" 的键

「带条件查询」的判定：FastAPI 会把解析后的**全部**入参作为 kwargs 传进来，
拿它与该参数的**默认值**比较；只要有一个偏离默认值（如 q=、langs=、min_stars=、
period_key=、year=、page=2 等），就视为「带条件的查询」，改用较短的 TTL。
这样「默认视图」缓存久（10 分钟），「用户筛出来的结果」缓存短（2 分钟）—— 符合用户 2026-10 口径。

TTL 过期后自动失效;满 `_MAXSIZE` 时淘汰最早到期条目。
"""
import functools
import hashlib
import inspect
import json
import time
from contextvars import ContextVar

from . import config as cfg
from . import runtime_config as rt

_MAXSIZE = 512  # 最多缓存 512 个不同的查询结果

# 观测统计（供调试端点；不参与业务逻辑）
_STATS = {"hit": 0, "miss": 0, "bypass": 0}

# 最近一次缓存结果（供中间件写 X-Cache 调试头）。
# ⚠️ 关键：anyio 在线程池里跑同步路由时，是**浅拷贝** context —— 
#   直接在路由线程里 `_LAST.set(...)` **传不回**外层中间件（实测拿到 None）。
#   正解是让 ContextVar 持有一个**可变 dict**：引用被复制、对象共享，
#   线程内 mutate 同一份 dict，外层中间件就能读到。
_LAST: ContextVar[dict | None] = ContextVar("cache_last", default=None)


def mark(status: str, ttl: float) -> None:
    holder = _LAST.get()
    if holder is not None:
        holder["status"] = status
        holder["ttl"] = ttl


def last_status():
    """返回 (HIT|MISS, ttl) 或 None。"""
    holder = _LAST.get()
    if holder and "status" in holder:
        return (holder["status"], holder.get("ttl"))
    return None


def reset_status() -> None:
    _LAST.set({})


def stats() -> dict:
    total = _STATS["hit"] + _STATS["miss"]
    return {
        **_STATS,
        "entries": len(_MEMO),
        "hit_rate": round(_STATS["hit"] / total, 3) if total else None,
    }


def reset_stats() -> None:
    for k in _STATS:
        _STATS[k] = 0


def entries() -> list[dict]:
    """当前缓存条目明细（key 截断 + 剩余 TTL），供调试端点。"""
    now = time.time()
    out = []
    for k, v in _MEMO.items():
        out.append({"key": k[:12], "ttl_left": round(max(0.0, v.exp - now), 1)})
    return sorted(out, key=lambda x: x["ttl_left"], reverse=True)


class _Entry:
    __slots__ = ("val", "exp")

    def __init__(self, val, ttl: float):
        self.val = val
        self.exp = time.time() + ttl


_MEMO: dict[str, _Entry] = {}


def _key(fn: str, args, kwargs) -> str:
    raw = (f"{fn}:{json.dumps(args, sort_keys=True, default=str)}:"
           f"{json.dumps(kwargs, sort_keys=True, default=str)}")
    return hashlib.md5(raw.encode()).hexdigest()


def _real_default(v):
    """FastAPI 的 Query()/Path() 等参数对象, 取它真正的 default 值。"""
    if type(v).__module__.startswith("fastapi") and hasattr(v, "default"):
        return getattr(v, "default")
    return v


def _defaults_of(func) -> dict:
    """提取函数签名里各参数的默认值（解析 FastAPI Query 包装）。"""
    out: dict = {}
    try:
        for name, p in inspect.signature(func).parameters.items():
            if p.default is inspect.Parameter.empty:
                continue
            out[name] = _real_default(p.default)
    except (TypeError, ValueError):
        return {}
    return out


def cached(ttl: int | None = None, cond_ttl: int | None = None):
    """TTL 缓存装饰器。

    ttl      「无条件/默认视图」缓存秒数；None 取 cfg.CACHE_TTL_DEFAULT（10 分钟）
    cond_ttl 「带条件查询」缓存秒数；None 取 cfg.CACHE_TTL_COND（2 分钟）

    运行时开关：`debug.cache_enabled=false` 时**直通**（不读不写缓存，便于开发看最新数据）。
    """
    def deco(func):
        base_ttl = int(ttl if ttl is not None else cfg.CACHE_TTL_DEFAULT)
        cond = int(cond_ttl if cond_ttl is not None else cfg.CACHE_TTL_COND)
        defaults = _defaults_of(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # ---- 调试开关：关闭缓存 → 直通 ----
            if not rt.cache_enabled():
                _STATS["bypass"] += 1
                return func(*args, **kwargs)

            now = time.time()
            # 惰性清理过期条目
            stale = [k for k, v in list(_MEMO.items()) if v.exp <= now]
            for k in stale:
                _MEMO.pop(k, None)

            k = _key(func.__qualname__, args, kwargs)
            hit = _MEMO.get(k)
            if hit and hit.exp > now:
                _STATS["hit"] += 1
                mark("HIT", round(hit.exp - now, 1))
                return hit.val

            # 带条件查询 → 短 TTL
            use_ttl = base_ttl
            if cond != base_ttl and defaults:
                for name, val in kwargs.items():
                    if name in defaults and val != defaults[name]:
                        use_ttl = cond
                        break

            result = func(*args, **kwargs)
            _STATS["miss"] += 1

            # 超量淘汰（最早到期者）
            if len(_MEMO) >= _MAXSIZE:
                oldest = min(_MEMO, key=lambda x: _MEMO[x].exp)
                _MEMO.pop(oldest, None)

            _MEMO[k] = _Entry(result, use_ttl)
            mark("MISS", use_ttl)
            return result
        return wrapper
    return deco


def invalidate(pattern: str | None = None) -> int:
    """使缓存失效。pattern: 含该子串的键被清; None=全清。返回清除条目数。"""
    global _MEMO
    before = len(_MEMO)
    if pattern is None:
        _MEMO.clear()
    else:
        _MEMO = {k: v for k, v in _MEMO.items() if pattern not in k}
    return before - len(_MEMO)


def size() -> int:
    return len(_MEMO)
