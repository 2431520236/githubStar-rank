"""内置调度器(后台线程,20s 一 tick)。

对应手册《04 · 后端服务层逻辑》7. scheduler.py。
受 gh_app_setting.scheduler.enabled 开关控制;启动时恢复上次执行时间并清理残留 running。
"""
import logging
import threading
import time
from datetime import datetime, time as dtime, timedelta
from typing import Callable, Optional

from .. import config as cfg
from .. import db
from ..utils import as_int
from .. import cache
from . import job_service

log = logging.getLogger("ghradar.scheduler")

# 需要 GitHub Token 的「同步类」任务(用户 2026-10 口径):
#   **没 token 也必须能跑** —— 服务/页面/库里的 SQL 数据全部照常展示,
#   只是这些任务不执行或立即跳过, 不报错、不刷屏, 由页面顶栏提示去配 token。
TOKEN_TASKS = ("collect_detail", "collect_history", "backfill")


def _naive_local(dt: Optional[datetime]) -> Optional[datetime]:
    """把 DB(timestamptz) 返回的感知时间转成本地 naive, 与 datetime.now() 同型可减。"""
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _setting(key: str, default=None):
    row = db.query_one("SELECT value FROM gh_app_setting WHERE key = %s", [key])
    if not row:
        return default
    v = row["value"]
    if isinstance(v, dict) and "v" in v:
        return v["v"]
    return v


class Scheduler:
    """简单 cron 式调度:每个任务一个 check 函数,返回是否到期。"""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self.last_run: dict[str, Optional[datetime]] = {}
        self.last_result: dict[str, str] = {}
        self._token_noted: set[str] = set()
        self._last_stats: dict[str, dict] = {}
        self.tasks = self._build_tasks()

    # ---------------- 任务定义 ----------------
    def _build_tasks(self) -> list[dict]:
        return [
            # daily_snapshot 已删(2026-10 用户定): 它的 delta 用「本快照 - 上一快照」算,
            # 会把「上一条快照 stars=0」的仓算成「全部 star 都是当日新增」→ 假日增 8,796 万。
            # star 历史唯一数据源 = 官方 stargazers/history, 由下面的 collect_history 任务负责。
            {"name": "weekly_rank", "desc": "每周榜单计算(12 周 gain + 周/月 total/gain)",
             "period": "每周一 " + cfg.WEEKLY_RANK_AT, "runner": self._weekly_rank},
            {"name": "collect_detail", "desc": "补全仓库详情(limit 20)", "period": "每 86400s",
             "runner": self._collect_detail},
            {"name": "collect_history", "desc": "采集 star 历史(距上次 >1 天)", "period": "60s tick",
             "runner": self._collect_history},
            {"name": "watch_check", "desc": "检查关注提醒", "period": "每 600s", "runner": self._watch_check},
            {"name": "rollup", "desc": "回填增量指标", "period": "每 86400s", "runner": self._rollup},
            # AI 中文概要任务已从自动调度中移除(用户 2026-10 定: 禁止随意触发)。
            # 需要时经 API 手动触发 llm_service.run_task, 不放进 cron。
            {"name": "daily_rank", "desc": "每日刷新 周榜/月榜/总榜 三榜快照",
             "period": "每天 " + cfg.WEEKLY_RANK_AT, "runner": self._daily_rank},
            {"name": "backfill", "desc": "缺口补全", "period": "每 86400s", "runner": self._backfill},
            # daily_ch_increment 已删(2026-10 用户定): ClickHouse 是第二套源, 与官方
            # stargazers/history 混写同一张 gh_repo_star_daily, 是「多套数据混用」的来源之一。
        ]

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        job_service.abort_stale()
        self._restore_last_run()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        log.info("调度器已启动,%s 个任务", len(self.tasks))

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _restore_last_run(self) -> None:
        # 饿死修复(2026-10-26): gh_job_log 空时 last_run_at 恒 None, 而 _every 把 None
        # 判成「永远不到期」→ collect_history / rollup / collect_detail / backfill /
        # watch_check 一次都不跑(9951 实测连续 3 天零任务、0 条 job_log)。
        # last_run 本来就只在内存、重启即丢, 这里按各自间隔回拨成「已到期」,
        # 让任务在启动后的后续 tick 正常补跑; 跨重启的持久化由 _log_run 写 gh_job_log 完成。
        now = datetime.now()
        interval = {"collect_history": 3600, "watch_check": 600}
        for t in self.tasks:
            name = t["name"]
            try:
                self.last_run[name] = _naive_local(job_service.last_run_at(name))
            except Exception:  # noqa: BLE001
                self.last_run[name] = None
            if self.last_run[name] is None:
                if name in ("daily_rank", "weekly_rank"):
                    continue  # _at_daily 对 None 判「到期」, 本来就会跑
                sec = interval.get(name, 86400)
                self.last_run[name] = now - timedelta(seconds=sec)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.enabled():
                    for t in self.tasks:
                        if self._due(t):
                            # 没配 token → 同步类任务直接跳过(不执行、不报错)
                            if self.token_blocked(t["name"]):
                                self._note_token_skip(t["name"])
                                continue
                            self._spawn(t["name"], t["runner"], trigger="schedule")
            except Exception:  # noqa: BLE001
                log.warning("调度循环异常", exc_info=True)
            self._stop.wait(20)

    # ---------------- Token 守卫 ----------------
    def token_blocked(self, name: str) -> bool:
        """该任务是否「需要 token 但当前没配」。"""
        return name in TOKEN_TASKS and not cfg.has_github_token()

    def _note_token_skip(self, name: str) -> None:
        self.last_result[name] = "skipped: 未配置 GitHub Token"
        if name not in self._token_noted:      # 每个任务只提示一次, 不刷屏
            self._token_noted.add(name)
            log.info("任务 %s 跳过: 未配置 GH_GITHUB_TOKEN(服务与页面数据不受影响)", name)

    def enabled(self) -> bool:
        v = _setting("scheduler.enabled", None)
        if v is None:
            return cfg.SCHEDULER_ENABLED
        return bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")

    # ---------------- 到期判断 ----------------
    def _due(self, t: dict) -> bool:
        name = t["name"]
        last = self.last_run.get(name)
        now = datetime.now()
        if name == "daily_rank":
            return self._at_daily(last, now, cfg.WEEKLY_RANK_AT)
        if name == "weekly_rank":
            if now.weekday() != 0:  # 周一
                return False
            return self._at_daily(last, now, cfg.WEEKLY_RANK_AT)
        if name == "ai_month_top":
            if now.day != 1:
                return False
            return self._at_daily(last, now, "03:30")
        if name == "watch_check":
            return self._every(last, now, 600)
        if name == "collect_history":
            return self._every(last, now, 3600)
        # collect_detail / rollup / ai_summary / backfill:每 86400s
        return self._every(last, now, 86400)

    @staticmethod
    def _every(last: Optional[datetime], now: datetime, seconds: int) -> bool:
        last = _naive_local(last)  # gh_job_log.started_at 是 timestamptz → 感知时间;
        # now() 是 naive —— 直接相减抛 TypeError(2026-10-26 实测: 表里有行后调度循环每 tick 崩)
        if last is None:
            return False  # 启动后不立刻跑,等第一轮间隔
        return (now - last).total_seconds() >= seconds

    @staticmethod
    def _at_daily(last: Optional[datetime], now: datetime, at: str) -> bool:
        try:
            h, m = (int(x) for x in at.split(":")[:2])
        except (ValueError, AttributeError):
            h, m = 23, 50
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < target:
            return False
        return last is None or last.date() < now.date()

    # ---------------- 执行 ----------------
    def _spawn(self, name: str, runner: Callable[[], dict], trigger: str = "schedule") -> bool:
        with self._lock:
            if name in self._running:
                return False
            self._running.add(name)

        def wrap() -> None:
            try:
                res = runner()
                self.last_result[name] = "ok" if not (isinstance(res, dict) and res.get("failed")) else "partial"
                self._last_stats[name] = res if isinstance(res, dict) else {}
            except Exception as e:  # noqa: BLE001
                self.last_result[name] = f"failed: {e}"
                log.error("调度任务 %s 失败: %s", name, e)
            finally:
                self.last_run[name] = datetime.now()
                self._log_run(name, trigger)
                with self._lock:
                    self._running.discard(name)
                # 任务结束 → 清一次页面查询缓存(用户 2026-10 口径: 任务跑完清一次即可)
                try:
                    cache.invalidate()
                except Exception:  # noqa: BLE001
                    log.warning("清缓存失败", exc_info=True)

        threading.Thread(target=wrap, name=f"sched-{name}", daemon=True).start()
        return True

    def _log_run(self, name: str, trigger: str) -> None:
        """把一次调度执行落到 gh_job_log —— last_run_at() 靠它跨重启记住「上次跑过」,
        否则调度器任务(不走 job_service.job 的 collect_detail/rollup/daily_rank 等)
        在日志表里永远无行, 重启后又可能被判回「从未执行」。失败不致命, 吞异常。"""
        res = self.last_result.get(name) or ""
        status = "success" if res.startswith("ok") else ("failed" if res.startswith("failed") else "partial")
        st = self._last_stats.get(name) or {}
        try:
            db.execute(
                "INSERT INTO gh_job_log (job_name, trigger_type, status, total, succeeded, failed, "
                "duration_ms, message, stats, started_at, finished_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, now(), now())",
                [name, trigger, status,
                 as_int(st.get("total", 0)), as_int(st.get("ok", st.get("succeeded", 0))),
                 as_int(st.get("failed", 0)), res[:500], db.Json(st)])
        except Exception:  # noqa: BLE001
            log.warning("写调度日志失败: %s", name)

    def run_task_now(self, name: str) -> dict:
        task = next((t for t in self.tasks if t["name"] == name), None)
        if not task:
            return {"ok": False, "error": f"未知任务: {name}"}
        # 没配 token → 同步类任务立即跳过(手动触发同样不执行)
        if self.token_blocked(name):
            self._note_token_skip(name)
            return {"ok": False, "skipped": True, "task": name,
                    "message": "未配置 GitHub Token，同步任务已跳过（页面数据照常展示）"}
        started = self._spawn(name, task["runner"], trigger="manual")
        return {"ok": started, "task": name,
                "message": "已触发" if started else "该任务正在运行中"}

    def status(self) -> list[dict]:
        with self._lock:
            running = set(self._running)
        token_ready = cfg.has_github_token()
        return [{
            "name": t["name"], "desc": t["desc"], "period": t["period"],
            "running": t["name"] in running,
            "last_run_at": (self.last_run.get(t["name"]).isoformat()
                            if self.last_run.get(t["name"]) else None),
            "last_result": self.last_result.get(t["name"]),
            "needs_token": t["name"] in TOKEN_TASKS,
            "token_ready": token_ready,
        } for t in self.tasks]

    # ---------------- 内置任务实现 ----------------
    def _daily_rank(self) -> dict:
        """每天刷新「周榜 / 月榜 / 总榜」快照(当前周期, 覆盖式)。

        用户 2026-10 重定: 日榜恢复; 周/月窗口 = 周期首日 ~ min(周期末日, 今天)。
        总榜 = metric 'total'(按 gh_repo.stars 全库排序), 与日/周/月榜互不干扰。
        """
        from . import snapshot_service
        from .rank_service import period_bounds
        day_key, _ = period_bounds("day", None)
        week_start, _ = period_bounds("week", None)
        month_start, _ = period_bounds("month", None)
        done = {}
        for pt, pk in (("day", day_key),
                       ("week", snapshot_service._iso_week(week_start)),
                       ("month", month_start[:7])):
            for metric in ("gain", "total"):
                r = snapshot_service.compute_rank(pt, pk, metric)
                done["%s_%s" % (pt, metric)] = r.get("status")
        return done

    def _weekly_rank(self) -> dict:
        from . import snapshot_service
        from .rank_service import period_bounds
        # 计算最近 12 周的 gain 榜 + 本周/本月 total/gain
        out = {}
        weeks = db.query_all("SELECT DISTINCT week_start FROM gh_repo_star_weekly "
                             "ORDER BY week_start DESC LIMIT 12")
        computed = 0
        for w in weeks:
            key = snapshot_service._iso_week(str(w["week_start"]))
            for metric in ("gain", "total"):
                r = snapshot_service.compute_rank("week", key, metric)
                computed += 1 if r.get("status") == "computed" else 0
        start, _ = period_bounds("week", None)
        month_start, _ = period_bounds("month", None)
        for pt, pk in (("week", snapshot_service._iso_week(start)), ("month", month_start[:7])):
            for metric in ("total", "gain"):
                snapshot_service.compute_rank(pt, pk, metric)
        return {"computed": computed, "weeks": len(weeks)}

    def _collect_detail(self) -> dict:
        from ..collectors import repo_collector
        return repo_collector.collect_details(limit=20, only_missing=True, concurrency=4)

    def _collect_history(self) -> dict:
        from ..collectors import history_collector
        # 距上次 >1 天才拉;>2 天立即拉
        last = _naive_local(job_service.last_run_at("collect_history"))
        if last is not None:
            days = (datetime.now() - last).total_seconds() / 86400
            if days < 1:
                return {"skipped": True, "reason": f"距上次 {days:.2f} 天,未到 1 天"}
        return history_collector.collect_history(limit=1000, concurrency=3, refresh_after_days=1)

    def _watch_check(self) -> dict:
        from . import watch_service
        return watch_service.check_watches(force=False)

    def _rollup(self) -> dict:
        from . import facts_service
        return facts_service.rollup_gains()

    def _ai_summary(self) -> dict:
        from . import llm_service
        return llm_service.run_task("repo_ai_summary", limit=300, group_size=15,
                                    concurrency=10, allow_overwrite=False)

    def _ai_month_top(self) -> dict:
        from . import llm_service
        return llm_service.run_task("repo_ai_summary", limit=2000, group_size=20,
                                    concurrency=10, allow_overwrite=True)

    def _backfill(self) -> dict:
        from . import backfill_service
        return backfill_service.run_round()


scheduler = Scheduler()
