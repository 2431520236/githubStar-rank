"""FastAPI 应用装配:路由注册 + 静态资源挂载 + 生命周期。

对应手册《01 · 系统总览与架构》3. 服务端目录结构。
"""
import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from . import auth
from . import config as cfg
from . import db
from .api import ROUTERS
from . import anti_scrape
from . import rate_limit
from . import runtime_config as rt
from . import cache

logging.basicConfig(
    level=logging.DEBUG if cfg.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("ghradar")


class NoCacheStaticFiles(StaticFiles):
    """静态资源浏览器缓存策略（用户 2026-10 要求「很多资源不必重复下载」）。

    分两类：
      - **第三方库**（`vendor/` 下的 vue / element-plus / echarts，约 1.4 MB，几乎不变）
        → `public, max-age=31536000, immutable`：**浏览器直接用本地副本，完全不发请求**。
        升级库时改 index.html 里的引用加版本参数（如 `?v=2`）即可强制刷新。
      - **业务文件**（`js/` `css/` 下的自写脚本样式，改版频繁）
        → `no-cache, must-revalidate`：浏览器存副本，每次回源校验；没变返回 **304**（body 0 字节），
        一改立刻生效（ETag 变）。

    调试开关 `debug.static_cache=false` 时统一退化为 `no-store` ——
    便于开发时反复刷新、每次都是原始 200，不被浏览器缓存干扰。
    """

    async def get_response(self, path, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        p = str(path).lstrip("/")
        if not rt.static_cache_enabled():
            resp.headers["Cache-Control"] = "no-store"
            return resp
        if p.startswith("vendor/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


class ConditionalGZipMiddleware:
    """可运行时开合的 gzip（`debug.gzip_enabled=false` 时完全不压缩，便于看原始响应）。"""

    def __init__(self, app):
        self.app = app
        self._gz = GZipMiddleware(app, minimum_size=500)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not rt.gzip_enabled():
            return await self.app(scope, receive, send)
        return await self._gz(scope, receive, send)


class ETagMiddleware(BaseHTTPMiddleware):
    """给 /api/ 的 GET 响应加 ETag + no-cache，让浏览器用 304 省掉响应体。

    只处理 /api/ 的 200 GET JSON/文本响应；静态资源交给 StaticFiles 自带的 ETag。
    放在 GZip **内层**（先 add），这样 ETag 基于原始（未压缩）内容计算，跨客户端稳定。
    调试开关 `debug.etag_enabled=false` 时跳过（每次 200 全量，便于看完整响应）。
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not rt.etag_enabled():
            return response
        if request.method != "GET" or response.status_code != 200:
            return response
        if not request.url.path.startswith("/api/"):
            return response
        ct = response.headers.get("content-type", "")
        if not (ct.startswith("application/json") or ct.startswith("text/")):
            return response
        body = b""
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body += chunk
        etag = '"' + hashlib.md5(body).hexdigest() + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={
                "ETag": etag, "Cache-Control": "no-cache, must-revalidate"})
        headers = {k: v for k, v in response.headers.items()
                   if k.lower() not in ("content-length", "etag", "cache-control")}
        headers["ETag"] = etag
        headers["Cache-Control"] = "no-cache, must-revalidate"
        return Response(content=body, status_code=200, headers=headers,
                        media_type=response.media_type)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("启动 GitHub 飙升雷达 (FastAPI) 端口=%s", cfg.PORT)
    try:
        db.init_pool()
        log.info("数据库连接成功: %s", db.health())
    except Exception as e:  # noqa: BLE001
        log.error("数据库连接失败(服务继续启动,接口会返回错误): %s", e)
    # 启动内置调度
    try:
        from .services import scheduler
        if cfg.SCHEDULER_ENABLED:
            scheduler.scheduler.start()
    except Exception:  # noqa: BLE001
        log.warning("调度器启动失败", exc_info=True)
    yield
    log.info("正在关闭…")
    try:
        from .services import scheduler
        scheduler.scheduler.stop()
    except Exception:  # noqa: BLE001
        pass
    db.close_pool()


app = FastAPI(title="GitHub 飙升雷达", version="1.0.0",
              description="GitHub 项目数据大屏 + 榜单 + AI 概要(自托管实现)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- 压缩 + 缓存校验（2026-10 用户要求） ----------------
# ⚠️ 顺序：后 add 的在外层。执行(请求)方向 = outer → inner。
#   anti_scrape → admin_guard → GZip → ETag → CORS → 路由
# 因此 GZip 最后 add(最外层)，ETag 在它内层 —— ETag 基于**未压缩** body 计算，跨客户端稳定；
# 响应返回时先由 ETag 加校验头，再由 GZip 压缩。
app.add_middleware(ETagMiddleware)
app.add_middleware(ConditionalGZipMiddleware)

# ---------------- 管理员权限闸门(角色只有 普通用户/管理员) ----------------
# 规则: 除 /api/auth/* 与纯查询型 POST 之外, 一切非 GET 请求(写 / 触发 / 配置)
# 都必须已登录管理员 —— 覆盖"加入知名、任务、供应商显示与设置"等全部敏感操作。
# 用中间件而不是逐个 Controller 加依赖, 是为了不漏掉任何现有/将来新增的写接口。
_ADMIN_OPEN_PREFIX = ("/api/auth/",)
_ADMIN_OPEN_EXACT = ("/api/repos/batch",)   # 参数太多的纯查询接口, 不改语义
# ★2026-10: 历史快照读取(快照数据属于管理范畴, 不允许普通用户/爬虫直接拉取)
_ADMIN_GET_PATHS = (
    "/api/ranks/snapshot",
    "/api/ranks/snapshot-periods",
    "/api/system/debug",          # 调试开关/缓存观测也需管理员
    # ---- 管理页数据（2026-10 用户要求：非管理员整页不可见，接口也要兜底）----
    "/api/ai/",                   # 模型供应商 / 模型 / 任务 / 运行记录
    "/api/system/jobs",           # 调度任务
    "/api/system/logs",           # 任务日志
    "/api/system/config",         # 运行配置
    "/api/system/settings",       # 应用设置
    "/api/system/backfill",       # 缺口补全
    "/api/system/counts",         # 各表行数
)
_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@app.middleware("http")
async def admin_guard(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()
    # 写操作保护
    if (method in _WRITE_METHODS
            and path.startswith("/api/")
            and not path.startswith(_ADMIN_OPEN_PREFIX)
            and path not in _ADMIN_OPEN_EXACT):
        if not auth.current_user(request)["is_admin"]:
            return JSONResponse({"code": 401, "msg": "需要管理员登录后才能执行该操作",
                                 "data": None}, status_code=401)
    # 快照读取保护（即使 GET 也要管理员）
    if method == "GET" and path.startswith("/api/") and not auth.current_user(request)["is_admin"]:
        for guarded in _ADMIN_GET_PATHS:
            if path.startswith(guarded):
                return JSONResponse({"code": 401, "msg": "需要管理员登录后才能查看历史快照",
                                     "data": None}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def anti_scrape_guard(request: Request, call_next):
    """反爬虫中间件: 拦截已知 Python 爬虫 UA。"""
    path = request.url.path
    # 不拦页面自身、认证、静态资源
    if path in ("/api/auth/me", "/api/auth/login", "/api/auth/logout",
                "/robots.txt", "/favicon.ico", "/health") or path.startswith("/web"):
        return await call_next(request)
    try:
        s = anti_scrape.settings_cached(_get_setting)
        if s.get("enabled") and s.get("block_python_ua"):
            ua = request.headers.get("user-agent", "")
            if anti_scrape.is_python_scraper(ua):
                log.warning("反爬拦截 UA=%s path=%s", ua[:80], path)
                return JSONResponse({"code": 403, "msg": "Forbidden: 该站点已启用反爬保护",
                                     "data": None}, status_code=403)
        if s.get("enabled") and s.get("rate_limit"):
            ok_rate, cnt = anti_scrape.rate_allowed(request, s.get("rate_limit_per_min") or 300)
            if not ok_rate:
                log.warning("反爬限流 IP 已触发 path=%s count=%s", path, cnt)
                return JSONResponse({"code": 429, "msg": "请求过于频繁, 请稍后再试",
                                     "data": None}, status_code=429)
    except Exception:  # noqa: BLE001
        pass  # 反爬设置读取失败时不阻塞正常请求
    return await call_next(request)


@app.middleware("http")
async def rate_limit_guard(request: Request, call_next):
    """IP 频率限制（2026-10 用户要求，**默认启用**，独立于反爬开关）。

    规则（同一 IP）：
      ① 同一接口最快 1 次/秒 —— path 不含 query，不同查询条件算同一接口，
         防止「同一 IP 换参数狂刷同一接口」；
      ② 所有接口合计 20 次/秒 —— 滑动窗口兜底，防脚本轮询。

    只约束 `/api/` 接口；静态资源(`/web` `/css` `/js`)、页面、`/robots.txt`、
    `/docs` 不在限流范围（否则正常首屏并发加载会被误伤）。
    放在最外层（最后定义 ⇒ 最先执行），恶意请求尽早被挡，不进后面的反爬/鉴权/业务。
    """
    if not cfg.RATE_LIMIT_ENABLED or not rt.rate_limit_enabled():
        return await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        # 管理员不受限流约束（用户 2026-10 要求：「管理员禁止限流」）——
        # 只有普通访客/爬虫才被限，避免管理员调参/压测/连续点按被自己挡住。
        if not auth.current_user(request)["is_admin"] and not rate_limit.check(request, path):
            return JSONResponse({"code": 429, "msg": "请求过于频繁，请稍后再试",
                                 "data": None}, status_code=429,
                                headers={"Retry-After": "1"})
    return await call_next(request)


@app.middleware("http")
async def cache_debug_header(request: Request, call_next):
    """调试响应头：把本次请求的缓存命中情况回给客户端（`debug.response_headers` 控制）。

    `X-Cache: HIT|MISS|BYPASS` + `X-Cache-TTL: 剩余秒数` —— 开发时一眼看出「这次到底有没有走缓存」，
    比翻日志快得多。生产也可保留（无敏感信息，几乎零成本）。
    """
    cache.reset_status()
    response = await call_next(request)
    if not rt.response_headers_enabled():
        return response
    st = cache.last_status()
    if st:
        response.headers["X-Cache"] = st[0]
        response.headers["X-Cache-TTL"] = str(st[1])
    if not rt.cache_enabled():
        response.headers["X-Cache"] = "BYPASS"
    response.headers["X-Debug-Flags"] = ",".join(
        k[6:] for k, v in rt.snapshot().items() if not v) or "all-on"
    return response


def _get_setting(key: str):
    """从 gh_app_setting 读配置值(读取失败不回抛)。"""
    try:
        row = db.query_one("SELECT value FROM gh_app_setting WHERE key = %s", [key])
        return row["value"] if row else None
    except Exception:  # noqa: BLE001
        return None


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    """把 HTTPException(如鉴权 401)也包成统一响应体 {code,msg,data}。"""
    return JSONResponse({"code": exc.status_code, "msg": exc.detail, "data": None},
                        status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.error("未处理异常 %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse({"code": 1, "msg": str(exc), "data": None})


# ---------------- robots.txt（反爬虫协议） ----------------
@app.get("/robots.txt", include_in_schema=False)
def robots():
    s = anti_scrape.settings_cached(_get_setting)
    content = anti_scrape.robots_txt(
        enabled=s.get("enabled", False),
        disallowed=s.get("disallowed_paths", ["/api"]),
        crawl_delay=s.get("crawl_delay", 10),
        host="",
    )
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content, media_type="text/plain",
                             headers={"Cache-Control": "no-store"})


# ---------------- 接口路由 ----------------
for r in ROUTERS:
    app.include_router(r)


# ---------------- 前端页面(Docker 内可能无 web 目录,做存在性判断) ----------------
if cfg.WEB_DIR.exists():
    app.mount("/web", NoCacheStaticFiles(directory=str(cfg.WEB_DIR)), name="web")
    app.mount("/css", NoCacheStaticFiles(directory=str(cfg.WEB_DIR / "css")), name="css")
    app.mount("/js", NoCacheStaticFiles(directory=str(cfg.WEB_DIR / "js")), name="js")

    _PAGES = {"index.html", "rank.html", "detail.html", "ai.html", "watch.html", "system.html"}

    def _page(name: str, request: Request):
        p = cfg.WEB_DIR / name
        if not p.exists():
            return JSONResponse({"code": 1, "msg": f"页面缺失: {name}", "data": None}, status_code=404)
        # 页面也要「不必重复下载」：no-cache = 存副本 + 回源校验（没变 304），
        # 比 no-store（每次全量重下 8KB 且禁用 ETag）更省。
        # ⚠️ FileResponse 只**设置** ETag 头、**不做 304 判定**（判定逻辑在 StaticFiles 里），
        # 所以这里必须自己比对 If-None-Match，否则复请仍是 200 全量传输（实测踩过）。
        st = p.stat()
        etag = '"%s"' % hashlib.md5(f"{int(st.st_mtime)}-{st.st_size}".encode()).hexdigest()
        headers = {"Cache-Control": "no-cache, must-revalidate", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return FileResponse(str(p), headers=headers)

    @app.get("/", include_in_schema=False)
    def page_root(request: Request):
        return _page("index.html", request)

    @app.get("/detail", include_in_schema=False)
    def page_detail(request: Request):
        return _page("detail.html", request)

    def _make_page_route(n: str):
        def _route(request: Request):
            return _page(n, request)
        return _route

    for _name in sorted(_PAGES):
        app.get(f"/{_name}", include_in_schema=False)(_make_page_route(_name))
else:
    @app.get("/", include_in_schema=False)
    def page_missing():
        return JSONResponse({"code": 0, "msg": "后端已启动;前端静态目录 web/ 不存在",
                             "data": {"docs": "/docs"}})


@app.get("/api", include_in_schema=False)
def api_index():
    return {"code": 0, "msg": "ok", "data": {"docs": "/docs",
            "modules": ["system", "repos", "history", "ranks", "stats", "ai", "watch"]}}
