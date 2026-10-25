"""通用工具:统一响应体、分页、序列化、参数清洗。

接口响应约定(手册《03 · 后端 API 接口》通用约定):
  成功 {"code": 0, "msg": "ok", "data": ...}
  失败 {"code": 1, "msg": "<错误信息>", "data": null}
"""
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from . import config as cfg


def ok(data: Any = None, msg: str = "ok") -> dict:
    return {"code": 0, "msg": msg, "data": data}


def fail(msg: str, data: Any = None, code: int = 1) -> dict:
    return {"code": code, "msg": str(msg), "data": data}


def jsonable(obj: Any) -> Any:
    """把 DB 取出的值转成可 JSON 化的结构。"""
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def page_result(items: Sequence[Any], total: int, page: int, page_size: int) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or cfg.DEFAULT_PAGE_SIZE))
    pages = int(math.ceil(total / page_size)) if page_size else 0
    return {
        "items": jsonable(list(items)),
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def norm_page(page: Optional[int], page_size: Optional[int], default_size: Optional[int] = None) -> tuple[int, int]:
    page = max(1, int(page or 1))
    size = int(page_size or default_size or cfg.DEFAULT_PAGE_SIZE)
    size = max(1, min(size, 5000))
    return page, size


def csv_list(v: Optional[str]) -> list[str]:
    """'a,b,c' -> ['a','b','c'](去空、去重保序)。"""
    if not v:
        return []
    out: list[str] = []
    for x in str(v).split(","):
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out


def like_terms(q: Optional[str]) -> list[str]:
    """关键词按空格分词,词间 AND(手册 03 /repos 的 q 语义)。"""
    if not q:
        return []
    return [t for t in str(q).replace(",", " ").split() if t.strip()]


def clamp(v: Any, lo: Any, hi: Any, default: Any) -> Any:
    try:
        x = type(default)(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


__all__ = ["ok", "fail", "jsonable", "page_result", "norm_page", "csv_list",
           "like_terms", "clamp", "as_int"]
