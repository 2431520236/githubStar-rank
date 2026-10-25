#!/bin/bash
# SQL 分批量导入工具
# 说明：用于大数据文件（4-1~4-10）的高效导入

set -euo pipefail

# 配置
SQL_DIR="${SQL_DIR:-./docs/sql}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-54321}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-github}"

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}==> SQL 导入工具${NC}"
echo "  目录: $SQL_DIR"
echo "  数据库: $DB_HOST:$DB_PORT/$DB_NAME"

# 1. 导入结构 (DDL)
echo -e "\n${YELLOW}步骤 1: 导入数据库结构${NC}"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$SQL_DIR/01-schema.sql" && \
echo -e "${GREEN}✓ 结构导入完成${NC}"

# 2. 导入种子数据
echo -e "\n${YELLOW}步骤 2: 导入种子数据${NC}"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$SQL_DIR/02-seed-config.sql" && \
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$SQL_DIR/03-llm-providers.sql" && \
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$SQL_DIR/04-gh_repo-data.sql" && \
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$SQL_DIR/05-pool-and-github-id.sql" && \
echo -e "${GREEN}✓ 种子数据导入完成${NC}"

# 3. 批量导入主数据 (4-1~4-10)
echo -e "\n${YELLOW}步骤 3: 批量导入主数据 (约 90MB)${NC}"
count=0
for f in "$SQL_DIR"/4-*; do
    if [[ -f "$f" && "$(basename "$f")" != "4-gh_repo-data.sql" ]]; then
        filename=$(basename "$f")
        echo -n "  导入 $filename ... "
        if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -q -f "$f" 2>/dev/null; then
            echo -e "${GREEN}✓${NC}"
            ((count++)) || true
        else
            echo -e "${RED}✗ 失败${NC}"
        fi
    fi
done

if [[ $count -gt 0 ]]; then
    total=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT count(*) FROM gh_repo" | xargs)
    echo -e "${GREEN}✓ 完成! 导入 $count 个文件, total $total 行${NC}"
else
    echo -e "${YELLOW}⚠ 无需导入主数据文件${NC}"
fi

echo -e "\n${GREEN}==> 所有 SQL 导入完成${NC}"