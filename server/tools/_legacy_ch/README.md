# _legacy_ch —— 已废弃的 ClickHouse 采集脚本（2026-10 迁入）

## 为什么废弃

这些脚本全部走 **ClickHouse playground `play.clickhouse.com` 的 `github_events` 表**，
是 star 历史的**第二套数据源**。它与官方 `GET /repos/{o}/{r}/stargazers/history` 混写同一张
`gh_repo_star_daily` / `gh_repo_star_weekly`，正是「多套数据混用」的来源。

用户 2026-10 口径（最终定案）：

> star 历史的**唯一数据源** = 官方 `GET /repos/{owner}/{repo}/stargazers/history`；
> 定时任务也只允许用这一套。

另外 ClickHouse 自身的 2026 年数据本身就是**采样不全**的（见项目文档
`docs/数据获取渠道清单.md`：W27 骤降到 1 万条/周，而 W18 ≈ 27 万条/周），
用它补 2026 年缺口属于「拿不准的数据覆盖准的数据」。

## 副作用记录（本次事故）

`backfill_daily_increment.py` 之类的日增量写入，加上 `facts_service.take_daily_snapshot()`
（已删）的错误增量算法，导致：

- `gh_repo_star_daily` 中 `source='github_api'` 的 **52,497 行全部挤在 2026-10-12**，
  `delta_stars` 合计 **8,796 万**（每行 delta ≈ 该仓**总 star 数**）
- 污染 `gh_stat_period` 的 **2026-W42 `gain_sum` = 8,265 万** → 周榜爆表
- 大屏「今日新增」显示 8,798 万

## 现在该用什么

活跃目录 `server/tools/` 下的**官方源**脚本：

| 脚本 | 用途 |
|---|---|
| `backfill_pool.py` | 按候选池逐仓拉官方 `stargazers/history`，多线程 + 跨额度窗口续跑（12 并发） |
| `rebuild_star_history.py` | 清库重拉（`--truncate` + 候选池），同上走官方接口 |
| `probe_gh.py` | GitHub 接口 / token / 额度探测 |

定时任务侧：`scheduler.py` 的 `collect_history`（每 60s tick、距上次 >1 天才拉，
`history_collector.collect_history(limit=1000, concurrency=3)`）是**唯一**的 star 历史采集任务。
`daily_snapshot` 与 `daily_ch_increment` 两个任务已删除。