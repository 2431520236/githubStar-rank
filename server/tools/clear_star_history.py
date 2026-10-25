#!/usr/bin/env python3
"""清空 star 历史表（周/日），先备份再删，逐句校验（用户 2026-10 定：全量删除，全部按 top3000 重建）。

为什么单独写成脚本：之前用 `python3 -c` 内联多语句 + 复合 shell 命令，转义把整条命令打散了，
导致备份没落、日志为空、表状态看不清。脚本文件没有转义问题。

用法（容器内跑）:
  docker compose exec -T api python3 tools/clear_star_history.py --only weekly
  docker compose exec -T api python3 tools/clear_star_history.py --only both
"""
import argparse
import sys

sys.path.insert(0, "/app")
from app import db  # noqa: E402

TABLES = ("gh_repo_star_weekly", "gh_repo_star_daily")


def count(table):
    return db.query_scalar("SELECT count(*) FROM %s" % table)


def clear_one(table):
    before = count(table)
    bak = table + "_bak"
    db.execute("DROP TABLE IF EXISTS %s" % bak)
    db.execute("CREATE TABLE %s AS SELECT * FROM %s" % (bak, table))
    nb = count(bak)
    print("%-24s 备份 %s -> %d 行（原表 %d 行）" % (table, bak, nb, before), flush=True)
    if nb != before:
        print("!! 备份行数不一致，中止删除（防止丢数据）", flush=True)
        return False
    db.execute("DELETE FROM %s" % table)
    after = count(table)
    print("%-24s 已清空：before=%d after=%d" % (table, before, after), flush=True)
    return after == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="both", choices=["weekly", "daily", "both"])
    args = ap.parse_args()
    targets = {"weekly": TABLES[:1], "daily": TABLES[1:], "both": TABLES}[args.only]
    ok = True
    for t in targets:
        try:
            ok = clear_one(t) and ok
        except Exception as e:  # noqa: BLE001
            print("%-24s 失败: %s" % (t, str(e)[:160]), flush=True)
            ok = False
    print("CLEAR_RESULT", "OK" if ok else "PARTIAL", flush=True)


if __name__ == "__main__":
    main()
