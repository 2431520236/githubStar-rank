"""数据库自检 / 迁移工具:应用 schema 并打印关键表行数。

用法:
    python3 tools/migrate.py [--schema docs/sql/01-schema.sql] [--seed docs/sql/02-seed-config.sql]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as cfg  # noqa: E402
from app import db  # noqa: E402


def run_sql_file(path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    print(f"==> 执行 {path.name} ({len(sql)} 字节)")
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print(f"    OK: {path.name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="../docs/sql/01-schema.sql")
    ap.add_argument("--seed", default="../docs/sql/02-seed-config.sql")
    ap.add_argument("--skip-seed", action="store_true")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent
    schema = (base / args.schema).resolve()
    seed = (base / args.seed).resolve()

    db.init_pool()
    print("==> 数据库:", db.health())
    if schema.exists():
        run_sql_file(schema)
    else:
        print(f"!! 未找到 schema: {schema}")
    if not args.skip_seed and seed.exists():
        run_sql_file(seed)
    for t in ("gh_repo", "gh_app_setting", "gh_llm_task", "gh_rank_whitelist"):
        try:
            print(f"    {t}: {db.query_scalar(f'SELECT count(*) FROM {t}')} 行")
        except Exception as e:  # noqa: BLE001
            print(f"    {t}: 查询失败 {e}")
    print("==> 完成。剩余数据(4-1..4-10)请用 psql 或 --data 导入")
    print(f"    psql -h {cfg.DB_HOST} -p {cfg.DB_PORT} -U {cfg.DB_USER} -d {cfg.DB_NAME} "
          f"-f docs/sql/4-1")


if __name__ == "__main__":
    main()
