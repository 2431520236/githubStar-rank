# star 历史采集方案 v2 —— 外部接口清单与批量拉取优化

> 2026-10-10 实测。所有数字均为本机真实执行结果。
> 硬约束（用户 2026-10 定）：**禁止 5000 仓拉 5000 次**；一次调用尽量多仓多天；接口有频率限制；
> 拉「周新增前 3000」的仓；**日数据每日补充**；**接口返回一律落文件，不进对话上下文**。

---

## 1. 结论先行

**唯一满足「一次请求多仓多周」的免认证通道是 ClickHouse 公共数据集 `play.clickhouse.com`（账号 `explorer`，无密码，表 `github_events`）。**
它不需要 token、不需要按仓拼 `IN (...)`、不需要翻页 —— 直接用 SQL 在服务端聚合，**一次请求返回全库任意时间片的 (repo_name, day/week, count)**。

实测（本机，2026-10-10）：

| 场景 | 请求数 | 耗时 | 返回规模 |
|---|---|---|---|
| 逐周各取前 3000 仓（定仓） | **1** | 1.28s | 123,000 行 / 43,368 仓 / 4.6MB |
| 全库 × 单周聚合 | **1** | 0.95s | 40,574 仓 / 1.1MB |
| 全库 × 单月 × 按天明细 | **1** | 2.74s | 18MB |
| 全库 × 全年 × 按周（不分片一次拉） | 1（失败） | — | 响应过大断流，**须分片** |
| 全库 × 全年 × 按天（整月切片 ×10） | 10 | 23.5s | 316 万行 / 123MB，宿主机全部成功 |
| **补 2026-09 一个月（周前 3000 仓）端到端** | **3** | **4s** | 8,151 仓 / 10,041 周记录 / 35,583 日记录 |

对比旧方案（v1，按 500 仓一批 `IN (...)`）：补同样口径需要 **41（定仓）+ 87（拉数）= 128 次请求**。
**v2 把请求数压到 1（定仓）+ N（月片），2026 全年 = 1 + 9 = 10 次。**

---

## 2. 全部渠道清单（本机实测状态）

| # | 渠道 | 能拿到什么 | 一次多仓? | 额度 | 本机实测 |
|---|---|---|---|---|---|
| 1 | **ClickHouse `github_events`**（explorer 免密） | 全库逐日/逐周 star 增量，2011-02 至今 | ✅ **全库一次** | 无（限并发） | ✅ 200，0.3~2.7s |
| 2 | OSS Insight `api.ossinsight.io`（免认证） | 单仓 stargazers 历史 `per=day/week/month` | ❌ 单仓（逗号多仓 404） | 无（beta） | ✅ 200，1.2~1.5s |
| 3 | GitHub REST `/repos/{o}/{r}/stargazers` + `star+json` | 单仓逐颗 `starred_at` | ❌ 单仓翻页 | 匿名 60/h，认证 5000/h | ⚠️ 本机 401（无 token） |
| 4 | GitHub GraphQL `stargazers(first:100)` | 单仓游标分页 `starredAt` | ❌ 单仓 | 5000 点/h | ⚠️ 本机 403（无 token） |
| 5 | star-history.com `api.star-history.com/svg` | 单仓聚合曲线（仅展示） | ❌ | 无 | ✅ 200（**须带浏览器 UA**，否则 Cloudflare 1010 403） |
| 6 | GH Archive `data.gharchive.org/{日期}-{时}.json.gz` | 每小时全量事件 | ✅ 全量但需自行解析 | 无 | ❌ 本机 SSL 握手超时，不可用 |
| 7 | 官方 `gh_repo` 主表（本地资料） | 当前快照 52,736 行 | — | — | ✅ 已入库 |
| 8 | 本库派生（快照 / rollup / 榜单） | 日增量、7/30/60 滚动、榜单冻结 | — | — | ✅ 已实现 |

---

## 3. v2 方案（推荐落地）

脚本：`server/tools/backfill_star_history_v2.py`（在 api 容器内跑，同时能连 db 和外网）。

### 两阶段

**Stage 1 定仓（1 次请求）** —— ClickHouse 的 `LIMIT N BY 表达式` 一条 SQL 拿到「每周各自前 N」：

```sql
SELECT repo_name, toStartOfWeek(created_at) wk, count() c FROM github_events
WHERE event_type='WatchEvent' AND created_at >= '<since> 00:00:00' AND created_at < '<until> 00:00:00'
GROUP BY repo_name, wk ORDER BY c DESC LIMIT 3000 BY wk FORMAT TSV
```

返回的是「逐周前 3000」的**并集**（实测 2026 全年 → 123,000 行 / 43,368 仓）。
不用逐周发请求，也不用先取全库再本地排名。

**Stage 2 拉数（每月 1 次请求，全库切片）** —— 不按仓分批，直接按月拉全库按天明细，本地过滤目标仓：

```sql
SELECT repo_name, toDate(created_at) d, count() n FROM github_events
WHERE event_type='WatchEvent' AND created_at >= '<月初> 00:00:00' AND created_at < '<下月初> 00:00:00'
GROUP BY repo_name, d FORMAT TSV
```

为什么按月而不是按仓：`IN (500个仓)` 的请求数 = 仓数/500，全库 5 万仓要上百次；
按月切片请求数 = 月数，**与仓数无关**。

### 请求数对比

| 口径 | v1（按仓 IN） | v2（全库切片） |
|---|---|---|
| 补 1 个月 / 周前 3000 | 1 + 6 = 7 次 | **2 次** |
| 补 2026 全年 / 周前 3000 | 1 + 87 = 88 次 | **10 次** |

### 用法

```bash
# 定仓 + 只读试跑（不写库）
docker compose exec -T api python3 tools/backfill_star_history_v2.py --per-week-top 3000 --since 2026-01-01 --dry-run
# 周新增各前 3000 的仓，补 2026 全年（写库）
docker compose exec -T api python3 tools/backfill_star_history_v2.py --per-week-top 3000 --since 2026-01-01 --write
# 不筛仓，全库全仓
docker compose exec -T api python3 tools/backfill_star_history_v2.py --all --write
```

### 写入与幂等

- 落 `gh_repo_star_weekly`：`uq_gh_star_weekly UNIQUE (repo_id, week_start)` + `ON CONFLICT DO UPDATE`，重跑零新增。
- 每 1000 行一条多值 `INSERT`（逐条 execute 会把全量跑成几小时）。
- `cumulative` 由当前 `gh_repo.stars` 往前逐周反推；`week_start` = 周一；`source = 'clickhouse_gh_events'`。
- 补完触发 `POST /api/system/jobs/rollup/run`（需管理员 Cookie）重算 `star_gain_1d/7d/30d/60d`。

---

## 4. 日数据「每日补充」

周/月历史靠 v2 批量补；**日粒度（`gh_repo_star_daily` / `star_gain_1d`）靠每日增量**：

- 每日一次 `take_daily_snapshot()`（scheduler 已有）落当日 star 快照，delta = 今日 − 上次。
- 或用 ClickHouse 每日一查：`SELECT repo_name, toDate(created_at) d, count() n FROM github_events WHERE event_type='WatchEvent' AND created_at >= '昨天' GROUP BY repo_name, d` —— **1 次请求覆盖全库昨天的 star 增量**。
- 两者都幂等，可重复跑。

---

## 5. 坑（实测）

1. **ClickHouse 大响应会偶发 `IncompleteRead`** —— 实测宿主机整月切片覆盖全年（10 片）全部成功：316 万行 / 123MB / 23.5s；但容器内 1、2 月整月片曾偶发断流。结论：**这不是硬上限，是偶发断流** —— 失败片重试，或把切片缩到 10 天即可。脚本默认 10 天切片最稳。
2. **playground 是公共资源**，片间 `sleep(1)`，并发别超 ~8，偶发 HTTP 500 要重试。
3. **`created_at` 是 UTC**，项目周起点为周一，两边取整须一致。
4. **仓名大小写**：ClickHouse 与库内常不一致，匹配统一 `lower()`。
5. **禁止用 LLM 提取 star 历史** —— 纯事件流取数，字段越少越好。
6. **接口返回一律落文件**，stdout 只留一行汇总（用户硬要求）。
7. star-history.com / GH Archive 有 Cloudflare 1010：前者带浏览器 UA 可过，后者本机 SSL 超时不通。

---

## 6. 主源定位建议

| 用途 | 首选 | 备选 |
|---|---|---|
| 批量补周/月历史（主力） | ClickHouse 全库切片 | OSS Insight 单仓（少量仓补缺） |
| 官方口径校验 | GitHub REST `stargazers`（需 token） | GraphQL（需 token） |
| 每日增量 | ClickHouse 昨日一查 | 本库 `take_daily_snapshot` |
| 当前快照 | 本地 `gh_repo` + REST 详情 | — |

---

## 7. 免登录 / 需认证接口的分级与最大化利用（2026-10 用户定）

**原则：免登录接口做主力（无限速、可批量），需认证接口只做校验/补缺（有限速，绝不用于批量）。**

| 级别 | 接口 | 额度 | 用途 | 用法纪律 |
|---|---|---|---|---|
| **免登录**（主力） | ClickHouse playground `github_events` | 无（限并发） | 周/月/日 star 增量、定仓、日增量 | 一次请求全库；切片 ≤10 天；片间 sleep 1s；并发 ≤8 |
| **免登录**（补充） | OSS Insight `stargazers/history` | 无（beta） | 单仓历史趋势校验 | 仅少量仓；不支持多仓，不可批量 |
| **免登录**（展示） | star-history.com | 无 | 人工核对曲线 | 必须带浏览器 UA，否则 Cloudflare 1010 |
| **需认证**（校验） | GitHub REST `stargazers` + `star+json` | 匿名 60/h，token 5000/h | 官方口径抽样校验 | 只校验少量关键仓；无 token 时禁用 |
| **需认证**（校验） | GitHub GraphQL `stargazers` | 5000 点/h | 同上（游标更稳） | 同上 |
| **不可用** | GH Archive 小时文件 | 无 | — | 本机 SSL 超时，弃用 |

要点：
- **绝不把批量采集挂在需认证接口上**（5000 仓 × 翻页 = 不可行）。
- 需认证接口只在「有 token 且只查少量仓」时启用；无 token 时自动跳过，不空转。
- 所有接口响应一律落文件，不进对话上下文；stdout 只留一行统计。

---

## 8. 日榜恢复 + 全自动运行（2026-10 用户重定）

- **日榜已恢复**：`period_bounds('day', key)` 返回单日窗口；未指定时取今天，今天无事实数据则回退最近一个有非零 `delta_stars` 的日期（否则日榜恒为 0 条）。
- 前端 `rank.html` 四个按钮：**日榜 / 周榜 / 月榜 / 总榜**；`MODE.day` 走 `period_type='day', metric='gain'`，列头显示「日新增」。
- 快照：`daily_rank` 任务每天同时刷 **日/周/月 × gain/total** 六组（`gh_rank_snapshot` 的 `period_type` 无约束，`day` 可落；`metric` 仍只有 total/gain）。
- **自动运行链路**（全部免登录、无需人工）：
  1. `daily_ch_increment`（每 86400s）→ ClickHouse 一次请求拉昨日全库 delta 落 `gh_repo_star_daily`；
  2. `rollup`（每 86400s）→ 重算 `star_gain_1d/7d/30d/60d`；
  3. `daily_rank`（每天 01:00）→ 刷日/周/月 × gain/total 快照；
  4. `daily_snapshot`（每天 23:50）→ 落当日主表快照。
- 手动补历史仍用 `backfill_star_history_v2.py`；日增量补漏用 `backfill_daily_increment.py`。

