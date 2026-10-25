"""模型供应商/模型配置:JSON 文件唯一来源(DB 表只读不写)。

对应手册《06 · AI 概要提取机制》1. 配置唯一来源 data/llm_config.json。
线程安全 + 原子写;接口返回时 api_key 脱敏。
"""
import json
import os
import threading
import uuid
from typing import Any, Optional

import requests

from .. import config as cfg

_LOCK = threading.RLock()
_JSON_PATH = cfg.DATA_DIR / "llm_config.json"


def _blank() -> dict:
    return {"version": 1, "updated_at": None, "seq": {"provider": 0, "model": 0}, "providers": []}


def _load() -> dict:
    if not _JSON_PATH.exists():
        return _blank()
    try:
        with _JSON_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("seq", {"provider": 0, "model": 0})
        data.setdefault("providers", [])
        return data
    except (json.JSONDecodeError, OSError):
        return _blank()


def _save(data: dict) -> None:
    """原子写:先写临时文件再 os.replace。"""
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = _JSON_PATH.with_suffix(f".{uuid.uuid4().hex}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _JSON_PATH)


def _mask(key: Optional[str]) -> str:
    """已废弃的首尾可见脱敏 —— 首 4 + 尾 4 字符仍属可逆泄露, 不再使用。"""
    if not key:
        return ""
    return "*" * 8


def _mask_provider(p: dict) -> dict:
    """页面端不可逆: 密钥一个字符都不回传, 只回「是否已配置」。

    ⛔ 禁止再返回 api_key 的任何片段(哪怕 sk-****1234), 前端渲染不出来才叫不裸爆。
    """
    q = dict(p)
    q.pop("api_key", None)
    q["has_key"] = bool(p.get("api_key"))
    return q


# ---------------- 供应商 ----------------
def list_providers(mask: bool = True) -> list[dict]:
    with _LOCK:
        data = _load()
    ps = data["providers"]
    return [_mask_provider(p) for p in ps] if mask else ps


def get_provider(pid: int, mask: bool = False) -> Optional[dict]:
    for p in list_providers(mask=False):
        if int(p.get("id")) == int(pid):
            return _mask_provider(p) if mask else p
    return None


def add_provider(payload: dict) -> dict:
    with _LOCK:
        data = _load()
        data["seq"]["provider"] = int(data["seq"].get("provider", 0)) + 1
        pid = data["seq"]["provider"]
        models = payload.pop("models", []) or []
        names = payload.get("model_names") or []
        prov = {
            "id": pid,
            "name": payload.get("name") or f"provider{pid}",
            "provider_type": payload.get("provider_type", "openai_response"),
            "base_url": payload.get("base_url", ""),
            "api_key": payload.get("api_key", ""),
            "api_prefix": payload.get("api_prefix", "/v1"),
            "headers": payload.get("headers") or {},
            "timeout_s": int(payload.get("timeout_s") or 120),
            "max_retries": int(payload.get("max_retries") or 2),
            "enabled": bool(payload.get("enabled", True)),
            "is_default": bool(payload.get("is_default", False)),
            "remark": payload.get("remark", ""),
            "models": [],
        }
        if names:
            # 前端「拉取 → 选模型 ID」只传名称数组，与 update 路径共用同一套写入逻辑
            _sync_models(data, prov, names)
        else:
            for m in models:
                data["seq"]["model"] = int(data["seq"].get("model", 0)) + 1
                prov["models"].append({
                    "id": data["seq"]["model"],
                    "model_name": m.get("model_name") if isinstance(m, dict) else str(m),
                    "display_name": (m.get("display_name") if isinstance(m, dict) else None) or
                                    (m.get("model_name") if isinstance(m, dict) else str(m)),
                    "model_type": (m.get("model_type") if isinstance(m, dict) else "chat") or "chat",
                    "max_tokens": int((m.get("max_tokens") if isinstance(m, dict) else 4096) or 4096),
                    "temperature": float((m.get("temperature") if isinstance(m, dict) else 0.3) or 0.3),
                    "enabled": True,
                    "is_default": bool(m.get("is_default") if isinstance(m, dict) else False),
                })
        data["providers"].append(prov)
        _save(data)
        return _mask_provider(prov)


def update_provider(pid: int, payload: dict) -> Optional[dict]:
    with _LOCK:
        data = _load()
        for p in data["providers"]:
            if int(p["id"]) != int(pid):
                continue
            for k in ("name", "provider_type", "base_url", "api_prefix", "headers",
                      "remark"):
                if k in payload and payload[k] is not None:
                    p[k] = payload[k]
            # 密钥留空 = 不改
            if payload.get("api_key"):
                p["api_key"] = payload["api_key"]
            if payload.get("timeout_s") is not None:
                p["timeout_s"] = int(payload["timeout_s"])
            if payload.get("max_retries") is not None:
                p["max_retries"] = int(payload["max_retries"])
            if payload.get("enabled") is not None:
                p["enabled"] = bool(payload["enabled"])
            if payload.get("is_default"):
                for q in data["providers"]:
                    q["is_default"] = (q["id"] == p["id"])
            if payload.get("model_names") is not None:
                _sync_models(data, p, payload["model_names"])
            _save(data)
            return _mask_provider(p)
    return None


def _sync_models(data: dict, prov: dict, names: list) -> None:
    """按模型名列表同步(新增缺失、删除多余)。"""
    names = [n for n in names if n]
    existing = {m["model_name"]: m for m in prov.get("models", [])}
    new_list = []
    for n in names:
        if n in existing:
            new_list.append(existing[n])
        else:
            data["seq"]["model"] = int(data["seq"].get("model", 0)) + 1
            new_list.append({"id": data["seq"]["model"], "model_name": n, "display_name": n,
                             "model_type": "chat", "max_tokens": 4096, "temperature": 0.3,
                             "enabled": True, "is_default": False})
    prov["models"] = new_list


def delete_provider(pid: int) -> bool:
    with _LOCK:
        data = _load()
        before = len(data["providers"])
        data["providers"] = [p for p in data["providers"] if int(p["id"]) != int(pid)]
        if len(data["providers"]) == before:
            return False
        _save(data)
        return True


# ---------------- 模型 ----------------
def list_models() -> list[dict]:
    out = []
    for p in list_providers(mask=False):
        for m in p.get("models", []):
            out.append({**m, "provider_id": p["id"], "provider_name": p["name"],
                        "provider_type": p.get("provider_type")})
    return out


def get_model(mid: int, mask: bool = False) -> Optional[dict]:
    for p in list_providers(mask=False):
        for m in p.get("models", []):
            if int(m["id"]) == int(mid):
                return {**m, "provider_id": p["id"], "provider_name": p["name"],
                        "provider": _mask_provider(p) if mask else p}
    return None


def save_model(payload: dict) -> Optional[dict]:
    pid = payload.get("provider_id")
    if pid is None:
        return None
    with _LOCK:
        data = _load()
        for p in data["providers"]:
            if int(p["id"]) != int(pid):
                continue
            name = payload.get("model_name")
            for m in p.get("models", []):
                if m["model_name"] == name:
                    for k in ("display_name", "model_type", "max_tokens", "temperature", "enabled"):
                        if payload.get(k) is not None:
                            m[k] = payload[k]
                    _save(data)
                    return m
            data["seq"]["model"] = int(data["seq"].get("model", 0)) + 1
            m = {"id": data["seq"]["model"], "model_name": name,
                 "display_name": payload.get("display_name") or name,
                 "model_type": payload.get("model_type", "chat"),
                 "max_tokens": int(payload.get("max_tokens") or 4096),
                 "temperature": float(payload.get("temperature") or 0.3),
                 "enabled": bool(payload.get("enabled", True)),
                 "is_default": bool(payload.get("is_default", False))}
            p.setdefault("models", []).append(m)
            _save(data)
            return m
    return None


def delete_model(mid: int) -> bool:
    with _LOCK:
        data = _load()
        for p in data["providers"]:
            before = len(p.get("models", []))
            p["models"] = [m for m in p.get("models", []) if int(m["id"]) != int(mid)]
            if len(p["models"]) != before:
                _save(data)
                return True
    return False


def get_default_model() -> Optional[dict]:
    models = list_models()
    for m in models:
        if m.get("is_default"):
            return m
    return models[0] if models else None


# ---------------- 远端探测 ----------------
def _join(base: str, prefix: str, path: str) -> str:
    b = (base or "").rstrip("/")
    p = (prefix or "").strip("/")
    # base 已自带同样的路径前缀时不再重复拼接(否则 /v1 + /v1 + /models → 404)
    if p and b.lower().endswith("/" + p.lower()):
        return f"{b}/{path.lstrip('/')}"
    return f"{b}/{p}/{path.lstrip('/')}" if p else f"{b}/{path.lstrip('/')}"


def _headers(prov: dict) -> dict:
    h = {"Content-Type": "application/json"}
    if prov.get("api_key"):
        h["Authorization"] = f"Bearer {prov['api_key']}"
    h.update(prov.get("headers") or {})
    return h


def resolve_provider(payload: dict) -> dict:
    """把「前端传入的配置」与「已保存的供应商」合并。

    密钥按设计**不回传前端**, 所以已保存的供应商在前端 payload 里 api_key 恒为空。
    拉取模型/测试这类要真发请求的动作必须回落到**已保存的密钥** ——
    否则会拿空密钥打远端(实测远端返回 401 API key is required, 前端就显示"密钥为空")。
    """
    pid = payload.get("id")
    saved = get_provider(int(pid), mask=False) if pid is not None else None
    if not saved:
        return dict(payload)
    merged = dict(saved)
    for k, v in payload.items():
        if k in ("id", "api_key"):
            continue
        if v is not None and v != "":
            merged[k] = v
    if payload.get("api_key"):
        merged["api_key"] = payload["api_key"]
    merged["id"] = saved.get("id")
    return merged


def fetch_remote_models(provider_id: int) -> dict:
    prov = get_provider(provider_id, mask=False)
    if not prov:
        return {"ok": False, "error": "供应商不存在", "models": []}
    return probe_models(prov)


def probe_models(prov: dict) -> dict:
    url = _join(prov.get("base_url"), prov.get("api_prefix", "/v1"), "/models")
    try:
        r = requests.get(url, headers=_headers(prov),
                         timeout=int(prov.get("timeout_s") or 60), verify=False)
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}", "models": []}
        data = r.json()
        items = data.get("data") or data.get("models") or []
        names = [i.get("id") or i.get("name") for i in items if isinstance(i, dict)]
        return {"ok": True, "models": names, "count": len(names), "url": url}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "models": [], "url": url}


def _resp_path(prov: dict) -> str:
    ptype = (prov.get("provider_type") or "openai_response").lower()
    return "/responses" if ptype in ("openai_response", "response", "responses") \
        else "/chat/completions"


def probe_chat(prov: dict, model: Optional[str] = None) -> dict:
    """真发一次最小推理请求,验证供应商 + 密钥可用。

    协议暂定仅支持 openai response → POST {base}{prefix}/responses。
    """
    import time as _t
    mdl = model or (prov.get("models") or [{}])[0].get("model_name") \
        if isinstance(prov.get("models"), list) else None
    path = _resp_path(prov)
    url = _join(prov.get("base_url"), prov.get("api_prefix", "/v1"), path)
    if path == "/responses":
        payload = {"model": mdl or "gpt-4o-mini", "input": "ping",
                   "max_output_tokens": 16}
    else:
        payload = {"model": mdl or "gpt-4o-mini",
                   "messages": [{"role": "user", "content": "ping"}], "max_tokens": 16}
    t0 = _t.time()
    try:
        r = requests.post(url, headers=_headers(prov), json=payload,
                          timeout=int(prov.get("timeout_s") or 60), verify=False)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "url": url, "protocol": path}
    ms = int((_t.time() - t0) * 1000)
    if r.status_code >= 400:
        return {"ok": False, "url": url, "protocol": path, "latency_ms": ms,
                "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    return {"ok": True, "url": url, "protocol": path, "latency_ms": ms,
            "model": mdl, "sample": r.text[:300]}


def test_provider(pid: int) -> dict:
    """综合测试:① 拉远端模型列表 ② 发最小推理请求。"""
    prov = get_provider(pid, mask=False)
    if not prov:
        return {"ok": False, "error": "供应商不存在"}
    models = probe_models(prov)
    first = (models.get("models") or [None])[0]
    chat = probe_chat(prov, first)
    return {
        "ok": bool(models.get("ok")) and bool(chat.get("ok")),
        "models_ok": bool(models.get("ok")),
        "models": models.get("models") or [],
        "count": models.get("count", 0),
        "models_url": models.get("url"),
        "models_error": models.get("error"),
        "chat_ok": bool(chat.get("ok")),
        "chat_url": chat.get("url"),
        "chat_protocol": chat.get("protocol"),
        "latency_ms": chat.get("latency_ms"),
        "chat_error": chat.get("error"),
        "sample": chat.get("sample"),
    }
