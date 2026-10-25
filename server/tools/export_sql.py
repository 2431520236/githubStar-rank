#!/usr/bin/env python3
"""从在线库导出规范 SQL 资料包。

设计原则（用户口径）：
  1. 结构 SQL 与表数据 SQL **物理分离**；
  2. 数据量小的表**集中**在一个文件，数据量大的表**单独拆分**成多个文件；
  3. 每个数据文件带行数与 md5，导入后可逐表核对，不靠"看起来对"；
  4. 派生表（统计结果 / 运行日志）默认**不导出** —— 它们是算出来的，不是种子；
  5. 导出过程对源库**只读**。

用法（在 api 容器内执行）：
    python3 tools/export_sql.py --out /app/data/sql-export --profile lite
    python3 tools/export_sql.py --out /app/data/sql-export --profile full --chunk-rows 200000

profile：
    lite  = 结构 + 配置小表 + star>=50 的仓库（公开数据集，体积可控）
    full  = 结构 + 配置小表 + 全部仓库 + 全部事实层 + 明细
"""
import argparse
import hashlib
import json
import os
import sys
import datetime

import psycopg2

# (表名, 分组, 是否派生, 是否运行日志)
TABLES = [
    ("gh_app_setting",       "config",  False, False),
    ("gh_llm_task",          "config",  False, False),
    ("gh_llm_provider",      "config",  False, False),
    ("gh_llm_model",         "config",  False, False),
    ("gh_rank_whitelist",    "config",  False, False),
    ("gh_repo",              "repo",    False, False),
    # 依赖 gh_repo 外键，必须排在 repo 之后
    ("gh_watch_target",      "postrepo", False, False),
    ("gh_repo_star_weekly",  "facts",   False, False),
    ("gh_repo_star_daily",   "facts",   False, False),
    ("gh_repo_contributor",  "detail",  False, False),
    ("gh_repo_release",      "detail",  False, False),
    ("gh_stat_period",       "derived", True,  False),
    ("gh_rank_snapshot",     "derived", True,  False),
    ("gh_job_log",           "runtime", False, True),
    ("gh_llm_run",           "runtime", False, True),
    ("gh_watch_event",       "runtime", False, True),
]

# 小表组：整组写进一个文件（config 在前，postrepo 在 repo 之后）
SINGLE_FILE_GROUPS = {"config": "10-config", "postrepo": "25-watch"}

# 分片文件名前缀按**表**区分（同组多表共用前缀会互相覆盖）
TABLE_PREFIX = {
    "gh_repo":             "20-repo",
    "gh_repo_star_weekly": "30-weekly",
    "gh_repo_star_daily":  "35-daily",
    "gh_repo_contributor": "40-contributor",
    "gh_repo_release":     "45-release",
    "gh_stat_period":      "50-stat-period",
    "gh_rank_snapshot":    "55-rank-snapshot",
    "gh_job_log":          "60-job-log",
    "gh_llm_run":          "65-llm-run",
    "gh_watch_event":      "68-watch-event",
}


def connect():
    return psycopg2.connect(
        host=os.getenv("GH_DB_HOST", "db"),
        port=int(os.getenv("GH_DB_PORT", "5432")),
        dbname=os.getenv("GH_DB_NAME", "github"),
        user=os.getenv("GH_DB_USER", "postgres"),
        password=os.getenv("GH_DB_PASSWORD", ""),
    )


def columns(cur, table):
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def count_rows(cur, table, where):
    cur.execute("SELECT count(*) FROM %s %s" % (table, where))
    return cur.fetchone()[0]


def copy_block(cur, table, cols, where, order_col="id"):
    """把一个查询以 COPY ... TO STDOUT 的字节流返回（按主键排序，保证 md5 可复现）。"""
    col_list = ", ".join('"%s"' % c for c in cols)
    sql = 'COPY (SELECT %s FROM %s %s ORDER BY %s) TO STDOUT' % (
        col_list, table, where, order_col)
    buf = []
    cur.copy_expert(sql, _Collector(buf))
    return b"".join(buf)


class _Collector:
    def __init__(self, sink):
        self.sink = sink

    def write(self, b):
        self.sink.append(b)

    def flush(self):
        pass


def verify(manifest_path, profile):
    """把 MANIFEST 里的行数与 md5 拿到目标库重算一遍，逐表比对。"""
    man = json.load(open(manifest_path, encoding="utf-8"))
    profile = profile or man.get("profile", "lite")
    conn = connect()
    conn.set_session(readonly=True)
    cur = conn.cursor()
    cur.execute("SET TIME ZONE 'UTC'")   # 与导出端保持一致
    ok = fail = 0
    print("== 校验目标库（profile=%s）" % profile)
    for key, info in man["tables"].items():
        if key.startswith("_"):
            for sub in info["tables"]:
                t = sub["table"]
                cols = columns(cur, t)
                where = ""
                rows = count_rows(cur, t, where)
                blob = copy_block(cur, t, cols, where, order_col=cols[0])
                md5 = hashlib.md5(blob).hexdigest()
                good = rows == sub["rows"] and md5 == sub["md5"]
                print("   %-24s 期望 %8d / 实际 %8d  md5 %s  %s"
                      % (t, sub["rows"], rows, "一致" if md5 == sub["md5"] else "不一致",
                         "PASS" if good else "FAIL"))
                ok += good
                fail += (not good)
            continue

        t = key
        cols = columns(cur, t)
        where = "WHERE stars >= 50" if (profile == "lite" and t == "gh_repo") else ""
        rows = count_rows(cur, t, where)
        blob = copy_block(cur, t, cols, where)
        md5 = hashlib.md5(blob).hexdigest()
        good = rows == info["rows"] and md5 == info["md5"]
        print("   %-24s 期望 %8d / 实际 %8d  md5 %s  %s"
              % (t, info["rows"], rows, "一致" if md5 == info["md5"] else "不一致",
                 "PASS" if good else "FAIL"))
        ok += good
        fail += (not good)
    print("== 结果: PASS %d / FAIL %d" % (ok, fail))
    return 0 if fail == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--profile", default="lite", choices=["lite", "full"])
    ap.add_argument("--chunk-rows", type=int, default=200000)
    ap.add_argument("--include-derived", action="store_true")
    ap.add_argument("--include-runtime", action="store_true")
    ap.add_argument("--verify", metavar="MANIFEST.json",
                    help="不导出，改为按清单校验当前连接的库")
    args = ap.parse_args()

    if args.verify:
        return verify(args.verify, args.profile)
    if not args.out:
        ap.error("--out 必填（或用 --verify）")

    out = args.out
    os.makedirs(out, exist_ok=True)
    conn = connect()
    conn.set_session(readonly=True)          # 对源库只读，物理上不可能写坏
    cur = conn.cursor()
    # 固定会话时区：timestamptz 的文本表示与服务器时区无关，
    # 否则源库(Asia/Shanghai)与校验库(UTC)的 md5 永远对不上。
    cur.execute("SET TIME ZONE 'UTC'")

    manifest = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "profile": args.profile,
        "chunk_rows": args.chunk_rows,
        "source": {"db": os.getenv("GH_DB_NAME", "github"),
                   "server": os.getenv("GH_DB_HOST", "db")},
        "tables": {},
    }

    print("== profile=%s out=%s" % (args.profile, out))
    for table, group, derived, runtime in TABLES:
        if derived and not args.include_derived:
            print("   skip %-24s (派生表，不导出)" % table)
            continue
        if runtime and not args.include_runtime:
            print("   skip %-24s (运行日志，不导出)" % table)
            continue

        cols = columns(cur, table)
        if not cols:
            print("   skip %-24s (表不存在)" % table)
            continue

        where = ""
        if args.profile == "lite" and table == "gh_repo":
            where = "WHERE stars >= 50"
        if args.profile == "lite" and table in ("gh_repo_star_weekly", "gh_repo_star_daily"):
            print("   skip %-24s (lite 不含事实层)" % table)
            continue
        if args.profile == "lite" and table in ("gh_repo_contributor", "gh_repo_release"):
            print("   skip %-24s (lite 不含明细)" % table)
            continue

        total = count_rows(cur, table, where)
        if total == 0:
            print("   skip %-24s (0 行)" % table)
            continue

        # 小表集中：整组写进一个文件（config → 10-config.sql，postrepo → 25-watch.sql）
        if group in SINGLE_FILE_GROUPS:
            key = SINGLE_FILE_GROUPS[group]
            manifest["tables"].setdefault("_" + key, {"rows": 0, "tables": []})
            blob = copy_block(cur, table, cols, where, order_col=cols[0])
            md5 = hashlib.md5(blob).hexdigest()
            manifest["tables"]["_" + key]["tables"].append(
                {"table": table, "rows": total, "md5": md5})
            manifest["tables"]["_" + key]["rows"] += total
            _append_config(out, key, table, cols, blob)
            print("   %-24s %8d 行 → %s.sql" % (table, total, key))
            continue

        # 大表分片
        prefix = TABLE_PREFIX.get(table)
        if not prefix:
            raise SystemExit("缺少分片前缀定义: %s" % table)
        cur.execute("SELECT min(id), max(id) FROM %s %s" % (table, where))
        lo, hi = cur.fetchone()
        files, table_md5, done = [], hashlib.md5(), 0
        idx = 0
        a = lo
        while a <= hi:
            b = a + args.chunk_rows - 1
            sub_where = _and(where, "id BETWEEN %d AND %d" % (a, b))
            blob = copy_block(cur, table, cols, sub_where)
            if blob:
                idx += 1
                name = "%s-%03d.sql" % (prefix, idx)
                rows = blob.count(b"\n")
                head = (
                    "-- 表 %s  分片 %d  id %d..%d  约 %d 行\n"
                    "-- md5 %s\n"
                    "COPY %s (%s) FROM stdin;\n"
                    % (table, idx, a, b, rows, hashlib.md5(blob).hexdigest(),
                       table, ", ".join('"%s"' % c for c in cols))
                )
                with open(os.path.join(out, name), "wb") as f:
                    f.write(head.encode("utf-8"))
                    f.write(blob)
                    f.write(b"\\.\n\n")
                table_md5.update(blob)
                files.append(name)
                done += rows
            a = b + 1

        manifest["tables"][table] = {
            "rows": total, "exported": done, "md5": table_md5.hexdigest(), "files": files,
        }
        print("   %-24s %8d 行 → %d 个分片" % (table, total, len(files)))

    # 派生：历史池名次（算出来的，不导出数据）
    with open(os.path.join(out, "70-pool.sql"), "w", encoding="utf-8") as f:
        f.write(POOL_SQL)
    with open(os.path.join(out, "99-verify.sql"), "w", encoding="utf-8") as f:
        f.write(VERIFY_SQL)
    with open(os.path.join(out, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("== 完成，清单写入 MANIFEST.json")
    return 0


def _and(where, extra):
    return (where + " AND " + extra) if where else "WHERE " + extra


def _append_config(out, key, table, cols, blob):
    path = os.path.join(out, key + ".sql")
    new = not os.path.exists(path)
    with open(path, "ab") as f:
        if new:
            f.write(("-- %s：配置 / 名单 / 任务等小表（集中在一个文件）\n"
                     "-- COPY 格式，导入快且不受引号/转义影响\n\n" % key).encode("utf-8"))
        f.write(("-- ===== %s (%d 行) =====\nCOPY %s (%s) FROM stdin;\n"
                 % (table, blob.count(b"\n"), table,
                    ", ".join('"%s"' % c for c in cols))).encode("utf-8"))
        f.write(blob)
        f.write(b"\\.\n\n")


POOL_SQL = """-- 历史池名次（派生数据，导入后执行；幂等）
-- 口径：周/日历史与日周月榜 gain 只统计 star 排名前 2000 的仓库；总榜不受限。
UPDATE gh_repo SET in_history_pool = FALSE, pool_rank = NULL
 WHERE COALESCE(in_history_pool, FALSE) = TRUE OR pool_rank IS NOT NULL;

WITH ranked AS (
    SELECT id, row_number() OVER (ORDER BY stars DESC NULLS LAST, id) AS rn
      FROM gh_repo WHERE stars IS NOT NULL
)
UPDATE gh_repo r SET in_history_pool = TRUE, pool_rank = k.rn
  FROM ranked k WHERE r.id = k.id AND k.rn <= 2000;

-- 序列复位：COPY 显式写了 id，序列不会自动前进
SELECT setval(pg_get_serial_sequence('gh_llm_task','id'),       COALESCE((SELECT MAX(id) FROM gh_llm_task),1));
SELECT setval(pg_get_serial_sequence('gh_llm_provider','id'),   COALESCE((SELECT MAX(id) FROM gh_llm_provider),1));
SELECT setval(pg_get_serial_sequence('gh_llm_model','id'),      COALESCE((SELECT MAX(id) FROM gh_llm_model),1));
SELECT setval(pg_get_serial_sequence('gh_rank_whitelist','id'), COALESCE((SELECT MAX(id) FROM gh_rank_whitelist),1));
SELECT setval(pg_get_serial_sequence('gh_watch_target','id'),   COALESCE((SELECT MAX(id) FROM gh_watch_target),1));
SELECT setval(pg_get_serial_sequence('gh_repo','id'),           COALESCE((SELECT MAX(id) FROM gh_repo),1));
"""

VERIFY_SQL = """-- 导入后自检（只读）
\\echo '== 表数 =='
SELECT count(*) AS tables FROM information_schema.tables
 WHERE table_schema='public' AND table_type='BASE TABLE';

\\echo '== 各表行数 =='
SELECT 'gh_repo' t, count(*) FROM gh_repo
UNION ALL SELECT 'gh_repo_star_weekly',  count(*) FROM gh_repo_star_weekly
UNION ALL SELECT 'gh_repo_star_daily',   count(*) FROM gh_repo_star_daily
UNION ALL SELECT 'gh_repo_contributor',  count(*) FROM gh_repo_contributor
UNION ALL SELECT 'gh_repo_release',      count(*) FROM gh_repo_release
UNION ALL SELECT 'gh_rank_snapshot',     count(*) FROM gh_rank_snapshot
UNION ALL SELECT 'gh_stat_period',       count(*) FROM gh_stat_period
UNION ALL SELECT 'gh_app_setting',       count(*) FROM gh_app_setting
UNION ALL SELECT 'gh_llm_task',          count(*) FROM gh_llm_task
UNION ALL SELECT 'gh_rank_whitelist',    count(*) FROM gh_rank_whitelist
ORDER BY t;

\\echo '== 关键列 =='
SELECT count(*) total,
       count(*) FILTER (WHERE github_id IS NOT NULL) github_id_ok,
       count(*) FILTER (WHERE COALESCE(ai_summary,'') <> '') ai_ok,
       count(*) FILTER (WHERE COALESCE(in_history_pool,FALSE)) in_pool
  FROM gh_repo;
"""

if __name__ == "__main__":
    sys.exit(main())
