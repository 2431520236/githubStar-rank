# AGENTS · 项目约束与开发约定

面向在本仓库里干活的自动化代理：动手前先读这里，改完按对应文档自检。

## 1. 项目是什么

自托管的 GitHub 榜单大屏：**FastAPI + PostgreSQL 15 + Docker Compose**，前端 Vue 3 零构建。
同步仓库 star 历史 → 算日/周/月/总榜 → 用自配模型生成中文概要 → 暗色数据大屏展示。

## 2. 文档索引

| 文档 | 内容 |
|---|---|
| `README.md` | 项目介绍、界面预览、两周 TOP25、快速开始、关键词 |
| `docs/安装部署与初始化数据.md` | 部署四步、数据从哪来、验证清单、备份回滚、上线与反代、多实例、排错 |
| `docs/系统设计.md` | 架构 / 表结构 / 接口 / 采集机制 / 前端实现 |
| `docs/数据来源与采集详解.md` | 数据源与采集链路 |
| `docs/性能优化与缓存策略.md` | 三层缓存与运行时开关 |
| `docs/sql/README.md` | SQL 资料包规范与导入顺序 |
| `docs/GitHub-Token-指南.md` | Token 申请与额度 |

## 3. 分层铁律

**Controller 不写 SQL，Service 不碰 HTTP。** 采集层只负责拉取与限速，业务规则放 Service 层。

## 4. 数据安全红线（最高优先级）

1. **已上线实例只读**：容器 `ghradar-api` / `ghradar-db`、数据卷 `githubstar_ghradar_pgdata`、端口 `9951` / `54321`。查看类操作（`curl`、`psql SELECT`、`docker ps`）随便做；**任何写、重启、重建、删卷，必须先拿到用户明确同意**。本仓库历史上发生过「删卷导致数据丢失」事故。
2. **业务表严禁 DROP / DELETE / UPDATE**：`gh_repo`、`gh_repo_star_daily`、`gh_repo_star_weekly`、`gh_rank_snapshot`、`gh_stat_period`。只允许改配置表（`gh_app_setting` 等）。
3. 动数据库前先确认有可用备份，并记录备份路径与时间点。
4. 严禁 `docker compose down -v`、`docker volume rm`、`--volumes` 类操作。

## 5. 公开与合规红线

本仓库对外公开。**禁止出现公司内网域名关键字、以及公司内网网段地址（10 点 192 段）**。为不把这些原文写进仓内，扫描命令用引号拆写（shell 会自动拼回完整词）：

```bash
git ls-files -z | xargs -0 grep -lIi 'z''te'          # 内网域名关键字
git ls-files -z | xargs -0 grep -lI '10''\.192\.'     # 内网网段
```

两条都必须 **0 命中**。注意 `server/data/*.tsv` 里偶有第三方 GitHub 组织名**恰好包含该关键字片段**（如某向量库组织名），属业务数据，不算违规、**禁止改动**；真正的违规是域名 / 主机名形态。

**代理**是允许的功能，取值默认留空，优先级：**显式 `GH_PROXY` > 系统 / 环境变量（`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY`）> 直连**。实现见 `server/app/collectors/github_client.py`（`trust_env` 保持默认 True，显式配置写入 `session.proxies`）。

## 6. 多实例隔离

要另起一套实例用 `scripts/deploy-instance.sh`：目录、compose 项目名（`-p`）、容器名、数据卷名、端口**全部独立**。

- 不得复用已有容器名 / 数据卷 / 端口 —— 同名容器会互相顶掉，共用卷会让两套写同一份库。
- 不要在已有实例目录里裸跑 `docker compose up -d`（会按目录名落到默认项目名，可能顶掉已有容器）。