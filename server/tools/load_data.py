#!/usr/bin/env python3
"""数据加载器:建表 + 种子 + gh_repo 全量数据(4-1..4-10)。

用途:
  - docker compose 首次启动时由 deploy/initdb 自动调用(容器内 /seed 为挂载目录);
  - 也可在宿主机手动执行,用于导入到本机已有的 PostgreSQL / MySQL 之外的实例。

设计要点:
  1. 01-schema.sql 全部 IF NOT EXISTS,可重复执行;
  2. 4-* 数据文件是 INSERT ... ON CONFLICT DO NOTHING,可重复执行;
  3. 02-seed-config.sql 由源库导出时把 JSONB 列写成了裸字面量(1095 / TRUE / https://...),
     PostgreSQL 15 会拒绝,这里自动改写为 JSON 字面量再执行 —— 不改动原始资料。

用法:
    python3 tools/load_data.py --seed-dir /seed
    python3 tools/load_data.py --seed-dir ../docs/sql --schema-only
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

# 02-seed-config.sql 中 gh_app_setting.value 是 JSONB,但导出成了裸字面量
_SETTING_LINE = re.compile(r"^\('([^']+)',\s*([^,]+?),\s*'", re.M)


def _fix_jsonb_literals(sql: str) -> str:
    """把 ('key', 1095, 'desc', ...) 的裸值改写为合法 JSON 字面量。

    gh_app_setting.value 是 JSONB,源导出却写成了 SQL 裸值:
        1095        -> '1095'          (JSON number)
        TRUE        -> 'true'          (JSON boolean)
        'https://…' -> '"https://…"'   (JSON string,注意原值已带 SQL 引号)
    """

    def repl(m: re.Match) -> str:
        key, val = m.group(1), m.group(2).strip()
        # 源值可能自带 SQL 单引号(字符串型),先剥掉
        if len(val) >= 2 and val.startswith("'") and val.endswith("'"):
            val = val[1:-1]
        if val.upper() in ("TRUE", "FALSE"):
            return f"('{key}', '{val.lower()}', '"
        if re.fullmatch(r"-?\d+(\.\d+)?", val):
            return f"('{key}', '{val}', '"
        # 其余一律当 JSON 字符串(含空串)
        return f"('{key}', '\"{val}\"', '"

    head, sep, tail = sql.partition("-- ===== gh_llm_task =====")
    if not sep:
        return sql
    return _SETTING_LINE.sub(repl, head) + sep + tail


def _has_sql(body: str) -> bool:
    """判断段内容是否含真正的 SQL(纯注释段应跳过)。"""
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("--"):
            return True
    return False


def split_segments(sql: str) -> list[tuple[str, str]]:
    """按 '-- ===== 表名 =====' 注释切分为 (段名, SQL) 列表。

    源导出文件把每个表的数据作为独立 INSERT 段,逐段独立执行可避免
    单段失败(例如 gh_watch_target 的 repo_id 外键)拖垮整份种子。
    """
    parts = re.split(r"^(--\s*=====\s*[^=]+?\s*=====.*)$", sql, flags=re.M)
    segments: list[tuple[str, str]] = []
    head = parts[0].strip()
    if _has_sql(head):
        segments.append(("head", head))
    for i in range(1, len(parts) - 1, 2):
        title = parts[i].strip().strip("-= ").strip()
        body = parts[i + 1].strip()
        if body and _has_sql(body):
            segments.append((title, body))
    return segments


def _strip_psql_meta(sql: str) -> str:
    """去掉 psql 元命令行（\\restrict / \\unrestrict / \\echo / \\set ...）。

    pg_dump 15.19+ 会在文件头尾写入 `\\restrict`（防注入特性）。
    psql 认这些行，但 psycopg2 会把它们当 SQL 发给服务器 → syntax error。
    """
    keep = []
    for line in sql.splitlines():
        if line.lstrip().startswith("\\"):
            continue
        keep.append(line)
    return "\n".join(keep)


def exec_file(path: Path, transform=None, label: str = "", segmented: bool = False,
              tolerate_errors: bool = False) -> dict:
    """执行 SQL 文件。segmented=True 时按段独立执行并汇总结果。"""
    sql = _strip_psql_meta(path.read_text(encoding="utf-8"))
    if transform:
        sql = transform(sql)
    t0 = time.time()
    ok = failed = 0
    errors: list[str] = []

    if segmented:
        for name, body in split_segments(sql):
            try:
                with db.get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(body)
                    conn.commit()
                ok += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                errors.append(f"{name}: {e}")
                if not tolerate_errors:
                    raise
                print(f"    !! 段 {name} 失败(已跳过): {e}", flush=True)
        print(f"    OK {label or path.name} ({ok} 段成功{', '+str(failed)+' 段失败' if failed else ''}, "
              f"{time.time() - t0:.1f}s)", flush=True)
        return {"ok": ok, "failed": failed, "errors": errors}

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print(f"    OK {label or path.name} ({time.time() - t0:.1f}s)", flush=True)
    return {"ok": 1, "failed": 0, "errors": []}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", default=os.environ.get("SEED_DIR", "/seed"))
    ap.add_argument("--schema-only", action="store_true")
    ap.add_argument("--skip-seed", action="store_true")
    ap.add_argument("--skip-data", action="store_true", help="跳过 4-* 全量数据(已导入时用)")
    args = ap.parse_args()

    seed = Path(args.seed_dir)
    if not seed.is_dir():
        print(f"!! 种子目录不存在: {seed}", file=sys.stderr)
        sys.exit(1)

    print("==> 数据库:", db.health(), flush=True)

    schema = seed / "01-schema.sql"
    if schema.exists():
        print("==> 建表 DDL", flush=True)
        exec_file(schema)
    if args.schema_only:
        return

    # 顺序关键:先导 gh_repo 全量数据,再导小表种子
    # (gh_watch_target 有 FK 指向 gh_repo.id,主数据必须先到位)
    if args.skip_data:
        print("==> 跳过 4-* 全量数据(--skip-data)", flush=True)
    else:
        data_files = sorted([p for p in seed.glob("4-*") if p.is_file()],
                            key=lambda p: int(re.sub(r"\D", "", p.name) or 0))
        if data_files:
            print(f"==> gh_repo 全量数据({len(data_files)} 个文件)", flush=True)
            for p in data_files:
                exec_file(p, label=p.name)
        else:
            print("!! 未找到 4-* 数据文件", file=sys.stderr)

    if not args.skip_seed:
        seed_cfg = seed / "02-seed-config.sql"
        if seed_cfg.exists():
            print("==> 配置/任务/名单/周期种子", flush=True)
            exec_file(seed_cfg, transform=_fix_jsonb_literals, label="02-seed-config.sql",
                      segmented=True, tolerate_errors=True)
        prov = seed / "03-llm-providers.sql"
        if prov.exists() and prov.stat().st_size > 0:
            print("==> 模型供应商", flush=True)
            exec_file(prov, segmented=True, tolerate_errors=True)

    for t in ("gh_repo", "gh_app_setting", "gh_llm_task", "gh_rank_whitelist",
              "gh_stat_period", "gh_watch_target"):
        try:
            print(f"    {t}: {db.query_scalar(f'SELECT count(*) FROM {t}')} 行", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"    {t}: 查询失败 {e}", flush=True)
    print("==> 数据加载完成", flush=True)


if __name__ == "__main__":
    main()
