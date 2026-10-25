#!/usr/bin/env bash
# 秒级重启: 只重启 api 容器。
#
# 为什么快: 后端代码(./server/app)、工具(./server/tools)、页面(./server/web)、
# 供应商配置(./server/data) 全部是 bind mount, 已在容器里实时生效 ——
# 刷新页面即可看到前端改动; 改 Python 只需重启 api 进程, **既不重建镜像也不动 db**。
#
# 用法:
#   ./restart.sh          # 只重启 api, 并轮询等待就绪
#
# 反例(慢的元凶):
#   docker compose restart            # 不带服务名 → 连 db 一起重启, 无谓等待探针
#   docker compose up -d --build      # 每次重建镜像(在 NFS 上跑 pip), 几十秒起
set -euo pipefail
cd "$(dirname "$0")"

PORT="${GH_PORT:-9951}"
URL="http://localhost:${PORT}/api/system/health"

echo "==> 重启 api (探活 ${URL})"
start=$(date +%s)

docker compose restart api

for _ in $(seq 1 60); do
  if curl -sf -m 2 "$URL" >/dev/null 2>&1; then
    echo "==> api 就绪, 耗时 $(( $(date +%s) - start ))s"
    exit 0
  fi
  sleep 0.5
done

echo "!! api 30s 内未就绪, 请查看: docker logs --tail 50 ghradar-api" >&2
exit 1
