"""针对 stars=0 的仓做独立验证：官方接口 vs 库内周值。只读，不改库。

输出落 /app/data/verify_ghost.txt
"""
import json
import sys

sys.path.insert(0, "/app")

from app import db
from app.collectors import github_client as gc
from app.collectors.history_collector import _to_weeks

OUT = "/app/data/verify_ghost.txt"
lines = []


def p(s=""):
    lines.append(str(s))


# 取 2026-10-05 那周贡献最大、且库内 stars=0 的仓
rows = db.query_all(
    "SELECT r.id, r.full_name, COALESCE(r.stars,0) AS stars, r.history_synced_at, "
    "       COALESCE(r.repo_created_at::text,'') AS created, "
    "       (SELECT sum(weekly_new) FROM gh_repo_star_weekly w WHERE w.repo_id = r.id) AS wn_sum, "
    "       (SELECT count(*) FROM gh_repo_star_weekly w WHERE w.repo_id = r.id) AS wk_cnt "
    "FROM gh_repo r JOIN gh_repo_star_weekly w ON w.repo_id = r.id AND w.week_start = '2026-10-05' "
    "WHERE COALESCE(r.stars,0) = 0 ORDER BY w.weekly_new DESC LIMIT 6")
p("候选（2026-10-05 周 stars=0）:")
for r in rows:
    p("  id=%s %s stars=%s wn_sum=%s weeks=%s synced=%s created=%s"
      % (r["id"], r["full_name"], r["stars"], r["wn_sum"], r["wk_cnt"],
         r["history_synced_at"], r["created"]))

cli = gc.get_client()
for r in rows:
    name = r["full_name"]
    p("")
    p("=" * 70)
    p("REPO %s (id=%s)" % (name, r["id"]))
    code, data, _h = cli.raw_full("GET", "/repos/%s" % name)
    p("GET /repos -> HTTP %s" % code)
    if code == 200 and isinstance(data, dict):
        p("  真实 stars=%s created=%s" % (data.get("stargazers_count"), data.get("created_at")))
    raw = cli.stargazers_history(name)
    p("  history items=%d" % len(raw or []))
    if not raw:
        p("  >>> 官方无历史数据")
        continue
    weeks, _days = _to_weeks(raw)
    api = {str(w["week_start"]): int(w["weekly_new"]) for w in weeks}
    dbm = {str(x["ws"]): int(x["weekly_new"]) for x in db.query_all(
        "SELECT to_char(week_start,'YYYY-MM-DD') AS ws, weekly_new FROM gh_repo_star_weekly "
        "WHERE repo_id = %s", [r["id"]])}
    p("  api_weeks=%d db_weeks=%d" % (len(api), len(dbm)))
    p("  %-12s %10s %10s" % ("week", "api", "db"))
    for k in sorted(set(api) | set(dbm)):
        p("  %-12s %10s %10s %s" % (k, api.get(k), dbm.get(k),
                                    "" if api.get(k) == dbm.get(k) else "  <== 不一致"))

p("")
p("RATE: %s" % json.dumps(gc.RATE.snapshot(), ensure_ascii=False))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written %s (%d lines)" % (OUT, len(lines)))
snap = gc.RATE.snapshot()
print("RATE remaining=%s used=%s" % (snap.get("remaining"), snap.get("used")))
