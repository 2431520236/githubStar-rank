#!/usr/bin/env bash
# 启动 GitHub 飙升雷达（多环境）
#
# 用法:
#   ./up.sh            # 默认 local 环境（.env.local, 端口 9952）
#   ./up.sh local      # 本地开发环境（不提交 GitHub）
#   ./up.sh dev        # 发布/演示环境（.env.dev, 端口 9951）
#
# 环境文件 = .env.<环境名>，端口/数据库口令/GitHub Token/LLM 初始化全部来自该文件。
# DB 种子导入只在数据卷为空时发生（PostgreSQL 官方 entrypoint 语义 = 已有数据自动跳过）。
set -euo pipefail
cd "$(dirname "$0")"

ENV="${1:-local}"
ENV_FILE=".env.${ENV}"

if [ ! -f "$ENV_FILE" ]; then
  echo "!! 环境文件 $ENV_FILE 不存在" >&2
  echo "   可用环境: $(ls -1 .env.* 2>/dev/null | sed 's/^\.env\.//' | grep -v example | tr '\n' ' ')" >&2
  exit 1
fi

PORT=$(grep -E '^GH_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2)
# 环境 → compose 项目名 + 容器/卷后缀。
# ⚠️ local 的项目名必须是 githubstar9952 (现有卷前缀), 否则会换到空卷触发种子重导!
case "$ENV" in
  local) ENV_NAME="9952"; PROJECT="githubstar9952" ;;
  dev)   ENV_NAME="9951"; PROJECT="githubstar-dev" ;;
  *)     ENV_NAME="$ENV"; PROJECT="githubstar-$ENV" ;;
esac

echo "==> 环境: $ENV （$ENV_FILE）  端口 $PORT  项目 $PROJECT"

export GH_ENV_NAME="$ENV_NAME"
export GH_ENV="$ENV"

echo "==> docker compose up（--env-file $ENV_FILE）"
docker compose -p "$PROJECT" --env-file "$ENV_FILE" up -d --build

URL="http://localhost:${PORT}/api/system/health"
echo "==> 等待就绪: $URL"
start=$(date +%s)
for _ in $(seq 1 120); do
  if curl -sf -m 2 "$URL" >/dev/null 2>&1; then
    echo "==> 就绪, 耗时 $(( $(date +%s) - start ))s → http://localhost:${PORT}/"
    exit 0
  fi
  sleep 1
done
echo "!! 60s 内未就绪, 请查看: docker compose -p githubstar-${ENV} logs --tail 50" >&2
exit 1
