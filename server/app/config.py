"""全局配置:环境变量读取 + 默认值(对应手册《09 · 部署与运行》)。

所有可调项都通过环境变量注入,代码不写死;本地开发可放 server/.env,
容器运行时由 docker-compose 的 environment 段注入。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# server/ 目录(本文件位于 server/app/config.py)
BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
README_TMP_DIR = BASE_DIR / "readme"

# 允许 server/.env 覆盖(容器内环境变量优先级更高)
load_dotenv(BASE_DIR / ".env")


def _bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


# ---------------- 服务 ----------------
HOST = os.getenv("GH_HOST", "0.0.0.0")
PORT = _int("GH_PORT", 9951)
DEBUG = _bool("GH_DEBUG", True)

# ---------------- 数据库 ----------------
DB_HOST = os.getenv("GH_DB_HOST", "127.0.0.1")
DB_PORT = _int("GH_DB_PORT", 54321)
DB_NAME = os.getenv("GH_DB_NAME", "github")
DB_USER = os.getenv("GH_DB_USER", "postgres")
DB_PASSWORD = os.getenv("GH_DB_PASSWORD", "postgres")
DB_POOL_MIN = _int("GH_DB_POOL_MIN", 2)
DB_POOL_MAX = _int("GH_DB_POOL_MAX", 16)

# ---------------- 筛选默认口径 ----------------
DEFAULT_MIN_STARS = _int("GH_DEFAULT_MIN_STARS", 50)
DEFAULT_MAX_AGE_DAYS = _int("GH_DEFAULT_MAX_AGE_DAYS", 1095)
DEFAULT_PAGE_SIZE = _int("GH_DEFAULT_PAGE_SIZE", 20)

# ---------------- 历史池口径（2026-10 用户重定，唯一来源） ----------------
# star 历史（周表 + 日表）只采集「stars 排名前 N」的仓；日/周/月榜的 gain 口径
# 同样限制在池内；**总榜(total)不受池限制**（沿用统一门槛，见 _base_filter）。
# 改动此值后必须重建池：python3 tools/rebuild_pool.py --rebuild-only
HISTORY_POOL_TOP_N = _int("GH_HISTORY_POOL_TOP_N", 2000)

# ---------------- GitHub 采集 ----------------
API_BASE = os.getenv("GH_API_BASE", "https://api.github.com")
GITHUB_TOKEN = os.getenv("GH_GITHUB_TOKEN", "")
PROXY = os.getenv("GH_PROXY", "")
CONCURRENCY = _int("GH_CONCURRENCY", 6)
REQUEST_TIMEOUT = _int("GH_REQUEST_TIMEOUT", 25)
MAX_RETRIES = _int("GH_MAX_RETRIES", 3)


def has_github_token() -> bool:
    """是否配置了 GitHub Token。

    用户 2026-10 口径: **没 token 也必须能跑** —— 服务、页面、库里的 SQL 数据
    (榜单/大屏/详情/AI 概要)全部照常展示; 只有「同步类」任务(采集详情 / 采集 star
    历史 / 缺口补全)不执行或立即跳过, 并在页面顶栏提示去配 token。
    """
    return bool((GITHUB_TOKEN or "").strip())

# ---------------- 反爬虫防御 ----------------|
ANTI_SCRAPE_ENABLED = _bool("GH_ANTI_SCRAPE_ENABLED", False)
ANTI_SCRAPE_BLOCK_UA = _bool("GH_ANTI_SCRAPE_BLOCK_UA", True)
ANTI_SCRAPE_CRAWL_DELAY = _int("GH_ANTI_SCRAPE_CRAWL_DELAY", 10)

# ---------------- 缓存（TTL 秒，0=不缓存） ----------------
# 用户 2026-10 口径：
#   - 「无条件 / 默认视图」的查询 —— 所有接口统一缓存 **10 分钟**；
#   - 「带条件」的查询（任何入参偏离默认值，如 q=、langs=、min_stars=、period_key=、page=2）—— 缓存 **2 分钟**；
#   - 触发型任务(采集/rollup/榜单快照)执行结束时自动清一次缓存(见 job_service / scheduler)；
#   - 管理员顶栏「清理缓存」按钮可随时手动清。
CACHE_TTL_DEFAULT = _int("GH_CACHE_TTL_DEFAULT", 600)   # 无条件下默认视图 10 分钟
CACHE_TTL_COND = _int("GH_CACHE_TTL_COND", 120)         # 带条件查询 2 分钟
# 兼容旧名（各处装饰器已统一为 @cache.cached()，取上面两个默认值）
CACHE_TTL_STATS = CACHE_TTL_DEFAULT
CACHE_TTL_META = CACHE_TTL_DEFAULT
CACHE_TTL_RANKS = CACHE_TTL_DEFAULT

# ---------------- IP 频率限制（2026-10 用户要求，默认启用） ----------------
# 同一 IP：① 同一接口最快 1 次/秒（path 不含 query，不同查询条件算同一接口）；
#           ② 所有接口合计 20 次/秒（滑动窗口兜底）。
# 目的：防恶意狂刷、防脚本轮询；静态资源 / 页面 / robots.txt 不在限流范围。
RATE_LIMIT_ENABLED = _bool("GH_RATE_LIMIT_ENABLED", True)
RATE_LIMIT_GLOBAL_PER_SEC = _int("GH_RATE_LIMIT_GLOBAL_PER_SEC", 20)
RATE_LIMIT_PATH_PER_SEC = _int("GH_RATE_LIMIT_PATH_PER_SEC", 1)

# ---------------- 管理员鉴权(角色只有 admin / guest) ----------------
# 默认管理员账密均为 github (用户 2026-10 指定); 生产部署请用环境变量覆盖。
ADMIN_USER = os.getenv("GH_ADMIN_USER", "github")
ADMIN_PASSWORD = os.getenv("GH_ADMIN_PASSWORD", "github")
AUTH_SECRET = os.getenv("GH_AUTH_SECRET", "")

# ---------------- 统计 / 调度 ----------------
STAT_START_DATE = os.getenv("GH_STAT_START_DATE", "2025-01-01")
SCHEDULER_ENABLED = _bool("GH_SCHEDULER_ENABLED", True)
DAILY_SNAPSHOT_AT = os.getenv("GH_DAILY_SNAPSHOT_AT", "23:50")
WEEKLY_RANK_AT = os.getenv("GH_WEEKLY_RANK_AT", "01:00")

# 采集/断点续采阈值
HISTORY_LOOKBACK_DAYS = _int("GH_HISTORY_LOOKBACK_DAYS", 210)
HISTORY_REFRESH_AFTER_DAYS = _int("GH_HISTORY_REFRESH_AFTER_DAYS", 7)
README_REFRESH_AFTER_DAYS = _int("GH_README_REFRESH_AFTER_DAYS", 30)


def as_dict() -> dict:
    """暴露给 /api/system/config(不含密钥明文)。"""
    return {
        "host": HOST,
        "port": PORT,
        "debug": DEBUG,
        "db": {
            "host": DB_HOST,
            "port": DB_PORT,
            "name": DB_NAME,
            "user": DB_USER,
            "pool_min": DB_POOL_MIN,
            "pool_max": DB_POOL_MAX,
        },
        "filter": {
            "default_min_stars": DEFAULT_MIN_STARS,
            "max_age_days": DEFAULT_MAX_AGE_DAYS,
            "default_page_size": DEFAULT_PAGE_SIZE,
        },
        "collect": {
            "api_base": API_BASE,
            "token_configured": bool(GITHUB_TOKEN),
            "proxy": PROXY,
            "concurrency": CONCURRENCY,
            "request_timeout": REQUEST_TIMEOUT,
            "max_retries": MAX_RETRIES,
        },
        "auth": {
            "admin_user": ADMIN_USER,
            "secret_configured": bool(AUTH_SECRET),
        },
        "stat": {
            "start_date": STAT_START_DATE,
            "scheduler_enabled": SCHEDULER_ENABLED,
            "daily_snapshot_at": DAILY_SNAPSHOT_AT,
            "weekly_rank_at": WEEKLY_RANK_AT,
        },
        "anti_scrape": {
            "enabled": ANTI_SCRAPE_ENABLED,
            "block_python_ua": ANTI_SCRAPE_BLOCK_UA,
            "crawl_delay": ANTI_SCRAPE_CRAWL_DELAY,
        },
        "rate_limit": {
            "enabled": RATE_LIMIT_ENABLED,
            "same_path_per_sec": RATE_LIMIT_PATH_PER_SEC,
            "all_paths_per_sec": RATE_LIMIT_GLOBAL_PER_SEC,
        },
    }
