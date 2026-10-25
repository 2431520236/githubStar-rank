"""数据库访问层:psycopg2 线程安全连接池 + 统一查询/执行助手。

设计要点(手册《05 · 多线程 / 限速 通用要点》):
  - 使用 ThreadedConnectionPool,每个线程独立取用连接,用完归还;
  - 所有写入走 UPSERT/幂等语句,支持中断重跑;
  - 行结果统一返回 dict(RealDictCursor),便于 JSON 直出。
"""
import contextlib
import logging
import threading
from typing import Any, Iterable, Optional, Sequence

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import Json, RealDictCursor

from . import config as cfg

log = logging.getLogger("ghradar.db")

_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_lock = threading.Lock()


def init_pool() -> None:
    """初始化连接池(幂等)。"""
    global _pool
    with _lock:
        if _pool is None:
            _pool = pg_pool.ThreadedConnectionPool(
                cfg.DB_POOL_MIN,
                cfg.DB_POOL_MAX,
                host=cfg.DB_HOST,
                port=cfg.DB_PORT,
                dbname=cfg.DB_NAME,
                user=cfg.DB_USER,
                password=cfg.DB_PASSWORD,
                connect_timeout=10,
                options="-c timezone=Asia/Shanghai",
            )
            log.info("数据库连接池已建立 %s:%s/%s", cfg.DB_HOST, cfg.DB_PORT, cfg.DB_NAME)


def close_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


def get_pool() -> pg_pool.ThreadedConnectionPool:
    if _pool is None:
        init_pool()
    return _pool  # type: ignore[return-value]


@contextlib.contextmanager
def get_conn():
    """借出一个连接,自动归还;异常时回滚。"""
    p = get_pool()
    conn = p.getconn()
    try:
        yield conn
    except Exception:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        p.putconn(conn)


def query_all(sql: str, params: Sequence[Any] | None = None) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: Sequence[Any] | None = None) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def query_scalar(sql: str, params: Sequence[Any] | None = None) -> Any:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None


def execute(sql: str, params: Sequence[Any] | None = None, *, returning: bool = False,
            many: Optional[Iterable[Sequence[Any]]] = None) -> Any:
    """执行写操作。returning=True 时返回首行(或行列表)。"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor if returning else None) as cur:
            if many is not None:
                cur.executemany(sql, list(many))
            else:
                cur.execute(sql, params)
            out: Any = None
            if returning:
                rows = cur.fetchall()
                out = [dict(r) for r in rows]
            conn.commit()
            return out


def health() -> dict:
    """健康检查:连接 + 版本 + 时区。"""
    try:
        row = query_one("SELECT current_database() AS database, version() AS version, "
                        "current_setting('TimeZone') AS timezone")
        return {"ok": True, **(row or {})}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


__all__ = ["Json", "init_pool", "close_pool", "get_pool", "get_conn",
           "query_all", "query_one", "query_scalar", "execute", "health"]
