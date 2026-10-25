"""独立验证：官方 stargazers/history 原始响应 vs 库内周值。

只读，不改库。输出落 /app/data/verify_truth.txt。
"""
import json
import os
import sys

sys.path.insert(0, "/app")

from app import db
from app.collectors import github_client as gc
from app.collectors.history_collector import _to_weeks

OUT = "/app/data/verify_truth.txt"
NAMES = [
    "deepseek-ai/deepseek-harness",
    "NousResearch/hermes-agent",
    "mattpocock/skills",
    "odysseus-dev/odysseus",
    "VoltAgent/awesome-design-md",
]

lines = []


def p(s=""):
    lines.append(str(s))


cli = gc.get_client()
for name in NAMES:
    p("=" * 70)
    p("REPO %s" % name)
    raw = cli.stargazers_history(name)
    p("raw items=%d" % len(raw or []))
    if raw:
        keys = sorted(raw[0].keys())
        p("raw[0] keys=%s" % keys)
        d0 = (raw[0].get("days") or [])
        p("raw[0].days type=%s len=%d elem0=%r" % (type(d0).__name__, len(d0), d0[0] if d0 else None))
    weeks, days = _to_weeks(raw)
    p("_to_weeks: weeks=%d days=%d" % (len(weeks), len(days)))
    if days:
        p("days[0]=%r" % (days[0],))
        pos = sum(1 for d in days if int(d.get("stars") or 0) > 0)
        p("days with stars>0: %d / %d" % (pos, len(days)))

    api = {str(w["week_start"]): int(w["weekly_new"]) for w in weeks}
    rows = db.query_all(
        "SELECT to_char(week_start,'YYYY-MM-DD') AS ws, weekly_new, "
        "jsonb_array_length(days) AS dlen FROM gh_repo_star_weekly "
        "WHERE repo_id = (SELECT id FROM gh_repo WHERE full_name = %s) ORDER BY week_start", [name])
    dbm = {r["ws"]: (int(r["weekly_new"]), int(r["dlen"])) for r in rows}
    p("api weeks=%d db weeks=%d" % (len(api), len(dbm)))
    p("%-12s %10s %10s %6s %6s" % ("week", "api_total", "db_weekly", "api_d", "db_d"))
    diff = 0
    for k in sorted(set(api) | set(dbm)):
        a = api.get(k)
        b = dbm.get(k)
        ad = next((len(w["days"]) for w in weeks if str(w["week_start"]) == k), None)
        if a != (b[0] if b else None):
            diff += 1
        p("%-12s %10s %10s %6s %6s %s" % (k, a, b[0] if b else "-", ad, b[1] if b else "-",
                                          "" if a == (b[0] if b else None) else "  <== MISMATCH"))
    p("mismatched weeks: %d" % diff)

p("")
p("RATE: %s" % json.dumps(gc.RATE.snapshot(), ensure_ascii=False))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written %s (%d lines)" % (OUT, len(lines)))
print("RATE=%s" % json.dumps({k: v for k, v in gc.RATE.snapshot().items() if k != "token"}, ensure_ascii=False))
