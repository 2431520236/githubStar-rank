#!/usr/bin/env bash
# 秒级重启（多环境）
#
# 用法:
#   ./restart.sh            # 默认 local 环境
#   ./restart.sh dev        # 重启 dev 环境
#
# 为什么快: 后端代码(./server/app)、工具(./server/tools)、页面(./server/web)、
# 供应商配置(./server/data) 全部是 bind mount, 已在容器里实时生效 ——
# 刷新页面即可看到前端改动; 改 Python 只需重启 api 进程, **既不重建镜像也不动 db**。
#
# 反例(慢的元凶):
#   docker compose restart            # 不带服务名 → 连 db 一起重启, 无谓等待探针
#   docker compose up -d --build      # 每次重建镜像(在 NFS 上跑 pip), 几十秒起
set -euo pipefail
cd "$(dirname "$0")"

ENV="${1:-local}"
ENV_FILE=".env.${ENV}"
if [ ! -f "$ENV_FILE" ]; then
  echo "!! 环境文件 $ENV_FILE 不存在（可用: $(ls -1 .env.* 2>/dev/null | sed 's/^\.env\.//' | grep -v example | tr '\n' ' ')）" >&2
  exit 1
fi

case "$ENV" in
  local) ENV_NAME="9952"; PROJECT="githubstar9952" ;;
  dev)   ENV_NAME="9951"; PROJECT="githubstar-dev" ;;
  *)     ENV_NAME="$ENV"; PROJECT="githubstar-$ENV" ;;
esac

PORT=$(grep -E '^GH_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2)
URL="http://localhost:${PORT}/api/system/health"

echo "==> 重启 api [$ENV] (探活 ${URL})"
start=$(date +%s)

docker compose -p "$PROJECT" restart api

for _ in $(seq 1 60); do
  if curl -sf -m 2 "$URL" >/dev/null 2>&1; then
    echo "==> api 就绪, 耗时 $(( $(date +%s) - start ))s"
    exit 0
  fi
  sleep 0.5
done

echo "!! api 30s 内未就绪, 请查看: docker compose -p $PROJECT logs --tail 50 api" >&2
exit 1
