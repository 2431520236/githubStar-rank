"""IP 频率限制（2026-10 用户要求，默认启用，独立于反爬开关）。

规则（同一 IP）：
  1. 同一接口路径：最快 1 次/秒（path 不含 query —— 不同查询条件算同一接口，防带参狂刷）
  2. 全部接口合计：20 次/秒（滑动窗口兜底）

实现：纯内存 defaultdict + 时间戳（零依赖，符合项目「最轻量」原则；单 worker 下准确）。
被限请求返回 429，响应体统一 {code,msg,data}。
"""
import time

from . import config as cfg

# ---------------- 配置（来自 config.py，环境变量可覆盖） ----------------
GLOBAL_PER_SEC = cfg.RATE_LIMIT_GLOBAL_PER_SEC   # 同 IP 所有接口合计 / 秒
PATH_PER_SEC = cfg.RATE_LIMIT_PATH_PER_SEC       # 同 IP 同一接口 / 秒

# 页面框架探针类接口：每次页面加载都会被「顶栏组件」与「页面脚本」各调一次，
# 若同样受「同接口 1 次/秒」约束，正常首屏就会有一个 429（实测首页会同时打两次 health）。
# 因此这几个路径**豁免单接口 1 秒规则**，但**仍计入全局 20 次/秒**上限。
EXEMPT_PATHS = frozenset({
    "/api/system/health",
    "/api/system/rate-limit",
    "/api/auth/me",
})

# 前缀豁免：调试相关接口必须能连续调用（否则「用调试接口去关限流」会被限流自己挡住 —— 实测踩过）。
EXEMPT_PREFIXES = (
    "/api/system/debug",
)


def _is_exempt(path: str) -> bool:
    return path in EXEMPT_PATHS or any(path.startswith(p) for p in EXEMPT_PREFIXES)

# ip -> 最近请求时间戳列表（只保留 1 秒窗口内, 全部请求都计入——429 也计, 防恶意连打）
_GLOBAL: dict[str, list[float]] = {}
# (ip, path) -> 上次**放行**时间
_PATH: dict[tuple[str, str], float] = {}
# 惰性清理计数
_GC_EVERY = 20000
_GC_IDLE = 120.0             # 清理超过 120s 无活动的记录
_gc_count = 0


def _client_ip(request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return xff or (request.client.host if request.client else "unknown")


def check(request, path: str) -> bool:
    """检查当前请求是否放行。True=放行, False=被限(返回 429)。"""
    now = time.time()
    ip = _client_ip(request)

    # 1) 全局 20 次/秒：清出 1s 窗口外的旧时间戳
    stamps = _GLOBAL.get(ip)
    if stamps is None:
        stamps = []
        _GLOBAL[ip] = stamps
    while stamps and stamps[0] <= now - 1.0:
        stamps.pop(0)
    if len(stamps) >= max(1, int(GLOBAL_PER_SEC)):
        stamps.append(now)          # 429 也计数, 让恶意连打持续受限
        return False

    # 2) 单接口 1 次/秒：(ip, path) 上次放行时间（探针类路径豁免）
    if not _is_exempt(path):
        key = (ip, path)
        last = _PATH.get(key, 0.0)
        if now - last < 1.0:
            stamps.append(now)
            return False
        _PATH[key] = now

    # 放行
    stamps.append(now)

    # 惰性清理（避免长期运行内存无限增长）
    global _gc_count
    _gc_count += 1
    if _gc_count >= _GC_EVERY:
        _gc_count = 0
        _gc(now)
    return True


def _gc(now: float) -> None:
    """清掉超过 _GC_IDLE 秒无活动的 IP 记录。"""
    global _GLOBAL, _PATH
    _GLOBAL = {ip: st for ip, st in _GLOBAL.items()
               if st and st[-1] > now - _GC_IDLE}
    _PATH = {k: v for k, v in _PATH.items() if v > now - _GC_IDLE}
