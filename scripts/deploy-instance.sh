#!/usr/bin/env bash
# 一键起一套隔离实例（端口全部 +1），或一键删掉它。
#
#   ./scripts/deploy-instance.sh                 # 起 9952（DB 54322）
#   ./scripts/deploy-instance.sh --port 9953     # 起 9953（DB 54323）
#   ./scripts/deploy-instance.sh --sql /path/sql-full   # 指定数据包（full 包才有四榜）
#   ./scripts/deploy-instance.sh --down          # 删掉这套实例（容器+卷+目录）
#
# 四条隔离线：目录、compose 项目名、容器名、数据卷；端口一律 +1。
# 脚本全程只读源目录，绝不动线上那套。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=9952
SQL_DIR=""
DOWN=0
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --port)   PORT="$2"; shift 2 ;;
    --sql)    SQL_DIR="$2"; shift 2 ;;
    --down)   DOWN=1; shift ;;
    --force)  FORCE=1; shift ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

SUFFIX="$PORT"                                  # 9952
PROJECT="githubstar${SUFFIX}"                   # githubstar9952
DB_PORT=$(( 54321 + PORT - 9951 ))              # 9952 → 54322
TARGET="$(cd "$SRC/.." && pwd)/githubstar-${SUFFIX}"
API="ghradar-api-${SUFFIX}"
DB="ghradar-db-${SUFFIX}"
VOL="ghradar_pgdata_${SUFFIX}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 删除模式 ----------
if [ "$DOWN" = 1 ]; then
  [ -d "$TARGET" ] || die "目录不存在: $TARGET"
  say "停止并删除实例 $PROJECT"
  ( cd "$TARGET" && docker compose -p "$PROJECT" down -v --remove-orphans ) || true
  docker rm -f "$API" "$DB" >/dev/null 2>&1 || true
  docker volume rm "$VOL" >/dev/null 2>&1 || true
  rm -rf "$TARGET"
  say "已删除：容器 $API/$DB、卷 $VOL、目录 $TARGET"
  exit 0
fi

# ---------- 前置检查 ----------
[ -f "$SRC/docker-compose.yml" ] || die "源目录不像项目根: $SRC"
docker compose version >/dev/null 2>&1 || die "需要 docker compose v2"
if [ "$TARGET" = "$SRC" ]; then die "目标目录与源目录相同，拒绝执行"; fi
if [ -d "$TARGET" ] && [ "$FORCE" != 1 ]; then
  die "目标已存在: $TARGET（先 --down，或加 --force 覆盖）"
fi
if ss -tln 2>/dev/null | grep -q ":${PORT} "; then die "端口 ${PORT} 已被占用"; fi

say "源目录 : $SRC"
say "目标目录: $TARGET"
say "端口    : API ${PORT} / DB ${DB_PORT}"
say "项目名  : $PROJECT   容器: $API,$DB   卷: $VOL"

# ---------- 1. 复制源码（不带 .env / 数据包 / 本地测试目录） ----------
rm -rf "$TARGET"; mkdir -p "$TARGET/docs/sql" "$TARGET/server/data"
( cd "$SRC" && tar -c \
    --exclude=.env --exclude=.git --exclude=profiles --exclude=test-deploy \
    --exclude='server/data/sql-export*' --exclude='server/data/llm_config.json' \
    --exclude='docs/sql' . 2>/dev/null ) | tar -x -C "$TARGET"
say "源码已复制"

# ---------- 2. 生成 .env（端口 +1，其余沿用；Token 一并带入） ----------
if [ -f "$SRC/.env" ]; then
  sed -e "s/^GH_PORT=.*/GH_PORT=${PORT}/" \
      -e "s/^GH_DB_PORT_HOST=.*/GH_DB_PORT_HOST=${DB_PORT}/" "$SRC/.env" > "$TARGET/.env"
else
  sed -e "s/^GH_PORT=.*/GH_PORT=${PORT}/" \
      -e "s/^GH_DB_PORT_HOST=.*/GH_DB_PORT_HOST=${DB_PORT}/" "$SRC/.env.example" > "$TARGET/.env"
  say "源目录没有 .env，已从 .env.example 生成（Token 需自己填）"
fi
chmod 600 "$TARGET/.env"
say ".env 已生成（GH_PORT=${PORT} / GH_DB_PORT_HOST=${DB_PORT}）"

# ---------- 3. 改容器名与卷名（不改会顶掉线上容器） ----------
sed -i -e "s/container_name: ghradar-db\$/container_name: ${DB}/" \
       -e "s/container_name: ghradar-api\$/container_name: ${API}/" \
       -e "s/ghradar_pgdata/${VOL}/g" "$TARGET/docker-compose.yml"
# 复用已构建镜像，避免重复 build；全新机器首次会自动 build
sed -i "s|^  api:\$|  api:\n    image: ghradar-api:${SUFFIX}|" "$TARGET/docker-compose.yml"
if docker image inspect "githubstar-api:latest" >/dev/null 2>&1; then
  docker tag githubstar-api:latest "ghradar-api:${SUFFIX}"
  say "已复用现有镜像 ghradar-api:${SUFFIX}（无需 build）"
fi

# ---------- 4. 数据包 ----------
if [ -n "$SQL_DIR" ]; then
  [ -d "$SQL_DIR" ] || die "数据包目录不存在: $SQL_DIR"
  cp -a "$SQL_DIR"/*.sql "$SQL_DIR"/MANIFEST.json "$TARGET/docs/sql/" 2>/dev/null || true
  say "已使用指定数据包: $SQL_DIR"
else
  cp -a "$SRC"/docs/sql/*.sql "$SRC"/docs/sql/MANIFEST.json "$TARGET/docs/sql/" 2>/dev/null || true
  say "已使用源目录数据包: $SRC/docs/sql"
fi
if ! ls "$TARGET"/docs/sql/30-weekly-*.sql >/dev/null 2>&1; then
  printf '\033[1;33m   ⚠ 包里没有 30-weekly-*.sql：部署后日/周/月榜会是空的（lite 包）。\n'
  printf '     要四榜有数据：--sql /path/to/sql-full，或部署后跑采集。\033[0m\n'
fi

# ---------- 5. 启动 ----------
say "启动中…"
( cd "$TARGET" && docker compose -p "$PROJECT" up -d ) >/dev/null
printf '    等待初始化'
for i in $(seq 1 180); do
  if docker exec "$DB" sh -c 'test -f /var/lib/postgresql/data/.init_done' 2>/dev/null; then
    printf ' 完成（约 %ds）\n' $((i*5)); break
  fi
  printf '.'; sleep 5
  [ "$i" = 180 ] && { printf '\n'; docker logs "$DB" 2>&1 | tail -20; die "初始化超时"; }
done

# ---------- 6. 生成榜单快照（派生表，包里不带） ----------
if [ "$(docker exec "$DB" psql -U postgres -d github -tAc 'select count(*) from gh_rank_snapshot' 2>/dev/null || echo 0)" = "0" ]; then
  say "生成榜单快照（首次必做）"
  docker exec -i "$API" python3 - <<'PY' 2>&1 | sed 's/^/    /'
import sys; sys.path.insert(0,'/app')
from app.services import snapshot_service, facts_service
from app.services.rank_service import period_bounds
facts_service.rollup_gains()
d,_=period_bounds('day',None); w,_=period_bounds('week',None); m,_=period_bounds('month',None)
for pt,pk in (('day',d),('week',snapshot_service._iso_week(w)),('month',m[:7])):
    for mt in ('gain','total'):
        print(pt, snapshot_service.compute_rank(pt,pk,mt).get('status'))
PY
fi

# ---------- 7. 验证 ----------
sleep 3
say "数据核对"
docker exec "$DB" psql -U postgres -d github -tAc "
select '    仓库='||(select count(*) from gh_repo)
     ||' 周事实='||(select count(*) from gh_repo_star_weekly)
     ||' 日事实='||(select count(*) from gh_repo_star_daily)
     ||' 快照='||(select count(*) from gh_rank_snapshot);"

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
CODE=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/" || echo 000)
say "页面: http://${IP:-localhost}:${PORT}/  (HTTP ${CODE})"
[ "$CODE" = "200" ] || die "首页不是 200，看日志: docker logs ${API}"

say "完成。删除这套实例: $0 --down --port ${PORT}"
