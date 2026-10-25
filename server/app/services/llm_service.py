"""LLM 服务:任务执行、批量 AI 概要、中文硬校验、执行记录。

对应手册《06 · AI 概要提取机制》3~7。
核心约束:概要必须是中文 + 模板合规,不合格一律丢弃,只累加 ai_summary_tries。
"""
import logging
import re
import time
from typing import Any, Optional, Sequence

import requests

from .. import config as cfg
from .. import db
from ..utils import as_int
from . import llm_config

# 概要只对「有意义项目」提取(用户 2026-10 定): star ≥ 50 且 3 年内有更新。
# 与总榜筛选、核心指标共用同一口径 —— 长期停更/零 star 的仓没有提取中文概要的必要。
_MEANINGFUL = ("stars >= 50 AND COALESCE(repo_pushed_at, repo_updated_at, "
               "repo_created_at, first_seen_at) >= now() - make_interval(days => %d)"
               % cfg.DEFAULT_MAX_AGE_DAYS)

log = logging.getLogger("ghradar.llm")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_MIN_CJK = 20
MAX_SUMMARY_TRIES = 3
_SUMMARY_FORBIDDEN = ("用户要求", "材料包含", "按格式", "需要按格式", "作为ai",
                      "根据你", "以下是", "好的,", "简介如下", "以下是根据")

# ---------------- 概要生成硬约束(需求《06 · AI 概要提取机制》) ----------------
# ① 绝对禁止恶意全库扫描:任何选仓都必须带 LIMIT,且不得超过此硬上限
HARD_MAX_BATCH = 2000
# ② 每个项目没有中文概要时只 AI 生成一次:ai_summary 非空即永不重复生成
# ③ 批量:一次提交给模型的项目数(默认取任务配置 batch_size = 50)
DEFAULT_SUMMARY_BATCH = 50
# ④ 非周榜前 500 绝对禁止重复生成;周榜前 500 一周只刷新一次
WEEKLY_TOP_N = 500
REFRESH_COOLDOWN_DAYS = 7

_SUMMARY_SYS_BATCH = (
    "你是开源项目分析助手。阅读给定项目材料后,用流畅、简洁、有逻辑的中文写成一段或几段项目介绍,"
    "自然讲清楚:它是什么、有什么用、主要功能,像一段得体的小简介。禁止分点罗列、禁止小标题、"
    "禁止复述指令、禁止输出思考过程或与本项目无关的内容。"
)
_SUMMARY_TPL_BATCH = """共 {n} 个项目。请为每个项目用流畅的中文写 1~3 段话做介绍,自然涵盖:它是什么、解决什么问题、主要能力;语言要通顺连贯、有逻辑,不要分点、不要"做什么:"这类小标题、不要任何额外说明。每个项目以一行 "### <full_name>" 开头,随后紧跟正文(总字数约 120~250 字):

项目材料:
{materials}
"""


# ---------------- 校验 ----------------
def is_chinese_summary(t: Optional[str]) -> bool:
    if not t:
        return False
    return len(_CJK_RE.findall(t)) >= _MIN_CJK


def is_valid_summary(t: Optional[str]) -> bool:
    if not t:
        return False
    s = t.strip()
    if not is_chinese_summary(s):
        return False
    if s.startswith("#"):
        return False
    if len(s) > 900:
        return False
    low = s.lower()
    return not any(w in low for w in _SUMMARY_FORBIDDEN)


# ---------------- 模型调用 ----------------
def chat(provider: dict, model: dict, system: str, user: str) -> dict:
    """统一调用:openai_response/response/responses → /responses,其它 → /chat/completions。"""
    ptype = (provider.get("provider_type") or "openai_chat").lower()
    base = llm_config._join(provider.get("base_url"), provider.get("api_prefix", "/v1"),
                            "/responses" if ptype in ("openai_response", "response", "responses")
                            else "/chat/completions")
    headers = llm_config._headers(provider)
    proxies = llm_config._proxies(provider)
    timeout = int(provider.get("timeout_s") or 120)
    max_tokens = int(model.get("max_tokens") or 4096)
    temperature = float(model.get("temperature") or 0.3)

    if ptype in ("openai_response", "response", "responses"):
        body = {"model": model.get("model_name"), "max_output_tokens": max_tokens,
                "input": [{"role": "system", "content": [{"type": "input_text", "text": system}]},
                          {"role": "user", "content": [{"type": "input_text", "text": user}]}]}
    else:
        body = {"model": model.get("model_name"), "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}

    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(base, headers=headers, json=body, proxies=proxies,
                              timeout=timeout, verify=False)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}",
                        "text": "", "usage": {}}
            data = r.json()
            text, usage = _extract_text(data)
            return {"ok": True, "text": text, "usage": usage, "raw": data}
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    return {"ok": False, "error": last_err or "未知错误", "text": "", "usage": {}}


def _extract_text(data: dict) -> tuple[str, dict]:
    """兼容 output_text / responses.output[] / chat.choices / content。"""
    text = ""
    if isinstance(data.get("output_text"), str) and data["output_text"]:
        text = data["output_text"]
    if not text and isinstance(data.get("output"), list):
        for item in data["output"]:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in (None, "message"):
                continue  # 跳过 reasoning/思考过程
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("text"):
                    text += c["text"]
    if not text:
        try:
            msg = data["choices"][0]["message"]["content"]
            if isinstance(msg, str):
                text = msg
            elif isinstance(msg, list):
                text = "".join(c.get("text", "") for c in msg if isinstance(c, dict))
        except (KeyError, IndexError, TypeError):
            pass
    if not text and isinstance(data.get("content"), str):
        text = data["content"]
    usage = data.get("usage") or {}
    return text.strip(), {
        "prompt_tokens": as_int(usage.get("input_tokens", usage.get("prompt_tokens"))),
        "completion_tokens": as_int(usage.get("output_tokens", usage.get("completion_tokens"))),
        "total_tokens": as_int(usage.get("total_tokens")),
    }


# ---------------- 任务 ----------------
def list_tasks() -> list[dict]:
    return db.query_all("""
        SELECT t.*, m.model_name AS model_name FROM gh_llm_task t
        LEFT JOIN gh_llm_model m ON m.id = t.model_id ORDER BY t.id
    """)


def save_task(payload: dict) -> Optional[dict]:
    tid = payload.get("id")
    fields = {k: payload.get(k) for k in ("name", "description", "system_prompt", "prompt_template",
                                          "model_id", "concurrency", "batch_size",
                                          "max_readme_len", "enabled") if k in payload}
    if tid:
        sets = ", ".join(f"{k} = %s" for k in fields)
        rows = db.execute(f"UPDATE gh_llm_task SET {sets}, updated_at = now() WHERE id = %s "
                          f"RETURNING *", list(fields.values()) + [tid], returning=True)
        return rows[0] if rows else None
    key = payload.get("task_key")
    rows = db.execute("""
        INSERT INTO gh_llm_task (task_key, name, description, task_type, system_prompt,
                                 prompt_template, model_id, concurrency, batch_size)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
    """, [key, payload.get("name") or key, payload.get("description"), payload.get("task_type", "summary"),
          payload.get("system_prompt"), payload.get("prompt_template") or "{description}",
          payload.get("model_id"), as_int(payload.get("concurrency"), 3),
          as_int(payload.get("batch_size"), 20)], returning=True)
    return rows[0] if rows else None


def _cap(n, default: int) -> int:
    """统一上限:任何选仓都必须带 LIMIT 且不得突破 HARD_MAX_BATCH —— 防「全库扫描」。"""
    return max(1, min(as_int(n, default) or default, HARD_MAX_BATCH))


def _pick_repos(task_key: str, limit: int, ids: Optional[Sequence[int]] = None) -> list[dict]:
    limit = _cap(limit, DEFAULT_SUMMARY_BATCH)
    if ids:
        return db.query_all("SELECT * FROM gh_repo WHERE id = ANY(%s) ORDER BY stars DESC LIMIT %s",
                            [list(ids)[:limit], limit])
    if task_key == "repo_ai_summary":
        return db.query_all(f"""
            SELECT * FROM gh_repo WHERE {_MEANINGFUL} AND COALESCE(ai_summary,'') = ''
              AND COALESCE(ai_summary_tries,0) < %s
            ORDER BY star_gain_7d DESC NULLS LAST, stars DESC LIMIT %s
        """, [MAX_SUMMARY_TRIES, limit])
    if task_key == "repo_summary":
        return db.query_all(f"""
            SELECT * FROM gh_repo WHERE {_MEANINGFUL} AND COALESCE(summary,'') = ''
            ORDER BY star_gain_7d DESC NULLS LAST, stars DESC LIMIT %s
        """, [as_int(limit, 20)])
    if task_key == "version_note":
        return db.query_all("""
            SELECT * FROM gh_repo WHERE COALESCE(version_note,'') = '' AND latest_version IS NOT NULL
            ORDER BY latest_release_at DESC NULLS LAST LIMIT %s
        """, [as_int(limit, 20)])
    return db.query_all(f"SELECT * FROM gh_repo WHERE {_MEANINGFUL} ORDER BY stars DESC LIMIT %s",
                        [as_int(limit, 20)])


def _pick_weekly_refresh(limit: int) -> list[dict]:
    """周榜前 WEEKLY_TOP_N 中、距上次 AI 生成 ≥REFRESH_COOLDOWN_DAYS 天的项目。

    硬约束:非周榜前 500 的项目永不进入此列表 —— 绝对禁止重复生成。
    """
    return db.query_all("""
        SELECT r.* FROM gh_repo r
        JOIN (SELECT id FROM gh_repo WHERE stars >= 50
              ORDER BY star_gain_7d DESC NULLS LAST, stars DESC
              LIMIT %s) t ON t.id = r.id
        WHERE COALESCE(r.ai_summary,'') <> ''
          AND (r.ai_summary_synced_at IS NULL
               OR r.ai_summary_synced_at < now() - make_interval(days => %s))
        ORDER BY r.star_gain_7d DESC NULLS LAST, r.stars DESC
        LIMIT %s
    """, [WEEKLY_TOP_N, REFRESH_COOLDOWN_DAYS, _cap(limit, DEFAULT_SUMMARY_BATCH)])


def run_task(task_key: str, model_id: Optional[int] = None, limit: int = 20,
             ids: Optional[Sequence[int]] = None, concurrency: int = 4,
             group_size: Optional[int] = None, allow_overwrite: bool = False) -> dict:
    """执行任务。解析模型(显式 → 任务绑定 → 默认),按 task_key 分流。"""
    task = db.query_one("SELECT * FROM gh_llm_task WHERE task_key = %s", [task_key])
    if not task:
        return {"ok": False, "error": f"任务不存在: {task_key}"}
    # 批量/并发以任务配置为准(概要任务 batch_size=50、concurrency=4),调用方未显式指定时回退到配置
    concurrency = as_int(concurrency, 0) or as_int(task.get("concurrency"), 4)
    group_size = as_int(group_size, 0) or as_int(task.get("batch_size"), DEFAULT_SUMMARY_BATCH)
    limit = _cap(limit, as_int(task.get("batch_size"), DEFAULT_SUMMARY_BATCH))

    model = None
    if model_id:
        model = llm_config.get_model(model_id)
    if not model and task.get("model_id"):
        model = llm_config.get_model(task["model_id"])
    if not model:
        model = llm_config.get_default_model()
    if not model:
        return {"ok": False, "error": "未配置任何可用模型,请在 /api/ai/providers 添加供应商与模型"}
    provider = llm_config.get_provider(model["provider_id"], mask=False)

    if task_key == "repo_ai_summary":
        if allow_overwrite:
            # 硬约束:仅「周榜前 500 且距上次生成 ≥7 天」可刷新;非周榜前 500 永不覆盖
            repos = _pick_weekly_refresh(limit)
        else:
            # 只补空:已有中文概要的一律跳过(每个项目只生成一次)
            repos = [r for r in _pick_repos(task_key, limit, ids)
                     if not (r.get("ai_summary") or "").strip()]
        if not repos:
            msg = (f"周榜前 {WEEKLY_TOP_N} 暂无到期项目(同一项目 {REFRESH_COOLDOWN_DAYS} 天才刷新一次)"
                   if allow_overwrite else "没有需要处理的项目(可能都已生成)")
            return {"ok": True, "processed": 0, "task_key": task_key, "message": msg}
        return _run_ai_summary(repos, provider, model, group_size, concurrency, allow_overwrite)

    repos = _pick_repos(task_key, limit, ids)
    if not repos:
        return {"ok": True, "processed": 0, "message": "没有需要处理的项目(可能都已生成)"}
    return _run_generic(task, repos, provider, model, concurrency)


def _render(tpl: str, repo: dict, readme: str = "", extra: Optional[dict] = None) -> str:
    mapping = {
        "full_name": repo.get("full_name", ""), "lang": repo.get("lang") or "未知",
        "license": repo.get("license_spdx") or "未知", "description": repo.get("description") or "",
        "readme": readme or "", "latest_version": repo.get("latest_version") or "",
        "release_body": repo.get("version_note") or "", "period_label": "",
        "repo_list": "",
    }
    mapping.update(extra or {})
    out = tpl
    for k, v in mapping.items():
        out = out.replace("{" + k + "}", str(v or ""))
    return out


def _run_generic(task: dict, repos: list[dict], provider: dict, model: dict,
                 concurrency: int) -> dict:
    from ..collectors import base as cbase
    from ..collectors import repo_collector

    target_col = {"repo_summary": "summary", "version_note": "version_note",
                  "repo_ai_summary": "ai_summary"}.get(task["task_key"])
    tpl = task.get("prompt_template") or ""
    sys_p = task.get("system_prompt") or ""

    def work(repo: dict) -> dict:
        readme = ""
        try:
            readme = repo_collector.collect_readme_for(repo["full_name"]) or ""
        except Exception:  # noqa: BLE001
            pass
        max_len = as_int(task.get("max_readme_len"), 6000)
        user = _render(tpl, repo, readme[:max_len])
        res = chat(provider, model, sys_p, user)
        return {"repo": repo, "res": res}

    def on_result(item: dict) -> None:
        repo, res = item["repo"], item["res"]
        if res.get("ok") and is_valid_summary(res.get("text")):
            if target_col:
                db.execute(f"UPDATE gh_repo SET {target_col} = %s, updated_at = now() WHERE id = %s",
                           [res["text"][:4000], repo["id"]])
        elif target_col == "ai_summary":
            db.execute("UPDATE gh_repo SET ai_summary_tries = COALESCE(ai_summary_tries,0) + 1 "
                       "WHERE id = %s", [repo["id"]])

    out = cbase.run_threaded(repos, work, concurrency=concurrency, on_result=on_result)
    return {"ok": True, "processed": out["ok"], "failed": out["failed"],
            "errors": out["errors"][:10], "task_key": task["task_key"]}


def _run_ai_summary(repos: list[dict], provider: dict, model: dict, group_size: int,
                    concurrency: int, allow_overwrite: bool) -> dict:
    from ..collectors import base as cbase
    from ..collectors import repo_collector

    groups = [repos[i:i + max(1, group_size)] for i in range(0, len(repos), max(1, group_size))]
    stats = {"ok": 0, "failed": 0, "tries": 0}

    def one_group(group: list[dict]) -> dict:
        materials = []
        for r in group:
            readme = ""
            try:
                readme = repo_collector.collect_readme_for(r["full_name"]) or ""
            except Exception:  # noqa: BLE001
                pass
            materials.append(
                f"【{r['full_name']}】\n语言:{r.get('lang') or '未知'} "
                f"协议:{r.get('license_spdx') or '未知'}\n官方简介:{r.get('description') or ''}\n"
                f"README:\n{readme[:6000]}")
        names = [r["full_name"] for r in group]
        user = _SUMMARY_TPL_BATCH.format(n=len(group), materials="\n\n".join(materials))
        res = chat(provider, model, _SUMMARY_SYS_BATCH, user)
        if not res.get("ok"):
            return {"ok": 0, "failed": len(group), "error": res.get("error")}
        blocks = _split_blocks(res["text"], names)
        ok = 0
        for r in group:
            body = _strip_summary_body(blocks.get(r["full_name"], ""))
            if is_valid_summary(body):
                # 补空模式必须带 "ai_summary='' " 守卫;刷新模式(仅周榜前 500)才允许覆盖
                guard = "" if allow_overwrite else " AND COALESCE(ai_summary,'') = ''"
                db.execute(
                    "UPDATE gh_repo SET ai_summary = %s, ai_summary_synced_at = now(), "
                    "updated_at = now() WHERE id = %s" + guard,
                    [body[:4000], r["id"]])
                ok += 1
            else:
                db.execute("UPDATE gh_repo SET ai_summary_tries = COALESCE(ai_summary_tries,0) + 1 "
                           "WHERE id = %s", [r["id"]])
        return {"ok": ok, "failed": len(group) - ok}

    def on_result(item: dict) -> None:
        stats["ok"] += as_int(item.get("ok"))
        stats["failed"] += as_int(item.get("failed"))

    out = cbase.run_threaded(groups, one_group, concurrency=concurrency, on_result=on_result)
    return {"ok": True, "processed": stats["ok"], "failed": stats["failed"],
            "groups": len(groups), "errors": out["errors"][:10], "task_key": "repo_ai_summary"}


def _split_blocks(text: str, names: Sequence[str]) -> dict:
    """优先按 '### <full_name>' 标题行切块;无标题行回退名称包含匹配。"""
    out: dict[str, str] = {}
    if not text:
        return out
    lines = text.splitlines()
    cur: Optional[str] = None
    buf: list[str] = []
    for line in lines:
        matched = None
        if line.strip().startswith("#") or line.strip().startswith("【"):
            for n in names:
                if n in line:
                    matched = n
                    break
        if matched:
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur, buf = matched, []
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    # 回退:未命中任何标题的项目,尝试整段包含匹配
    for n in names:
        if n not in out and n in text:
            out[n] = text
    return out


def _strip_summary_body(block: str) -> str:
    lines = [l for l in (block or "").splitlines()]
    while lines and (lines[0].strip().startswith("#") or lines[0].strip().startswith("【")
                     or lines[0].strip().startswith("项目:")):
        lines.pop(0)
    return "\n".join(lines).strip()


def list_runs(limit: int = 100, task_key: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    where, params = [], []
    if task_key:
        where.append("task_key = %s")
        params.append(task_key)
    if status:
        where.append("status = %s")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return db.query_all(f"SELECT id, task_key, full_name, provider_name, model_name, status, "
                        f"prompt_tokens, completion_tokens, total_tokens, latency_ms, error, "
                        f"started_at, finished_at FROM gh_llm_run {where_sql} "
                        f"ORDER BY started_at DESC LIMIT %s", params + [as_int(limit, 100)])


def run_stats() -> dict:
    row = db.query_one("""
        SELECT count(*) AS runs,
               count(*) FILTER (WHERE status = 'success') AS success,
               count(*) FILTER (WHERE status = 'failed') AS failed,
               COALESCE(sum(total_tokens),0) AS tokens,
               COALESCE(round(avg(latency_ms))::int,0) AS avg_latency_ms
        FROM gh_llm_run
    """)
    return dict(row or {})
