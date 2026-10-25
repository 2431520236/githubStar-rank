# star 历史批量补全（ClickHouse → gh_repo_star_weekly）

> 2026-10 落地。解决「gh_repo_star_weekly 长期 0 行、gh_stat_period 只有零星几周」的问题。

## 一、数据源选型（为什么不是 GitHub 官方接口）

| 渠道 | 能否批量 | 结论 |
|---|---|---|
| GitHub REST `/repos/{o}/{r}/stargazers` + `Accept: application/vnd.github.star+json` | ❌ 逐仓翻页，5 万仓不可行；无 token 时匿名 60 次/小时 | 只适合单仓补 |
| **ClickHouse 公共数据集** `play.clickhouse.com`（账号 `explorer`，无密码）表 `github_events` | ✅ **一次请求塞一批 repo_name**，`GROUP BY repo_name, toDate(created_at)` | **本项目采用** |
| GH Archive / GraphQL | 可行但成本高 | 备用 |

`github_events`：111.8 亿条事件、2011-02-12 至今；`event_type='WatchEvent'` 即 star。

## 二、脚本

`server/tools/backfill_star_history.py`（在容器内跑，同时能连 db 和外网）

```bash
cd githubstar

# 试跑（不写库）
docker compose exec -T api python3 tools/backfill_star_history.py --limit 500 --dry-run

# 写库
docker compose exec -T api python3 tools/backfill_star_history.py --limit 500 --write

# 全量（--limit 0）
docker compose exec -T api python3 tools/backfill_star_history.py --limit 0 --write

# 只补库里还没有周记录的仓库（业务层防重复）
docker compose exec -T api python3 tools/backfill_star_history.py --limit 0 --only-missing --write
```

参数：`--limit N`（取 star 最高的前 N 个仓库，0=全量）`--batch N`（**一次请求塞多少个 repo_name**，默认 500）`--workers N`（并发批数，默认 4）`--write` / `--dry-run` / `--only-missing`。

## 三、防重复（两层，缺一不可）

**① 数据库层 —— 联合唯一约束（已存在，无需新建，不影响性能）**

```
uq_gh_star_weekly | UNIQUE (repo_id, week_start)
```

写入用 `ON CONFLICT (repo_id, week_start) DO UPDATE SET ...`，
**冲突即更新而非插入**，天然幂等。实测：同一批 100 仓连写两次，行数 24613 → 24613，零新增。

性能说明：该唯一约束就是 `ON CONFLICT` 的推断目标，同时也是「按仓库查历史」的走索引路径
（另有 `idx_gh_star_weekly_repo (repo_id, week_start DESC)`），**不需要也不应再加第二个索引** —— 加只会拖慢写入。

**② 业务层 —— `--only-missing`**

只挑选 `NOT EXISTS (SELECT 1 FROM gh_repo_star_weekly w WHERE w.repo_id = r.id)` 的仓库，
已补过的仓库直接跳过，不发起请求。

## 四、字段映射（对齐项目代码）

| ClickHouse | gh_repo_star_weekly | 说明 |
|---|---|---|
| `toDate(created_at)` | `days` jsonb | `[{"day":"2026-09-28","count":19}, ...]`（按天明细，保留） |
| 同周 `count()` 求和 | `weekly_new` | 该 ISO 周新增 star |
| 由 `gh_repo.stars` 反推 | `cumulative` | 当前 stars 视为最新周累计，往前逐周减 |
| ISO 周年/周 | `week_key` = `2026-W40`、`week_label` = `W40`、`week_index` = 40 | |
| — | `week_start` | 周一（`d - timedelta(days=d.weekday())`），与项目其他周口径一致 |
| — | `source` = `'clickhouse_gh_events'` | 与官方接口来源 `stargazers_history` 区分 |

## 五、性能与内存

- **批拉批写 + 流式**：一批拉完立即聚合、立即写库，不累积全量 → 内存峰值 = 一批的量，不随库规模增长。
- **批量 INSERT**：每 1000 行拼一条多值 `VALUES (...),(...)`，不是逐条 execute（逐条会把全量跑成几小时）。
- 实测：100 仓 = 1 次请求 / 5117 条周记录 / 30601 条日记录 / **2 秒**（含写库）。

## 六、补完还要做的一步

周表补齐后滚动字段（`star_gain_1d/7d/30d`）与榜单口径仍要重算：

```bash
curl -s -XPOST localhost:9951/api/system/jobs/rollup/run
```

（或等内置调度；该接口现在需管理员 Cookie。）
