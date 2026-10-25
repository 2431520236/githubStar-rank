#!/usr/bin/env python3
"""探测 ClickHouse 公共数据集能否做「一周全表聚合 top-N」。只读，不写库。"""
import sys
import time
import urllib.request

CH_URL = "https://play.clickhouse.com/"
CH_USER = "explorer"


def ch(sql, timeout=300):
    req = urllib.request.Request(
        CH_URL + "?user=" + CH_USER + "&default_format=TSV",
        data=sql.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def probe(label, sql):
    t0 = time.time()
    try:
        out = ch(sql)
        lines = [l for l in out.splitlines() if l.strip()]
        print("%-22s | %5.1fs | rows=%-6d | %s"
              % (label, time.time() - t0, len(lines),
                 (lines[0][:110] if lines else "-")), flush=True)
    except Exception as e:  # noqa: BLE001
        print("%-22s | %5.1fs | FAIL | %s" % (label, time.time() - t0, e), flush=True)


W = "created_at >= '2026-10-05 00:00:00' AND created_at < '2026-10-10 00:00:00'"

probe("week_total", "SELECT count() AS n, uniqExact(repo_name) AS repos "
      "FROM github_events WHERE event_type = 'WatchEvent' AND " + W)
probe("week_top5", "SELECT repo_name, count() AS c FROM github_events "
      "WHERE event_type = 'WatchEvent' AND " + W +
      " GROUP BY repo_name ORDER BY c DESC LIMIT 5")
probe("week_top5000", "SELECT repo_name, count() AS c FROM github_events "
      "WHERE event_type = 'WatchEvent' AND " + W +
      " GROUP BY repo_name ORDER BY c DESC LIMIT 5000")
