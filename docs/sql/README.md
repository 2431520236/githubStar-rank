# SQL 资料包 · 规范（导出 → 导入 → 校验）

本目录是**从线上库导出的可复现资料包**，不是手写脚本。

- 来源库：`github`（9951 线上库），导出时间见 `MANIFEST.json`
- 导出工具：`server/tools/export_sql.py`（对源库**只读**）
- 校验方式：逐表行数 + **md5**（同 `MANIFEST.json` 比对），不是"看着对"

---

## 一、文件命名即导入顺序

| 文件 | 内容 | 说明 |
|---|---|---|
| `01-schema.sql` | 建表 / 索引 / 约束 | `pg_dump --schema-only`，纯 DDL，**零数据** |
| `10-config.sql` | 配置 / 任务 / 供应商 / 名单 | 小表集中一个文件，COPY 块 |
| `20-repo-001..006.sql` | `gh_repo` 主数据 | 大数据量按 id 分片 |
| `25-watch.sql` | 关注目标 | 外键依赖 `gh_repo`，**必须排在其后** |
| `30-weekly-*.sql` | star 周事实 | full 包才有 |
| `35-daily-*.sql` | star 日事实 | full 包才有 |
| `40-contributor-*.sql` / `45-release-*.sql` | 参与者 / 版本 | full 包才有 |
| `70-pool.sql` | 历史池名次 + 序列复位 | 派生数据，导入后计算 |
| `99-verify.sql` | 只读自检 | 最后执行 |
| `MANIFEST.json` | 行数 + md5 清单 | 校验依据 |

> 导入脚本直接 `ls *.sql | sort` 按文件名顺序跑，**不需要手工维护顺序**。

## 二、两个 profile

| profile | 内容 | 体积 | 用途 |
|---|---|---|---|
| `lite`（本目录） | 结构 + 配置 + `stars>=50` 的仓库（52,612 行） | 83 MB | 随仓库发布，开箱有总榜 |
| `full` | 结构 + 配置 + 全部 72 万仓 + 全部事实层 | 355 MB | 作为 Release 附件单独下载 |

事实层（周/日 star 历史）体积大，不进 git；需要日/周/月榜时二选一：
① 下 full 包；② 用 lite 包启动后跑采集（`collect_history` → `rollup` → `daily_rank`）。

## 三、导出（从线上库重新生成资料包）

```bash
# 1) 结构（在 db 容器里 pg_dump，输出到宿主机）
docker exec ghradar-db pg_dump -U postgres -d github \
  --schema-only --no-owner --no-privileges > docs/sql/01-schema.sql

# 2) 数据（在 api 容器里跑导出工具，对库只读）
docker exec ghradar-api python3 /app/tools/export_sql.py \
  --out /app/data/sql-export-lite --profile lite  --chunk-rows 10000
docker exec ghradar-api python3 /app/tools/export_sql.py \
  --out /app/data/sql-export-full --profile full  --chunk-rows 200000

# 3) 把 01-schema.sql 一起放进包（导出工具只导数据）
cp docs/sql/01-schema.sql server/data/sql-export-lite/
cp docs/sql/01-schema.sql server/data/sql-export-full/
```

导出工具的三条硬规则：

1. **只读**：`conn.set_session(readonly=True)`，物理上写不坏源库；
2. **时区固定 UTC**：否则源库（Asia/Shanghai）与校验库（UTC）的 `timestamptz` 文本不同，md5 永远对不上；
3. **派生表不导出**：`gh_stat_period`、`gh_rank_snapshot` 是**算出来的**，导出会把旧统计值带进新库 —— 这正是之前「统计图有假数据」的根因。

## 四、导入

```bash
for f in $(ls -1 docs/sql/*.sql | sort); do
  case "$(basename $f)" in 99-verify.sql) continue;; esac
  psql -U postgres -d github -v ON_ERROR_STOP=1 -q -f "$f"
done
psql -U postgres -d github -f docs/sql/99-verify.sql
```

Docker 部署时这套流程已写进 `deploy/initdb/00-init.sh`，**首次启动自动执行**。

## 五、校验（必须做，别只看"导入成功"）

```bash
# 让 api 容器连到目标库，按 MANIFEST 重算行数与 md5
docker run --rm --network <网络> \
  -e GH_DB_HOST=<目标库> -e GH_DB_PASSWORD=<密码> \
  -v $PWD/server/tools:/app/tools:ro -v $PWD/docs/sql:/app/data:ro \
  ghradar-api:latest python3 /app/tools/export_sql.py \
  --verify /app/data/MANIFEST.json
```

输出示例（本次实测）：

```
   gh_repo                  期望   52612 / 实际   52612  md5 一致  PASS
   gh_app_setting           期望      12 / 实际      12  md5 一致  PASS
== 结果: PASS 7 / FAIL 0
```

`FAIL` 就是数据不一致，不要放过。

## 六、本包的实测结论（2026-10-21）

| 包 | 表数 | 逐表 md5 |
|---|---|---|
| lite | 7 组 | **PASS 7 / FAIL 0** |
| full | 9 组 | **PASS 9 / FAIL 0**（`gh_repo` 721,562 / 周 233,922 / 日 1,255,674） |

一键初始化实测（全新数据卷 + initdb 挂载）：约 **10 秒**完成，16 张表、`gh_repo` 52,612、历史池 2,000。

## 七、本轮修掉的坑（都是真实踩过的）

| 坑 | 现象 | 修法 |
|---|---|---|
| 配置表排在仓库表之前 | `gh_watch_target` 外键报错，导入中断 | 拆成 `25-watch.sql`，排在 `20-repo-*` 之后 |
| 周/日事实共用分片前缀 | 日表分片**覆盖**周表分片，周事实导入后 0 行 | 前缀按表区分：`30-weekly` / `35-daily` |
| 校验受服务器时区影响 | 行数对、md5 全不一致 | 导出与校验都 `SET TIME ZONE 'UTC'` |
| schema 漏打包 | full 包无 `01-schema.sql`，导入首表就报"表不存在" | 导出后必须把 schema 一起放进包 |
| `pg_dump` 的 `\restrict` 元命令 | api 的 bootstrap 用 psycopg2 执行 schema 时 `syntax error at or near "\"` | 打包时删掉 `\restrict` / `\unrestrict` 两行；`tools/load_data.py` 增加 `_strip_psql_meta` 兜底 |
| 派生统计当种子导出 | 新库统计图显示别处的旧数值（9952 的图就是这么坏的） | 派生表默认不导出，改为导入后计算 |
