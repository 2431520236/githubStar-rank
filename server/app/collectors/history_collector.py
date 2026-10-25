"""star 历史采集(官方周聚合 → 周事实 + 每日增量)。

对应手册《05 · 采集器与多线程机制》4. history_collector.py。
额度耗尽时置 stop_flag,立即停止剩余仓库(不空转)。
"""
import logging
import threading
from datetime import date, datetime, timedelta
from typing import Optional, Sequence

from .. import config as cfg
from .. import db
from ..services import facts_service, repo_service
from ..utils import as_int
from . import base, github_client

log = logging.getLogger("ghradar.collector.history")


def _to_weeks(raw: Sequence[dict]) -> tuple[list[dict], list[dict]]:
    """官方 history → (weeks, days)。

    官方一页 = 最新 30 周, 每项形如 {"week": <周日0点Unix秒>, "total": N, "days": [7 个整数]}。
    这里把 `days[i]` 展开成逐日明细, 并给每一天算出**当天结束时的累计 star**
    (从该周累计往前逐日倒推) —— `gh_repo_star_daily.stars` 必须写这个真值:
    早先写 0 会让「上一条快照」看起来是 0, 下游算出「全部 star 都是当日新增」的假增量。
    """
    weeks: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        ws = item.get("week") or item.get("week_start") or item.get("date")
        if not ws:
            continue
        if isinstance(ws, str):
            ws_d = date.fromisoformat(ws[:10])
        else:
            ws_d = ws
        total = as_int(item.get("total", item.get("count", 0)))
        iso = ws_d.isocalendar()
        weeks.append({
            "week_start": ws_d, "week_key": f"{iso[0]}-W{iso[1]:02d}",
            "week_label": f"{iso[0]}年第{iso[1]}周", "weekly_new": total,
            "cumulative": as_int(item.get("cumulative", 0)),
            "days": [d for d in (item.get("days") or []) if isinstance(d, dict)],
            "source": "stargazers_history",
        })
    # 累计重算(逐周累加, 从最早的一周起)
    acc = 0
    for w in sorted(weeks, key=lambda x: x["week_start"]):
        acc += as_int(w["weekly_new"])
        w["cumulative"] = acc
    # 逐日明细: 每天结束时的累计 star = 周累计 - 该周内更晚几天的增量
    days: list[dict] = []
    for w in weeks:
        ds = sorted([d for d in w["days"] if d.get("day")], key=lambda x: str(x.get("day")))
        running = as_int(w["cumulative"])
        by_day: dict[str, int] = {}
        for d in reversed(ds):
            by_day[str(d["day"])] = running
            running -= as_int(d.get("count", 0))
        for d in ds:
            days.append({"date": str(d["day"]), "count": as_int(d.get("count", 0)),
                         "stars": by_day[str(d["day"])]})
    return weeks, days


def collect_one(full_name: str) -> dict:
    cli = github_client.get_client()
    raw = cli.stargazers_history(full_name)
    rid = db.query_scalar("SELECT id FROM gh_repo WHERE full_name = %s", [full_name])
    if not rid:
        raise RuntimeError(f"仓库不在库: {full_name}")
    if not raw:
        # 无历史数据也标记已同步,避免死循环重采
        db.execute("UPDATE gh_repo SET history_synced_at = now() WHERE id = %s", [rid])
        return {"full_name": full_name, "weeks": 0, "empty": True}
    weeks, days = _to_weeks(raw)
    res = facts_service.save_history(rid, weeks, days)
    return {"full_name": full_name, **res}


def collect_history(limit: int = 200, concurrency: int = 3, refresh_after_days: int = 7,
                    only_missing: bool = True, ids: Optional[Sequence[int]] = None) -> dict:
    # 没配 token → 同步任务立即跳过(不报错); 页面/库里已有数据不受影响
    if not cfg.has_github_token():
        return {"ok": 0, "failed": 0, "skipped": 0, "total": 0, "errors": [],
                "token_missing": True, "reason": "未配置 GitHub Token"}
    cands = repo_service.candidates_for_history(limit, ids, refresh_after_days, only_missing)
    names = [c["full_name"] for c in cands]
    stop = threading.Event()

    def work(fn: str) -> dict:
        if github_client.RATE.exhausted():
            stop.set()
            raise RuntimeError("GitHub 额度已耗尽,停止采集")
        return collect_one(fn)

    out = base.run_threaded(names, work, concurrency=concurrency, stop_flag=stop)
    out["rate"] = github_client.RATE.snapshot()
    return {"ok": out["ok"], "failed": out["failed"], "skipped": out.get("skipped", 0),
            "total": out["total"], "errors": out["errors"][:20], "rate": out["rate"]}


def collect_all_history(limit: int = 1000, concurrency: int = 3,
                        lookback_days: Optional[int] = None) -> dict:
    """全量重拉(一键刷新用):refresh_after_days=0 不做跳过判断。"""
    return collect_history(limit=limit, concurrency=concurrency, refresh_after_days=0,
                           only_missing=False)
