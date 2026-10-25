"""Controller 层(接口层):FastAPI Router 集合,等价 Java 的 @RestController。

分层约定:
  controllers(controller/) → services → collectors → db
Controller 只做:参数装配 → 调服务 → 统一 {code,msg,data} 包装。业务规则全部在 services。
"""
from . import (  # noqa: F401
    ai_controller,
    auth_controller,
    facts_controller,
    rank_controller,
    repo_controller,
    stat_controller,
    system_controller,
    watch_controller,
)

# 装配顺序(手册 03:system → repos → facts → ranks → stats → ai → watch; auth 置前)
ROUTERS = [
    auth_controller.router,
    system_controller.router,
    repo_controller.router,
    facts_controller.router,
    rank_controller.router,
    stat_controller.router,
    ai_controller.router,
    watch_controller.router,
]

__all__ = ["ROUTERS"]
