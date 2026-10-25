#!/usr/bin/env python3
"""star 数据巡检 —— 检查周/日汇总覆盖度、缺口、源头断档（用户 2026-10 要求）。

输出：docs/巡检报告.md + stdout 一行汇总。

用法（容器内跑）:
  docker compose exec -T api python3 tools/check_star_coverage.py
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")
from app import db  # noqa: E402

MIN_WEEKLY = 3000
MIN_DAILY = 3000
REPORT = "/app/docs/巡检报告.md"


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = ["# star 数据巡检报告", "", "生成时间: %s" % now, ""]

    # 1. 周覆盖
    wk = db.query_all("SELECT week_start, count(*) c FROM gh_repo_star_weekly GROUP BY 1 ORDER BY 1")
    wk_thin = [(str(r["week_start"]), r["c"]) for r in wk if r["c"] < MIN_WEEKLY]
    out += ["## 1. 周汇总覆盖", "", "- 周数: %d" % len(wk), "- 周行数 >= %d: %d/%d"
            % (MIN_WEEKLY, len(wk) - len(wk_thin), len(wk))]
    if wk_thin:
        out.append("- **偏薄周（< %d 行）**:" % MIN_WEEKLY)
        for w, c in wk_thin:
            out.append("  - %s: %d 行" % (w, c))
    out.append("")

    # 2. 日覆盖
    dy = db.query_all("SELECT stat_date, count(*) c FROM gh_repo_star_daily GROUP BY 1 ORDER BY 1")
    dy_thin = [(str(r["stat_date"]), r["c"]) for r in dy if r["c"] < MIN_DAILY]
    out += ["## 2. 日汇总覆盖", "", "- 天数: %d" % len(dy),
            "- 日行数 >= %d: %d/%d" % (MIN_DAILY, len(dy) - len(dy_thin), len(dy))]
    if dy_thin:
        out.append("- **偏薄日（< %d 行）**: %d 天" % (MIN_DAILY, len(dy_thin)))
        for d, c in dy_thin[:20]:
            out.append("  - %s: %d 行" % (d, c))
        if len(dy_thin) > 20:
            out.append("  - ... 其余 %d 天" % (len(dy_thin) - 20))
    out.append("")

    # 3. 总量
    tot_wk = db.query_scalar("SELECT count(*) FROM gh_repo_star_weekly")
    tot_wk_repo = db.query_scalar("SELECT count(DISTINCT repo_id) FROM gh_repo_star_weekly")
    tot_dy = db.query_scalar("SELECT count(*) FROM gh_repo_star_daily")
    tot_dy_repo = db.query_scalar("SELECT count(DISTINCT repo_id) FROM gh_repo_star_daily")
    gh_n = db.query_scalar("SELECT count(*) FROM gh_repo")
    out += ["## 3. 总量", "", "| 指标 | 值 |", "|---|---|",
            "| gh_repo 仓数 | %d |" % gh_n,
            "| 周记录行数 | %d |" % tot_wk,
            "| 周记录仓数 | %d |" % tot_wk_repo,
            "| 日记录行数 | %d |" % tot_dy,
            "| 日记录仓数 | %d |" % tot_dy_repo, ""]

    # 4. 近期缺口
    recent_wk = db.query_all(
        "SELECT week_start, count(*) c FROM gh_repo_star_weekly "
        "WHERE week_start >= CURRENT_DATE - INTERVAL '8 weeks' GROUP BY 1 ORDER BY 1")
    out += ["## 4. 近 8 周", "", "| 周起始 | 行数 |", "|---|---|"]
    for r in recent_wk:
        out.append("| %s | %d |" % (r["week_start"], r["c"]))
    out.append("")

    try:
        open(REPORT, "w", encoding="utf-8").write("\n".join(out))
        wrote = REPORT
    except Exception as e:  # noqa: BLE001
        wrote = "(写入失败: %s)" % str(e)[:80]

    print("COVERAGE_CHECK weeks=%d(wk_thin=%d) days=%d(day_thin=%d) wk_rows=%d dy_rows=%d repos=%d -> %s"
          % (len(wk), len(wk_thin), len(dy), len(dy_thin), tot_wk, tot_dy, gh_n, wrote), flush=True)


if __name__ == "__main__":
    main()
