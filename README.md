# GitHub 飙升雷达 · GitHubStarRadar

**GitHub 排行榜 · GitHub 周榜 · GitHub 日榜 · GitHub 月榜 · GitHub 总榜 · 新兴项目发现 · star 历史曲线 · 自托管数据大屏**

> 一个自托管的 GitHub 开源项目数据大屏：**自动同步项目 star 历史 → 算日/周/月/总榜 → 生成中文项目概要**。
> 后端 FastAPI + PostgreSQL，前端 Vue 3 零构建，一条 `docker compose up -d` 全栈起来。

> **关键词**：GitHub 排行榜 · GitHub 周榜 · GitHub 日榜 · GitHub 月榜 · GitHub 总榜 · 新兴项目 · 飙升项目 · 涨星最快 · star 增长榜 · GitHub Trending 中文版 · 开源项目排行 · star 历史曲线 · 自托管数据大屏 · star-history 替代 · 开源项目数据看板 · GitHub 热门项目监控

---

## ⚡ 本目录 = 9952 隔离实例（先看这段）

本目录 `githubstar-9952/` 是 9951 线上实例的**完全隔离副本**，与线上不共用任何容器 / 卷 / 端口 / 数据。

| 项 | 值 |
|---|---|
| 访问地址 | `http://127.0.0.1:9952/`（本机 `http://localhost:9952/`） |
| compose 项目名 | `githubstar9952`（命令必须带 `-p githubstar9952`，否则会顶掉线上容器） |
| 容器 | `ghradar-api-9952` / `ghradar-db-9952` |
| 端口 | API **9952**、PG **54322** |
| 数据卷 | `ghradar_pgdata_9952` |
| 管理员 | 默认 `github` / `github`（页面右上角「改账密」可改，存库优先于 env） |

```bash
cd githubstar-9952
docker compose -p githubstar9952 up -d          # 启动 / 更新容器
docker compose -p githubstar9952 ps             # 状态
docker compose -p githubstar9952 restart api    # 只重启 API（改 Python 后，约 3 秒）
docker compose -p githubstar9952 logs -f api    # 日志
```

**从 9951 同步代码**（代码是 bind mount，改文件后 `restart api` 即生效，**不需要重建镜像**）：

```bash
rsync -a --exclude __pycache__ ../githubstar/server/app/ server/app/
rsync -a --exclude __pycache__ ../githubstar/server/web/ server/web/
docker compose -p githubstar9952 restart api
```

> ⚠️ 改了 `docker-compose.yml` 的 `environment` / `volumes`（如换 Token、改账密默认值）必须重建容器：
> `docker compose -p githubstar9952 rm -sf api && docker compose -p githubstar9952 create api && docker compose -p githubstar9952 start api`
> （`restart` 不重读 `.env`，会出现「配置改了但行为没变」）

本实例数据基线（2026-10-23 实测）：`gh_repo` 721,562 / 日事实 1,255,674 / 周事实 233,922 / 快照 9,985。

📄 **上线方式调研、发布流程、回滚与验收清单** → [`docs/上线指南.md`](docs/上线指南.md)（含四条上线路径对比、共享 Token 额度风险、红线清单）

---

## 目录

1. [它是什么](#一它是什么)
2. [核心功能](#二核心功能)
3. [三分钟上手（最不熟的走这条）](#三三分钟上手最不熟的走这条)
4. [GitHub Token 怎么拿](#四github-token-怎么拿)
5. [启动后会发生什么（初始化流程）](#五启动后会发生什么初始化流程)
6. [验证部署成功](#六验证部署成功)
7. [榜单/统计图没数据？先分清是哪一种（必读）](#七榜单统计图没数据先分清是哪一种必读)
8. [SQL 资料包怎么组织的](#八sql-资料包怎么组织的)
9. [数据库持久化与备份](#九数据库持久化与备份)
10. [常用运维命令](#十常用运维命令)
11. [同机再起一套实例（端口 +1，完全隔离）](#十一同机再起一套实例端口-1完全隔离)
12. [环境变量](#十二环境变量)
13. [目录结构](#十三目录结构)
14. [安全与脱敏](#十四安全与脱敏)
15. [常见问题](#十五常见问题)

---

## 一、它是什么

GitHub 上项目成千上万，但「谁在涨」这件事没有现成榜单。这个项目做的事：

1. **同步**：定期从 GitHub 官方接口拉取仓库详情与 star 历史曲线；
2. **算榜**：按 star 增量算日榜 / 周榜 / 月榜，按 star 总量算总榜；
3. **读懂**：调用你自己的大模型，把英文 README 提炼成一段流畅中文概要；
4. **展示**：暗色数据大屏 + 榜单筛选页 + 项目详情页。

全程自托管，数据落在你自己的 PostgreSQL 里，不依赖任何第三方统计服务。

### 它想回答的问题（这也是搜索关键词的落点）

| 你想知道 | 用哪个榜 / 页面 |
|---|---|
| 最近 **一周涨 star 最快** 的 GitHub 项目有哪些？ | **周榜**（近 7 天 star 增量） |
| 今天 **新晋热门 / 新兴项目** 是哪些？ | **日榜**（当日 star 增量，数据不足时自动降级为近 7 天日均） |
| 过去 **30 天 star 增长** 最多的是谁？ | **月榜** |
| GitHub 上 **star 总量最高** 的项目（排行榜 / 总榜）？ | **总榜**（全库按 star 总量排序） |
| 某个项目的 **star 历史曲线**、最近一周涨了多少？ | 项目详情页 `/detail.html?repo=owner/name` |
| 这个英文项目 **到底是做什么的**？ | AI 中文概要（悬停榜单行即可看） |
| **GitHub Trending 中文版** 有没有自托管的替代？ | 就是本项目（不抓 Trending 页面，用官方 star 事件自己算） |
| 怎么长期 **监控 GitHub 热门项目**、涨星异常？ | 关注提醒 + 定时任务 + 快照历史回看 |

---

## 二、核心功能

### 2.1 榜单（四个维度，口径不同）

| 榜单 | 排序依据 | 统计范围 | 说明 |
|---|---|---|---|
| **日榜** | 当日 star 增量 | 历史池内（star 前 2000） | 数据不足时自动降级为「近 7 天日均」并在页面标注 |
| **周榜** | 近 7 天 star 增量 | 历史池内 | 主力榜单 |
| **月榜** | 近 30 天 star 增量 | 历史池内 | |
| **总榜** | star **总量** | **全库**（不受池限制） | 想看「GitHub 上最著名的项目」用这个 |

- 增量口径来自 GitHub 官方 `stargazers` 历史，不是本地两次快照相减 —— 后者会把「首次入库时 star 已很高」的仓库误算成「当日暴涨」。
- 榜单结果会落 `gh_rank_snapshot` 快照表冻结，历史可回看，不会因为后续数据变化而被改写。
- 表格点表头可切换排序（`stars` / `gain` / `gain_7d` / `gain_30d`），会重新向后端查询而不是只排当前页。

### 2.2 项目历史同步

```
GitHub 官方 stargazers/history  ──► gh_repo_star_weekly（周事实）
                                          │
                                          ├─► gh_repo_star_daily（日事实，差分）
                                          ├─► gh_repo.star_gain_1d / 7d / 30d（回填）
                                          └─► gh_rank_snapshot（榜单快照）
```

- 每轮采集 1000 个仓库，多线程、带全局限速感知，**断点续采**（按 `history_synced_at` 排序，从未采集的优先，避免头部项目永远重复采）。
- 同一仓库改名不会产生两条记录：以 GitHub 全局 `github_id` 为业务唯一键做归并。

### 2.3 AI 中文概要

- 把仓库描述 + README 交给**你自己配置的模型**，产出一段 120–250 字中文介绍，写入 `gh_repo.ai_summary`。
- 硬校验：必须含 ≥20 个中文字符、不含 Markdown 小标题、≤900 字、不复述指令；不合格直接丢弃并累计重试次数（上限 3 次）。
- 一次性生成，不随 star 变化重复消耗 token。**任务默认不自动跑**，要手动触发。

### 2.4 关注提醒

对指定仓库设置阈值（如「周涨 500 star」），定时检查并落事件表，可在大屏查看未读提醒。

---

## 三、三分钟上手（最不熟的走这条）

### 0. 你需要先装什么

| 依赖 | 版本 | 检查命令 |
|---|---|---|
| Docker | ≥ 24 | `docker -v` |
| Docker Compose | v2（`docker compose`，不是 `docker-compose`） | `docker compose version` |

**不需要**装 Python、不需要装 PostgreSQL —— 都在容器里。
建议机器配置：**2 核 4G 内存、20GB 空闲磁盘**（主数据约 90MB，跑起来后数据库 1–3GB）。

### 1. 拿到代码

```bash
git clone <你的仓库地址> githubstar
cd githubstar
```

### 2. 准备配置文件（**这一步别跳过**）

```bash
cp .env.example .env
```

然后编辑 `.env`，至少要改这三处：

```ini
# GitHub Token（可选：不填也能跑，页面/库里已有数据照常展示，
# 只是采集与 star 历史同步任务会跳过，顶栏会提示"未配置 Token"）
GH_GITHUB_TOKEN=ghp_xx...xxxx

# 管理员账号（登录后才能做采集/生成概要等写操作）
# 默认账密均为 github / github；登录后在页面右上角「改账密」即可修改（存数据库，不用改这里）
GH_ADMIN_USER=github
GH_ADMIN_PASSWORD=github
GH_AUTH_SECRET=随便一串32位以上随机字符
```

> `.env` 已被 `.gitignore` 忽略，**永远不会被提交**。

### 3. 先确认你用的是哪个数据包（决定你能看到哪些榜）

`docs/sql/` 里是数据库初始化资料包，有两个档次，**这一步选错，后面榜单一定是空的**：

| 包 | 体积 | `gh_repo` | star 周/日事实 | 部署后能看到 |
|---|---|---|---|---|
| **lite**（仓库自带） | 83 MB | 52,612 | **没有** | 总榜有数据；日/周/月榜空，需采集 |
| **full**（Release 附件） | 355 MB | 721,562 | 233,922 / 1,255,674 | 四榜都能直接出数据 |

想开箱即用四榜 → 先下 full 包，把里面的 `*.sql` 覆盖进 `docs/sql/` 再启动：

```bash
# 例：full 包解压到 /tmp/sql-full
cp /tmp/sql-full/*.sql docs/sql/
```

### 4. 一条命令起全栈

```bash
docker compose up -d
```

首次启动会依次自动完成（详见[第五节](#五启动后会发生什么初始化流程)）：

1. 拉取 `postgres:15`、构建 API 镜像（国内网络看 `.env` 的镜像加速配置）；
2. 按文件名顺序导入 `docs/sql/*.sql`：建 16 张表 → 配置 → 仓库主数据 → 事实层 → 历史池；
3. 启动 API。

查看进度：

```bash
docker compose logs -f db     # 看到 "[initdb] 完成：处理 N 个文件" 即建库结束
docker compose ps             # 两个容器都 running / healthy 就成了
```

> 初始化只跑一次（写哨兵 `.init_done`）。之后再 `up -d` 不会重复导入。

### 5. 生成榜单快照（新库必做一次）

资料包不含派生表（快照/周期统计），初始化后跑一次即可，否则榜单页显示"尚无快照"：

```bash
docker exec ghradar-api-9952 python3 -c "
from app.services import snapshot_service, facts_service
from app.services.rank_service import period_bounds
facts_service.rollup_gains()
d,_=period_bounds('day',None); w,_=period_bounds('week',None); m,_=period_bounds('month',None)
for pt,pk in (('day',d),('week',snapshot_service._iso_week(w)),('month',m[:7])):
    for mt in ('gain','total'):
        print(pt, snapshot_service.compute_rank(pt,pk,mt).get('status'))
"
```

### 6. 打开页面

浏览器访问（局域网内把 `localhost` 换成这台机器的 IP）：

```
http://localhost:9952/
```

> 服务默认监听 `0.0.0.0:9952`，同一局域网的其他设备可直接访问 `http://<你的内网IP>:9952/`。

---

## 四、GitHub Token 怎么拿

不做这一步也能部署，但**采集 star 历史会非常慢**：

| 认证方式 | 配额 | 后果 |
|---|---|---|
| 匿名 | **60 次/小时** | 2000 个仓库的历史要跑几十天 |
| 带 Token | **5000 次/小时** | 一天内可跑完一轮 |

### 步骤

1. 登录 GitHub → 右上角头像 → **Settings**
2. 左侧最下方 → **Developer settings**
3. **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**
4. 填写：
   - **Note**：`githubstar`（随便写，方便以后识别）
   - **Expiration**：按需（建议 90 天，到期换新）
   - **Scopes**：只勾这两个
     - ✅ `public_repo` —— 读公开仓库
     - ✅ `read:org` —— 读组织信息（部分仓库需要）
5. 点 **Generate token**，复制 `ghp_` 开头的那串
   ⚠️ **只显示一次**，关掉页面就再也看不到

### 填进去

```ini
# .env
GH_GITHUB_TOKEN=ghp_你复制的那串
```

然后重启 API 生效：

```bash
docker compose restart api
```

### 验证 Token 是否生效

```bash
curl -s localhost:9952/api/system/rate-limit
```

返回里的 `limit` 是 `5000` 就对了，`60` 说明 Token 没读到。

> **不要**把 Token 写进代码、SQL、README 或任何会提交的文件。只放 `.env`。

---

## 五、启动后会发生什么（初始化流程）

首次 `docker compose up -d` 时，数据库容器会按**文件名顺序**执行 `docs/sql/*.sql`，只执行一次（写哨兵 `/var/lib/postgresql/data/.init_done`，之后重启不再跑）。

```
01-schema.sql        建表 + 索引（纯 DDL，16 张表）
      ↓
10-config.sql        配置 12 / 任务 4 / 供应商 1 / 模型 5 / 熟知名单 25
      ↓
20-repo-00N.sql      仓库主数据（lite 6 片 5.2 万行；full 4 片 72 万行）
      ↓
25-watch.sql         关注目标（外键依赖仓库表，所以排在后面）
      ↓
30-weekly-*.sql      star 周事实   ← 只有 full 包有
35-daily-*.sql       star 日事实   ← 只有 full 包有
      ↓
70-pool.sql          历史池名次（前 2000）+ 序列复位
      ↓
99-verify.sql        自检，打印各表行数
```

实测耗时（本机 SSD）：lite 包约 **10 秒**；full 包（422MB）约 **2–4 分钟**。

### ⚠️ 初始化完成后还差一步：生成榜单快照

`gh_rank_snapshot`（榜单快照）和 `gh_stat_period`（周期统计）是**算出来的派生表，资料包里故意不带**。
所以初始化刚结束时，这两个表是 **0 行**，榜单页会显示"尚无快照"。跑一次即可：

```bash
docker exec ghradar-api-9952 python3 -c "
from app.services import snapshot_service, facts_service
from app.services.rank_service import period_bounds
facts_service.rollup_gains()
d,_=period_bounds('day',None); w,_=period_bounds('week',None); m,_=period_bounds('month',None)
for pt,pk in (('day',d),('week',snapshot_service._iso_week(w)),('month',m[:7])):
    for mt in ('gain','total'):
        print(pt, snapshot_service.compute_rank(pt,pk,mt).get('status'))
"
```

> 也可以在 `/system.html` 点「刷新榜单」，或 `POST /api/system/jobs/daily_rank/run`（需管理员登录）。

### 为什么派生表不放进资料包

因为它们依赖"当前时间"和"当前事实数据"。把别处算好的快照导进新库，会出现**统计图和榜单显示别的库的旧数值**——这种假数据比空数据更难发现（本项目就踩过：某实例的周趋势图里 W21–W27 全是 0、W38 只有 6,269，全是种子里的旧统计值）。

## 六、验证部署成功

### 6.1 一条 SQL 看穿"数据到底进没进"

```bash
docker exec -T db psql -U postgres -d github -c "
select (select count(*) from gh_repo)               as 仓库,
       (select count(*) from gh_repo_star_weekly)   as 周事实,
       (select count(*) from gh_repo_star_daily)    as 日事实,
       (select count(*) from gh_rank_snapshot)      as 榜单快照,
       (select count(*) from gh_stat_period)        as 周期统计;"
```

对照下表判断（这是本项目排查"缺数据"最有效的一条命令）：

| 仓库 | 周事实 | 快照 | 结论 |
|---|---|---|---|
| 52,612 | **0** | 0 | 装的是 lite 包，没事实层 → 换 full 包或跑采集 |
| 721,562 | 233,922 | **0** | 数据齐了，只差快照 → 跑[第五节](#五启动后会发生什么初始化流程)那段 compute_rank |
| 721,562 | 233,922 | >0 | 全部就绪 |

### 6.2 逐项验证

```bash
# 1. 容器状态：两个都该是 running / healthy
docker compose ps

# 2. 健康检查：code 应为 0
curl -s localhost:9952/api/system/health

# 3. 数据量（需要管理员登录，普通访问会返回 401）
docker compose exec -T db psql -U postgres -d github -c \
  "select count(*) as repos from gh_repo;"

# 4. 总榜有数据（这一步不依赖采集）
curl -s "localhost:9952/api/ranks?metric=total&page_size=3"

# 5. 页面能打开
curl -s -o /dev/null -w "%{http_code}\n" localhost:9952/
```

期望结果：`repos` 约 **5.2 万**（种子数据量），总榜能返回真实项目名与 star 数。

### 页面入口

| 地址 | 页面 |
|---|---|
| `/` | 数据大屏（核心指标 / 语言分布 / 周趋势 / 总榜 Top100） |
| `/rank.html` | 榜单 · 筛选（日/周/月/总 + 历史快照 + 熟知名单） |
| `/detail.html?repo=owner/name` | 项目详情 |
| `/ai.html` | 模型与任务（供应商 / 固定任务 / 执行记录） |
| `/watch.html` | 关注提醒 |
| `/system.html` | 采集与调度（采集 / 调度日志 / 数据总览） |
| `/docs` | Swagger 接口文档 |

---

## 七、榜单/统计图没数据？先分清是哪一种（必读）

按顺序核对，三种情况处理方式完全不同：

### 情况 A：装了 lite 包 → 日/周/月榜一定是空的（设计如此）

lite 包只含 `stars>=50` 的 5.2 万个仓库，**不含 star 历史事实**（`30-weekly-*` / `35-daily-*` 文件不存在）。

| 表 | lite | full |
|---|---|---|
| `gh_repo` | 52,612 | 721,562 |
| `gh_repo_star_weekly` | **0** | 233,922 |
| `gh_repo_star_daily` | **0** | 1,255,674 |

结果：总榜有数据，日/周/月榜空。**要么换 full 包重装，要么跑采集补**（见下方"情况 C"）。

### 情况 B：装了 full 包但榜单页仍空 → 少跑了一次快照

full 包有事实数据，但快照表是派生的。跑一次[第五节](#五启动后会发生什么初始化流程)里那段 `compute_rank` 即可。

### 情况 C：事实表本来就是 0 → 必须采集

```bash
# 1) 拉 star 历史（每轮 1000 仓，需要 Token，可反复执行）
curl -XPOST localhost:9952/api/system/jobs/collect_history/run
# 2) 回填 star_gain_1d/7d/30d
curl -XPOST localhost:9952/api/system/jobs/rollup/run
# 3) 生成快照
curl -XPOST localhost:9952/api/system/jobs/daily_rank/run
```

跑完第 1 步第一批（约 1000 仓）周榜就有内容，不必等全量。

### 情况 D：图有数据但数值明显不对（假图）

说明库里的 `gh_stat_period` 是从别处导入的旧统计值。**不要导入这张表**，清掉后按情况 B 重算：

```bash
docker exec -T db psql -U postgres -d github -c "TRUNCATE gh_stat_period, gh_rank_snapshot;"
```

### 一张表判断你到底卡在哪

```bash
docker exec -T db psql -U postgres -d github -c "
select (select count(*) from gh_repo) repo,
       (select count(*) from gh_repo_star_weekly) weekly,
       (select count(*) from gh_rank_snapshot) snap,
       (select count(*) from gh_stat_period) period;"
```

| 结果 | 结论 |
|---|---|
| `weekly=0` | 缺事实层 → 换 full 包 或 跑采集（情况 A/C） |
| `weekly>0, snap=0` | 缺快照 → 跑一次 compute_rank（情况 B） |
| `weekly>0, snap>0` 但图仍怪 | 统计值来源不对 → 清派生表重算（情况 D） |

## 八、SQL 资料包怎么组织的

`docs/sql/` 是**从线上库导出的可复现资料包**（不是手写脚本），详细规范见 [`docs/sql/README.md`](docs/sql/README.md)。

### 文件命名即导入顺序

| 文件 | 内容 | 说明 |
|---|---|---|
| `01-schema.sql` | 建表 / 索引 / 约束 | 纯 DDL，零数据 |
| `10-config.sql` | 配置 / 任务 / 供应商 / 名单 | 小表集中一个文件 |
| `20-repo-001..006.sql` | `gh_repo` 主数据 | 大数据量按 id 分片 |
| `25-watch.sql` | 关注目标 | 外键依赖仓库表，排在其后 |
| `30-weekly-*.sql` / `35-daily-*.sql` | star 周 / 日事实 | **full 包才有** |
| `70-pool.sql` | 历史池名次 + 序列复位 | 派生数据，导入后计算 |
| `99-verify.sql` | 只读自检 | 最后执行 |
| `MANIFEST.json` | 行数 + md5 清单 | 校验依据 |

### 两个体积档

| profile | 内容 | 体积 |
|---|---|---|
| **lite**（仓库自带） | 结构 + 配置 + `stars>=50` 的 52,612 个仓库 | 83 MB |
| **full**（Release 附件） | 结构 + 配置 + 全部 72 万仓 + 全部事实层 | 355 MB |

> 事实层（周/日 star 历史）体积大，不进 git。要日/周/月榜：下 full 包，或用 lite 包启动后跑采集。

### 导入

```bash
for f in $(ls -1 docs/sql/*.sql | sort); do
  case "$(basename $f)" in 99-verify.sql) continue;; esac
  psql -U postgres -d github -v ON_ERROR_STOP=1 -q -f "$f"
done
psql -U postgres -d github -f docs/sql/99-verify.sql
```

Docker 部署时这套顺序已写进 `deploy/initdb/00-init.sh`，**首次启动自动执行**。

### 校验（关键）

资料包带 `MANIFEST.json`（每表行数 + md5），导入后可以逐表核对：

```bash
docker run --rm --network <网络> -e GH_DB_HOST=<目标库> -e GH_DB_PASSWORD=<密码> \
  -v $PWD/server/tools:/app/tools:ro -v $PWD/docs/sql:/app/data:ro \
  ghradar-api:latest python3 /app/tools/export_sql.py --verify /app/data/MANIFEST.json
# → == 结果: PASS 7 / FAIL 0
```

本次实测：lite 包 **7/7 PASS**，full 包 **9/9 PASS**（`gh_repo` 721,562 / 周 233,922 / 日 1,255,674 全部 md5 一致）。

### 为什么派生表不导出

`gh_stat_period`、`gh_rank_snapshot` 是**算出来的统计结果**，不是种子数据。把它们当种子导出，新库的统计图会显示别的库的旧数值 —— 这类"假数据"比空数据更难发现。所以导出工具默认跳过，导入后由 `rollup` / `daily_rank` 任务重新计算。

## 九、数据库持久化与备份

数据库落在 Docker **命名卷** `githubstar_ghradar_pgdata`（容器内 `/var/lib/postgresql/data`），不在容器里，所以：

| 操作 | 数据 |
|---|---|
| `docker compose stop` / `start` / `restart` | ✅ 保留 |
| `docker compose down`（不删卷） | ✅ 保留 |
| `docker compose up -d --build` | ✅ 保留 |
| `docker compose down -v` ⚠️ | ❌ **清空，下次启动重新导入** |

### 备份（建议先做一次）

```bash
docker compose exec -T db pg_dump -U postgres -d github | gzip > backup_$(date +%F).sql.gz
```

### 恢复

```bash
gunzip -c backup_2026-10-14.sql.gz | docker compose exec -T db psql -U postgres -d github
```

### 卷放在哪

```bash
docker volume inspect githubstar_ghradar_pgdata     # 查物理路径
```

> 想改成本地目录（比如挂到 NAS 上），把 compose 里的命名卷换成 `- ./pgdata:/var/lib/postgresql/data`，注意宿主机目录权限要允许容器内的 postgres uid 写。

---

## 十、常用运维命令

```bash
# 日常改完代码，只重启 api（约 3 秒，不动数据库、不重建镜像）
./restart.sh
# 等价于
docker compose restart api

# 看日志
docker compose logs -f api
docker compose logs -f db

# 进数据库
docker compose exec db psql -U postgres -d github

# 查数据量
docker compose exec -T db psql -U postgres -d github -c \
  "select (select count(*) from gh_repo) repos, (select count(*) from gh_repo_star_weekly) weekly;"

# 接口冒烟（14 个）
docker compose exec -T api python3 tools/selfcheck.py

# 彻底重来（⚠️ 删数据）
docker compose down -v && docker compose up -d
```

**不要**用 `docker compose restart`（无参数）—— 会把数据库一起重启，白等一轮健康检查。

---

## 十一、同机再起一套实例（一条命令）

用于验证部署流程 / 试新数据包，不影响线上那套。

```bash
./scripts/deploy-instance.sh                       # 起 9952（DB 54322），用仓库自带数据包
./scripts/deploy-instance.sh --sql /path/sql-full  # 用 full 包（四榜直接有数据）
./scripts/deploy-instance.sh --port 9953           # 再起一套 9953（DB 54323）
./scripts/deploy-instance.sh --down --port 9952    # 删掉这套（容器 + 卷 + 目录）
```

脚本做的事（等价于手工五步，全部自动）：

| 步骤 | 内容 |
|---|---|
| 1 | 复制源码到 `../githubstar-<端口>/`（不带 `.env`、不带数据包） |
| 2 | 生成 `.env`：`GH_PORT=<端口>`、`GH_DB_PORT_HOST=<54321+1>`，**Token 等其余配置沿用源目录** |
| 3 | 改 compose 的容器名与卷名（`ghradar-*-9952` / `ghradar_pgdata_9952`），复用已构建镜像 |
| 4 | 放入数据包；若包里没有 `30-weekly-*.sql` 会提示"日/周/月榜会是空的" |
| 5 | `docker compose -p githubstar<端口> up -d` → 等 initdb 完成 → 补算榜单快照 → 核对行数 → 打印访问地址 |

### 实测输出（9952）

```
==> 端口    : API 9952 / DB 54322
==> 项目名  : githubstar9952   容器: ghradar-api-9952,ghradar-db-9952   卷: ghradar_pgdata_9952
    等待初始化............. 完成（约 70s）
==> 数据核对
    仓库=721562 周事实=233922 日事实=1255674 快照=9985
==> 页面: http://127.0.0.1:9952/  (HTTP 200)
```

### 四条隔离线

| 项 | 线上 | 测试实例 |
|---|---|---|
| 目录 | `githubstar/` | `githubstar-9952/` |
| compose 项目 | `githubstar` | `githubstar9952`（`-p`） |
| 容器 / 卷 | `ghradar-api`、`ghradar-db` / `ghradar_pgdata` | `ghradar-api-9952`、`ghradar-db-9952` / `ghradar_pgdata_9952` |
| 端口 | 9951 / 54321 | 9952 / 54322 |

### ⛔ 三条红线（脚本已规避，手工做时务必注意）

1. **不要在线上目录执行 `down` / `down -v`** —— `-v` 删卷等于删库，不可恢复。
2. **不要共用 `container_name`** —— 同名容器会被顶掉，端口映射也会串。
3. **不要共用数据卷** —— 两套写同一份库，出问题永远说不清是谁改的。

### 验证隔离

```bash
docker ps --format '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Ports}}' | grep ghradar
docker volume ls | grep ghradar
curl -s localhost:9951/api/stats/screen | grep -o '"history_done":[0-9]*'
curl -s localhost:9952/api/stats/screen | grep -o '"history_done":[0-9]*'
```

---

## 十二、环境变量

完整清单见 [`.env.example`](.env.example)，最常改的几项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GH_PORT` | `9952` | 对外端口（改完 `docker compose up -d api` 生效） |
| `GH_DB_PORT_HOST` | `54322` | 数据库映射到宿主机的端口 |
| `GH_DB_PASSWORD` | `postgres` | 数据库密码，生产必改 |
| `GH_GITHUB_TOKEN` | 空 | GitHub Token，**只放这里**；留空也能跑，同步任务跳过 |
| `GH_ADMIN_USER` / `GH_ADMIN_PASSWORD` | `github` / `github` | 管理员账密默认值；**页面右上角「改账密」可改**（存库，优先于这里） |
| `GH_AUTH_SECRET` | 空 | 会话签名密钥，生产必设随机值 |
| `GH_SCHEDULER_ENABLED` | `true` | 是否启用内置定时任务 |
| `GH_CACHE_TTL_DEFAULT` | `600` | 默认视图缓存秒数 |
| `GH_CACHE_TTL_COND` | `120` | 带条件查询缓存秒数 |
| `PIP_INDEX` / `BASE_IMAGE` | 国内镜像 | 构建加速，网络好可删 |

---

## 十三、目录结构

```
githubstar/
├── docker-compose.yml          # 一键编排：db(postgres:15) + api(FastAPI)
├── .env.example                # 环境变量模板（复制成 .env 再改）
├── .gitignore                  # 忽略 .env / 密钥 / 缓存
├── restart.sh                  # 秒级重启 api
├── deploy/initdb/00-init.sh    # 首次初始化：建表 + 导入 90MB 主数据
├── docs/
│   ├── sql/                    # SQL 资料包（结构 / 数据分离，见其 README）
│   ├── GitHub-Token-指南.md
│   └── Docker-部署指南.md
├── scripts/import-sql.sh       # 手工分批导入脚本
└── server/
    ├── Dockerfile / requirements.txt / run.py
    ├── app/
    │   ├── main.py             # 路由装配 + 静态挂载 + 生命周期
    │   ├── config.py           # 环境变量集中处
    │   ├── db.py               # 连接池 + query/execute
    │   ├── api/                # Controller 层（不写 SQL）
    │   ├── services/           # Service 层（业务规则，不碰 HTTP）
    │   └── collectors/         # 采集层（限速 / 断点续采）
    ├── tools/                  # load_data / bootstrap / selfcheck
    ├── web/                    # 前端页面 + vendor（离线可用）
    └── data/                   # 运行时配置（llm_config.json 等，不入库）
```

分层铁律：**Controller 不写 SQL，Service 不碰 HTTP**。

---

## 十四、安全与脱敏

本项目按「开源可发布」标准处理敏感信息：

| 项 | 处理方式 |
|---|---|
| GitHub Token | 只从环境变量读；页面与接口**只返回是否配置**，不显示任何字符 |
| 模型 API Key | 存在 `server/data/llm_config.json`（**已 gitignore**），接口返回 `has_key: true/false` |
| 管理员密码 | 环境变量注入；登录态是 HMAC 签名的 HttpOnly Cookie，不落库 |
| 数据库密码 | 环境变量注入，compose 里不写死 |
| 内网地址 / 代理 | SQL 种子里已清空，供应商 `base_url` 改为 `127.0.0.1` 占位 |
| 权限边界 | 所有非 GET 的 `/api/*` 请求由后端中间件强制校验，前端隐藏只是体验层 |

**开源前自检**：

```bash
git status --ignored | grep -E '\.env|llm_config'    # 确认被忽略
grep -rn "ghp_\|sk-[a-zA-Z0-9]\{20,\}" --exclude-dir=.git . | grep -v '.env.example'
```

---

## 十五、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 页面打得开但数据全空 | 初始化没跑完，看 `docker compose logs db \| grep initdb`；确认 `docs/sql` 里有 `20-repo-*.sql` |
| 接口返回「表不存在」 | 同上，或手动 `docker compose exec api python3 tools/bootstrap.py` |
| 日榜 / 周榜空 | 先跑 6.1 那条 SQL 定位：`周事实=0` 说明装的是 lite 包或没采集；`快照=0` 说明没跑 compute_rank。见[第七节](#七榜单统计图没数据先分清是哪一种必读) |
| 采集全部失败 | 没配 Token（匿名 60 次/小时）。查 `curl localhost:9952/api/system/rate-limit` |
| 端口被占用 | 改 `.env` 的 `GH_PORT` / `GH_DB_PORT_HOST`，再 `docker compose up -d api` |
| 改了页面没变化 | 页面是挂载卷，改完硬刷新（Ctrl+F5）即可，不用重建镜像 |
| 改了 `Dockerfile` / `requirements.txt` | 才需要 `docker compose up -d --build` |
| 概要生成后仍为空 | 模型返回没过中文/格式硬校验，查 `/api/ai/runs` 的 `error` |
| 日志里有 `syntax error at or near "\"` | `pg_dump 15.19+` 会在 dump 头尾写 `\restrict` 元命令，psycopg2 会当 SQL 发出去。资料包已删掉这两行，`tools/load_data.py` 也会自动跳过所有 `\` 开头的行 |
| 容器一直重启 | 看 `docker compose logs db`，多半是 `command` 里写了行内注释（`#` 会被当成参数） |
| 局域网访问不了 | 确认监听是 `0.0.0.0`（`ss -tln \| grep 9952`），再查宿主机防火墙 |

---

## 附：接口总览

全部返回 `{code, msg, data}`，`code=0` 为成功。完整文档见 `/docs`。

| 组 | 前缀 | 代表接口 |
|---|---|---|
| 系统 / 采集 / 调度 | `/api/system` | `/health` `/counts` `/jobs` `/rate-limit` `/refresh` |
| 仓库 | `/api/repos` | `GET /repos`（多条件筛选）、`GET /repos/{owner}/{repo}` |
| 历史事实 | `/api/history` | `/daily` `/weekly` `/summary` `/collect` `/snapshot` |
| 榜单 / 快照 / 白名单 | `/api/ranks`、`/api/whitelist` | `GET /ranks`、`POST /ranks/compute` |
| 统计大屏 | `/api/stats` | `/overview` `/screen` `/weekly-trend` `/lang-distribution` |
| 模型与任务 | `/api/ai` | `/providers` `/models` `/tasks` `/tasks/run` `/runs` |
| 关注提醒 | `/api/watch` | `/list` `/add` `/check` `/events` |

---

## License

MIT（见仓库 [LICENSE](LICENSE) 文件）。

---

## 关键词与 GitHub Topics

本项目面向「**GitHub 排行榜 / GitHub 周榜 / 日榜 / 月榜 / 总榜 / 新兴项目 / 飙升项目 / star 增长**」这类需求，
数据全部来自 GitHub 官方接口自行计算，不抓取 `github.com/trending` 页面。

若你要 fork 或二次发布，建议给仓库打这些 **Topics**（GitHub 站内搜索权重高于 README 正文）：

```
github-ranking  github-trending  github-star-history  star-history
open-source-ranking  trending-repositories  github-stats  data-dashboard
self-hosted  fastapi  postgresql  vue3  echarts  chinese  awesome-list
```

**常见搜索词对照**（本项目覆盖的意图）：

| 搜索意图 | 对应能力 |
|---|---|
| GitHub 排行榜 / GitHub 总榜 / 开源项目排行 | 总榜（全库按 star 总量） |
| GitHub 周榜 / 本周涨星最快 / 一周 star 增长 | 周榜（近 7 天增量） |
| GitHub 日榜 / 今日热门 / 新兴项目 | 日榜（当日增量） |
| GitHub 月榜 / 月度增长榜 | 月榜（近 30 天增量） |
| GitHub 飙升榜 / 涨星榜 / 爆款项目 | 周榜 + 关注提醒 |
| GitHub Trending 中文 / trending 替代 / 自建 trending | 本项目整体（自托管、中文界面） |
| star 历史 / star 曲线 / star-history 替代 | 官方 `stargazers/history` 同步 + 详情页曲线 |
| 开源项目数据大屏 / 大屏看板 / 数据可视化 | 暗色数据大屏（ECharts） |
| GitHub 项目中文简介 / 英文 README 翻译 | AI 中文概要 |

