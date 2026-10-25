# Docker 部署最佳实践

## 1. 环境准备

```bash
# 安装 Docker & Docker Compose
sudo apt update
sudo apt install -y docker.io docker-compose
sudo usermod -aG docker $USER  # 重启后生效
```

## 2. 一键启动

```bash
# 复制配置
cp .env.example .env

# 编辑配置 (请务必修改以下内容)
nano .env
#   GH_GITHUB_TOKEN=你的token   # 可选：留空也能跑，只是同步任务跳过
#   GH_ADMIN_USER=github        # 默认账密均 github
#   GH_ADMIN_PASSWORD=github
#   GH_AUTH_SECRET=随机密钥

# 启动服务
docker compose up -d

# 查看状态
docker compose ps
```

## 3. 数据持久化

### 数据卷位置
```bash
# 查看数据卷
docker volume inspect githubstar_ghradar_pgdata

# 物理路径 (通常在)
/var/lib/docker/volumes/githubstar_ghradar_pgdata/_data
```

### 备份恢复
```bash
# 备份
docker compose exec -T db pg_dump -U postgres github > backup_$(date +%F).sql

# 恢复
docker compose exec -T db psql -U postgres github < backup.sql

# 导出结构
docker compose exec -T db pg_dump -U postgres github --schema-only > schema.sql

# 只导入数据
docker compose exec -T db psql -U postgres github < data_only.sql
```

### 重置数据
```bash
# 保留容器，清空数据卷
docker compose down -v
docker compose up -d

# 完整重建
docker compose down
docker builder prune -f
docker compose up -d --build
```

## 4. 常见操作

| 操作 | 命令 |
|------|------|
| 查看日志 | `docker compose logs -f` |
| 只看 API 日志 | `docker compose logs -f api` |
| 重启 API (3秒) | `./restart.sh` |
| 重启 DB | `docker compose restart db` |
| 查看容器资源 | `docker stats` |
| Enter 容器 | `docker compose exec api sh` |

## 5. 性能优化

### 调整内存
编辑 `docker-compose.yml`：

```yaml
services:
  db:
    # 增加 shared_buffers
    command: >
      postgres
      -c shared_buffers=2GB    # 根据内存调整
      -c effective_cache_size=4GB
```

### 调整并发数
```bash
# 在 .env 中修改
GH_CONCURRENCY=10
```

## 6. 本地 PostgreSQL

如果已有本地 PostgreSQL，可只运行 API：

```bash
# 1. 确保 PostgreSQL 已启动
createdb github
psql -d github -c "ALTER USER postgres PASSWORD 'postgres';"

# 2. 修改 .env
GH_DB_HOST=127.0.0.1
GH_DB_PORT=5432

# 3. 取消 db 服务
# 从 docker-compose.yml 删除 db 服务及其依赖

# 4. 导入数据
docker compose run --rm api python3 /app/tools/load_data.py --seed-dir /seed

# 5. 启动
docker compose up -d
```

## 7. 环境变量速查

| 变量 | 说明 | 示例 |
|------|------|------|
| `GH_PORT` | 服务端口 | 9951 |
| `GH_GITHUB_TOKEN` | GitHub Token | ghp_xxx |
| `GH_DB_HOST` | 数据库主机 | localhost |
| `GH_DB_PASSWORD` | 数据库密码 | postgres |
| `GH_ADMIN_USER` | 管理员用户名 | github |
| `GH_ADMIN_PASSWORD` | 管理员密码 | github |
| `GH_AUTH_SECRET` | JWT 密钥 | 随机字符串 |
| `GH_CONCURRENCY` | 采集并发 | 6 |

## 8. 故障排查

```bash
# 容器启动失败
docker compose logs db

# 数据库连接失败
docker compose exec api env | grep GH_DB

# 采集限速
curl http://localhost:9951/api/system/rate-limit

# 健康检查
curl http://localhost:9951/api/system/health
```