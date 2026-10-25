#!/usr/bin/env bash
# 加载 .env 后启动(供 systemd / 裸机使用)
set -e
cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
exec "${PYTHON:-python3}" run.py
