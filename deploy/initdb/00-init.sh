#!/bin/bash
# PostgreSQL 首次初始化（只在数据卷为空时由官方 entrypoint 执行一次）
#
# 规范：/seed 下的 SQL 文件**文件名即导入顺序**（01 → 10 → 20 → 25 → 30 → 40 → 70 → 99）。
#   01-schema.sql     建表 + 索引（纯 DDL）
#   10-config.sql     配置 / 任务 / 名单（COPY 块）
#   20-repo-*.sql     gh_repo 主数据分片
#   25-watch.sql      关注目标（外键依赖 gh_repo，故排在其后）
#   30-facts-*.sql    star 周/日事实分片（full 包才有）
#   40-detail-*.sql   参与者 / 版本分片（full 包才有）
#   70-pool.sql       历史池名次 + 序列复位（派生，导入后计算）
#   99-verify.sql     只读自检
#
# 末尾写哨兵 /var/lib/postgresql/data/.init_done：compose 的 healthcheck 依赖它，
# 确保 api 不会在数据导入中途启动。
set -e

PSQL="psql -v ON_ERROR_STOP=1 --username $POSTGRES_USER --dbname $POSTGRES_DB"
SENTINEL=/var/lib/postgresql/data/.init_done

rm -f "$SENTINEL"

echo "==> [initdb] 开始，按文件名顺序导入 /seed/*.sql"
n=0
for f in $(ls -1 /seed/*.sql 2>/dev/null | sort); do
  base=$(basename "$f")
  case "$base" in 99-verify.sql) continue ;; esac
  n=$((n + 1))
  printf '    - %-24s' "$base"
  $PSQL -q -f "$f"
  echo "ok"
done

if [ -f /seed/99-verify.sql ]; then
  echo "==> [initdb] 自检 99-verify.sql"
  $PSQL -q -f /seed/99-verify.sql || true
fi

echo "==> [initdb] 完成：处理 $n 个文件，gh_repo 现有 $($PSQL -tAc 'SELECT count(*) FROM gh_repo') 行"

date -Iseconds > "$SENTINEL"
echo "==> [initdb] 已写入哨兵 $SENTINEL"
