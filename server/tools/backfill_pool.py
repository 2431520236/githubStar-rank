"""补采 ClickHouse 周榜候选池(每周 top2000 的并集)的 star 历史。

数据源: GitHub 官方 /repos/{owner}/{repo}/stargazers/history (每仓 1 页 = 最新 30 周)。
并发: 多线程跑满(默认 12); 额度是唯一瓶颈, 到 token 上限就睡到窗口重置再继续。

设计要点(实测踩过的坑, 别再改回去):
  * **额度只信真实 API 响应头(GC.RATE), 绝不信 /rate_limit** —— 实测经代理时
    /rate_limit 返回过期数字(报 rem=4995), 而真实请求已 403「API rate limit exceeded,
    used=5000」。信它会切出几千个的大批, 结果只有十几个真跑、其余全撞 403 被跳过,
    一整轮窗口白白浪费。
  * **分块提交**(CHUNK): 每块只提交一小批, 跑完立刻复核额度再决定下一块。
    额度可能被 api 主进程的调度器共享, 一次性提交一大批必然大量撞墙。
  * 用 history_synced_at 当哨兵: 采完(含无历史数据的空仓)即置位, 避免死循环重采。
  * 撞 403 时 GC.RATE 会从响应头拿到 remaining=0 + reset 时刻, 于是自动睡到窗口重置。

用法(容器内):
  python3 tools/backfill_pool.py            # 续采(默认)
  python3 tools/backfill_pool.py --reset    # 清标记重采
"""
import json
import os
import sys
import time

import urllib3

sys.path.insert(0, os.getenv("APP_DIR", "/app"))

from app import db  # noqa: E402
from app.collectors import github_client as gc, history_collector  # noqa: E402

urllib3.disable_warnings()          # 关掉每请求一条的 InsecureRequestWarning 刷屏

TODO_FILE = os.getenv("TODO_FILE", "/app/data/todo_names.tsv")
CONCURRENCY = int(os.getenv("CONCURRENCY", "12"))
# 每块提交量。取小值: 额度未知时也只会浪费一小块, 且能及时感知额度耗尽。
CHUNK = int(os.getenv("CHUNK", "200"))
# RATE 完全未知(进程刚起、还没发过请求)时的保守首块
COLD_START = int(os.getenv("COLD_START", "50"))


def load_todo() -> list[str]:
    """待采名单 = **历史池**（唯一口径来源 pool_service），不再读外部 tsv。

    用户 2026-10 口径：「周历史按照排名前 2000 的仓库」—— 池的判定只能有一处。
    `TODO_FILE` 仅作兜底：池为空时才回落到文件名单（用于异常排查）。
    """
    from app.services import pool_service
    rows = db.query_all(
        "SELECT full_name FROM gh_repo WHERE " + pool_service.pool_where("") +
        " ORDER BY stars DESC NULLS LAST")
    names = sorted({(r["full_name"] or "").strip().lower() for r in rows if r.get("full_name")})
    if names:
        return names
    if not os.path.exists(TODO_FILE):
        print("[fatal] 历史池为空且找不到兜底名单: %s" % TODO_FILE, flush=True)
        sys.exit(2)
    return sorted({l.strip().lower() for l in open(TODO_FILE, encoding="utf-8", errors="ignore") if l.strip()})


def quota() -> tuple[int, int]:
    """(剩余额度, 距窗口重置秒数)。**只读真实响应头** —— /rate_limit 经代理会返回过期值。

    - RATE 未知(-1): 返回保守首块, 让第一个请求去问出真实额度。
    - remaining==0 且窗口未过: 返回 0, 调用方睡到重置。
    - remaining==0 但窗口已过: 返回保守首块(额度应已刷新, 但别一次押满)。
    """
    snap = gc.RATE.snapshot()
    try:
        rem = int(snap.get("remaining"))
    except (TypeError, ValueError):
        rem = -1
    reset_in = snap.get("reset_in_s")
    if rem < 0:
        return COLD_START, (int(reset_in) if isinstance(reset_in, int) else 0)
    if rem == 0:
        if isinstance(reset_in, int) and reset_in > 0:
            return 0, reset_in
        return COLD_START, 0        # 窗口应已重置, 但先用小批量验证
    return rem, (int(reset_in) if isinstance(reset_in, int) else 0)


def report(tag: str) -> None:
    w = db.query_all(
        "SELECT count(*) AS rows, count(DISTINCT repo_id) AS repos, "
        "count(DISTINCT week_start) AS weeks, min(week_start) AS wmin, max(week_start) AS wmax "
        "FROM gh_repo_star_weekly")
    d = db.query_all("SELECT count(*) AS rows, count(DISTINCT repo_id) AS repos FROM gh_repo_star_daily")
    print("[%s weekly] %s" % (tag, json.dumps(w[0], default=str, ensure_ascii=False)), flush=True)
    print("[%s daily ] %s" % (tag, json.dumps(d[0], default=str, ensure_ascii=False)), flush=True)


def main() -> None:
    todo = load_todo()
    print("[todo] 名单 %d 仓 · CHUNK=%d 并发=%d" % (len(todo), CHUNK, CONCURRENCY), flush=True)

    # 默认**续采**: history_synced_at 作「本轮已尝试」哨兵, 保留已采进度。
    if "--reset" in sys.argv:
        db.execute("UPDATE gh_repo SET history_synced_at = NULL WHERE lower(full_name) = ANY(%s)", [todo])
        print("[reset] 已清零 history_synced_at(显式 --reset)", flush=True)
    else:
        left = db.query_scalar(
            "SELECT count(*) FROM gh_repo WHERE lower(full_name) = ANY(%s) AND history_synced_at IS NULL",
            [todo])
        print("[resume] 续采模式(保留已采标记), 队列剩 %s 仓" % left, flush=True)
    report("start")

    t0 = time.time()
    tot_ok = tot_failed = tot_skipped = rnd = 0
    while True:
        rnd += 1
        rows = db.query_all(
            "SELECT id, full_name FROM gh_repo "
            "WHERE lower(full_name) = ANY(%s) AND history_synced_at IS NULL", [todo])
        if not rows:
            print("[done] 全部采完 · 轮次=%d · 累计 ok=%d skipped=%d failed=%d · 总用时=%.0fs"
                  % (rnd - 1, tot_ok, tot_skipped, tot_failed, time.time() - t0), flush=True)
            break

        rem, reset_in = quota()
        if rem <= 0:
            wait = reset_in or 300
            print("[wait] 额度用尽, 睡 %ds 等窗口重置 · 队列还剩 %d 仓" % (wait, len(rows)), flush=True)
            time.sleep(max(5, min(wait + 10, 3700)))
            continue

        take = max(1, min(CHUNK, len(rows), rem))
        batch = rows[:take]
        ids = [r["id"] for r in batch]
        res = history_collector.collect_history(
            limit=len(ids), concurrency=CONCURRENCY,
            refresh_after_days=0, only_missing=False, ids=ids)
        ok = res.get("ok", 0)
        skipped = res.get("skipped", 0)
        failed = res.get("failed", 0)
        tot_ok += ok
        tot_skipped += skipped
        tot_failed += failed
        print("[round %d] 提交=%d ok=%d skipped=%d failed=%d · 队列剩=%d · 累计 ok=%d · 用时=%.0fs · rate=%s"
              % (rnd, len(ids), ok, skipped, failed, max(0, len(rows) - ok), tot_ok,
                 time.time() - t0, json.dumps(res.get("rate") or {}, ensure_ascii=False)),
              flush=True)
        if failed and res.get("errors"):
            print("[errors] %s" % json.dumps(res["errors"][:3], ensure_ascii=False), flush=True)
        # 整块都没进展(额度耗尽/被别处抢光) → 交给下一轮的 quota() 决定睡多久
        if ok == 0:
            time.sleep(2)

    report("final")


if __name__ == "__main__":
    main()
