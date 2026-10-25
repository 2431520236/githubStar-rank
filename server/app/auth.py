"""管理员鉴权:签名 Cookie 会话 + 角色(admin / guest)。

角色只有两种(用户 2026-10 明确要求):
  - guest = 普通用户(未登录), 只读浏览;
  - admin = 管理员(登录后), 才能看到并点击敏感功能(加入知名 / 任务 / 供应商显示与设置等)。

管理员账密默认 github / github, 可用环境变量覆盖:
  GH_ADMIN_USER / GH_ADMIN_PASSWORD / GH_AUTH_SECRET

会话是无状态 HMAC 签名 token(HttpOnly Cookie), 不依赖数据库、不建表。
"""
import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import HTTPException, Request

from . import config as cfg

COOKIE_NAME = "ghradar_session"
TOKEN_TTL = 7 * 86400  # 会话有效期 7 天
ROLE_ADMIN = "admin"
ROLE_GUEST = "guest"


def _secret() -> bytes:
    return (cfg.AUTH_SECRET or "ghradar-local-secret").encode("utf-8")


# ---------------- 管理员账密: 页面可改, 不依赖启动命令 ----------------
# 用户 2026-10-23 口径: 「管理员最好可以配置账密，不依赖启动命令，启动后账密默认就是
# github，提示修改账密」。所以优先级是:
#   ① gh_app_setting 里的 auth.admin_user / auth.admin_password(摘要) —— 页面改过的
#   ② 环境变量 GH_ADMIN_USER / GH_ADMIN_PASSWORD(默认均 github)
# 密码**只存 sha256 摘要, 不存明文**, 接口也永不回传账密。
CRED_USER_KEY = "auth.admin_user"
CRED_PASS_KEY = "auth.admin_password"
DEFAULT_ADMIN_USER = "github"
DEFAULT_ADMIN_PASSWORD = "github"


def _hash_password(password: str) -> str:
    return hashlib.sha256(("ghradar-admin::" + str(password or "")).encode("utf-8")).hexdigest()


def _read_cred() -> tuple[str, str]:
    """读页面配置的账密(用户名, 密码摘要)。没配过返回 ("", "")。"""
    from . import db
    try:
        rows = db.query_all(
            "SELECT key, value FROM gh_app_setting WHERE key IN (%s, %s)",
            [CRED_USER_KEY, CRED_PASS_KEY])
    except Exception:  # noqa: BLE001  库不可用 → 回落环境变量, 不阻塞登录
        return "", ""
    got = {}
    for r in rows or []:
        v = r.get("value")
        if isinstance(v, dict) and "v" in v:
            v = v["v"]
        got[r.get("key")] = "" if v is None else str(v)
    return got.get(CRED_USER_KEY, ""), got.get(CRED_PASS_KEY, "")


def _write_setting(key: str, value: str) -> None:
    from . import db
    db.execute("""
        INSERT INTO gh_app_setting (key, value, updated_at) VALUES (%s, %s, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
    """, [key, db.Json(value)])


def set_credentials(username: str, password: str) -> None:
    """页面改账密: 写库(密码只存摘要), 立即生效, 无需重启。"""
    _write_setting(CRED_USER_KEY, username)
    _write_setting(CRED_PASS_KEY, _hash_password(password))


def effective_admin_user() -> str:
    """当前生效的管理员账号(库优先, 否则 env/默认)。"""
    u, h = _read_cred()
    return u if (u and h) else cfg.ADMIN_USER


def credentials_info() -> dict:
    """给页面用的账密状态 —— **只回布尔, 永不回账密本身**。"""
    u, h = _read_cred()
    configured = bool(u and h)
    user = u if configured else cfg.ADMIN_USER
    # 是否还是出厂默认(github/github): 页面据此提示「建议修改账密」
    is_default = False
    if configured:
        is_default = (user == DEFAULT_ADMIN_USER
                      and h == _hash_password(DEFAULT_ADMIN_PASSWORD))
    else:
        is_default = (str(cfg.ADMIN_USER) == DEFAULT_ADMIN_USER
                      and str(cfg.ADMIN_PASSWORD) == DEFAULT_ADMIN_PASSWORD)
    return {"configured": configured, "is_default": is_default}


def _sign(raw: str) -> str:
    return hmac.new(_secret(), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def create_token(username: str, role: str = ROLE_ADMIN, ttl: int = TOKEN_TTL) -> str:
    payload = {"u": username, "r": role, "exp": int(time.time()) + int(ttl)}
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return raw + "." + _sign(raw)


def verify_token(token: Optional[str]) -> Optional[dict]:
    if not token or "." not in token:
        return None
    raw, _, sig = token.rpartition(".")
    if not raw or not hmac.compare_digest(sig, _sign(raw)):
        return None
    try:
        pad = "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw + pad))
    except Exception:  # noqa: BLE001
        return None
    if int(payload.get("exp") or 0) < time.time():
        return None
    return payload


def check_credentials(username: str, password: str) -> bool:
    """常量时间比对, 避免长度/前缀侧信道。

    优先级: 页面配置的账密(gh_app_setting) > 环境变量/默认(github/github)。
    """
    u, h = _read_cred()
    if u and h:
        return (hmac.compare_digest(str(username or ""), u)
                and hmac.compare_digest(_hash_password(password), h))
    return (hmac.compare_digest(str(username or ""), str(cfg.ADMIN_USER))
            and hmac.compare_digest(str(password or ""), str(cfg.ADMIN_PASSWORD)))


def current_user(request: Request) -> dict:
    payload = verify_token(request.cookies.get(COOKIE_NAME))
    if payload:
        role = payload.get("r") or ROLE_GUEST
        return {"username": payload.get("u"), "role": role, "is_admin": role == ROLE_ADMIN}
    return {"username": None, "role": ROLE_GUEST, "is_admin": False}


def require_admin(request: Request) -> dict:
    """FastAPI 依赖: 敏感接口(写 / 触发 / 配置)必须先登录管理员。

    未登录时抛 401, 由 main.py 的 HTTPException 处理器统一包成 {code:401,msg,...}。
    """
    user = current_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=401, detail="需要管理员登录后才能执行该操作")
    return user
