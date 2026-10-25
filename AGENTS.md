# githubstar 项目约束（AGENTS）

## ⛔ 最高优先级：禁止擅自变更 9951 线上服务

来源：用户 2026-10-21 强令，原话「绝对禁止擅自修改变更 9951 服务的东西」。

适用范围：**任何**涉及 9951 服务与 `ghradar-db`（54321）的操作，包括但不限于：

- `docker compose down` / `up` / `restart` / `recreate`
- `docker volume rm githubstar_ghradar_pgdata`（**已发生事故：删卷导致数据丢失**）
- `DROP DATABASE` / `CREATE DATABASE` / 批量导入 SQL 到线上库
- 修改 `docker-compose.yml`、`.env`、`server/` 下任何被挂载进容器的文件

规则：

1. 9951 是用户正在使用的线上环境，**只读查看可以做**（curl 接口、psql SELECT、docker ps），**任何写/重启/重建必须先获得用户明确同意**。
2. 要在别的端口试部署：**另建独立目录 + 独立 compose 项目名（-p）+ 独立数据卷名 + 独立端口**，不得复用 `ghradar-db`/`ghradar-api` 容器名、不得复用 `githubstar_ghradar_pgdata` 卷、不得复用 9951/54321 端口。
3. 严禁对线上数据卷执行任何 `rm`/`prune`/`down -v` 类操作。
4. 动数据库前先确认有可用备份，并记录备份路径与时间点。

## 9952 隔离实例（2026-10-21 建立）

- 目录：`githubstar-9952/`（独立代码副本，改动不互通）
- 启动：`docker compose -p githubstar9952 up -d`
- 端口：api **9952**、db **54322**；容器 `ghradar-api-9952` / `ghradar-db-9952`
- 数据卷：`ghradar_pgdata_9952`（与线上 `githubstar_ghradar_pgdata` 无关）
- 该实例 `GH_GITHUB_TOKEN` 为空、无 `llm_config.json`、调度器关闭 → 不消耗配额

**隔离验证方法**（每次都跑，别只靠口头）：

```bash
docker ps --format '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Ports}}' | grep ghradar
docker volume ls | grep ghradar
curl -s localhost:9951/api/stats/screen | grep -o '"history_done":[0-9]*'
curl -s localhost:9952/api/stats/screen | grep -o '"history_done":[0-9]*'   # 9951=4084 / 9952=0 → 各读各库
```

## 公开与合规红线（2026-10-26 用户强制）

- 本仓库对外公开（github.com/2431520236/githubstar-rank）。**禁止出现任何 `zte` 相关字样**（域名、路径、代理、注释均含）、**禁止出现任何 `10.192.*` 内网地址**。改动后必须复扫：
  `git ls-files -z | xargs -0 grep -lIi 'zte'` 与 `grep -rIil '10\.192\.[0-9]'`，必须 0 命中。
- 代理是允许的功能，但取值默认留空；**代理策略：显式 `GH_PROXY` > 系统/环境变量（`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY`）> 直连**。实现见 `server/app/collectors/github_client.py`（`trust_env` 保持默认 True，显式配置写入 `session.proxies`）。
- 数据库只允许改配置表（`gh_app_setting` 等）；**业务表（`gh_repo`/`gh_repo_star_daily`/`gh_repo_star_weekly`/`gh_rank_snapshot`/`gh_stat_period`）严禁 DROP/DELETE/UPDATE**。

## 备份与恢复


- 现有备份：`bark/githubstar_backup/star_backup_before_pool_20261014_161438.sql.gz`（130MB，含 gh_repo 72 万 / star_daily 125 万 / star_weekly 23 万 / rank_snapshot 16 万 / stat_period 59）
- 恢复方式：建空库 → `zcat 备份 | psql -U postgres -d github` → `psql -f docs/sql/01-schema.sql` 补建其余 11 张表

## 数据规模基线（2026-10-14 备份）

| 表 | 行数 |
|---|---|
| gh_repo | 721,562 |
| gh_repo_star_daily | 1,255,674 |
| gh_repo_star_weekly | 233,922 |
| gh_rank_snapshot | 165,949 |
| gh_stat_period | 59 |

注：`docs/sql/4-1~4-10` 种子只有 52,736 行，是旧快照，**不代表线上真实规模**。
