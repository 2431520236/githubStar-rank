"""登录 / 登出 / 当前身份 / 管理员账密接口(prefix /api/auth)。

对应需求: 右上角管理员登录; 角色只区分 普通用户(guest) / 管理员(admin)。
账密默认 github/github, **可在页面修改**(存 gh_app_setting, 不依赖启动命令)。
"""
from fastapi import APIRouter, Depends, Request, Response

from .. import auth
from .. import config as cfg
from ..utils import fail, ok
from . import schemas as S

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def me(request: Request):
    """当前身份(未登录 = 普通用户 guest)。

    ⛔ 不回传 username —— 用户名即管理员账密的一部分, 一律不出现在响应/页面上。
    """
    u = auth.current_user(request)
    return ok({"role": u["role"], "is_admin": u["is_admin"]})


@router.post("/login")
def login(req: S.LoginReq, response: Response):
    if not auth.check_credentials(req.username, req.password):
        return fail("账号或密码不正确")
    token = auth.create_token(auth.effective_admin_user(), auth.ROLE_ADMIN)
    response.set_cookie(auth.COOKIE_NAME, token, max_age=auth.TOKEN_TTL,
                        httponly=True, samesite="lax", path="/")
    # 同上: 登录成功也不回显账号, 只给角色
    return ok({"role": auth.ROLE_ADMIN, "is_admin": True})


@router.get("/credentials")
def get_credentials(_admin: dict = Depends(auth.require_admin)):
    """账密状态(仅管理员): 是否页面配过 / 是否仍是出厂默认 github。

    ⛔ 只回布尔, 永不回传账号或密码 —— 页面据此提示「建议修改默认账密」。
    """
    return ok(auth.credentials_info())


@router.post("/credentials")
def set_credentials(req: S.CredentialReq, response: Response,
                    _admin: dict = Depends(auth.require_admin)):
    """修改管理员账密(仅管理员)。写库立即生效, 不需要重启或改启动命令。"""
    user = (req.username or "").strip()
    pwd = req.password or ""
    if len(user) < 2:
        return fail("账号至少 2 个字符")
    if len(pwd) < 4:
        return fail("密码至少 4 个字符")
    auth.set_credentials(user, pwd)
    # 账密变了 → 旧会话签名仍有效但账号已换, 直接发一个新会话, 免得管理员被踢
    token = auth.create_token(user, auth.ROLE_ADMIN)
    response.set_cookie(auth.COOKIE_NAME, token, max_age=auth.TOKEN_TTL,
                        httponly=True, samesite="lax", path="/")
    return ok(auth.credentials_info())


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return ok({"role": auth.ROLE_GUEST, "is_admin": False})
