"""启动钩子:确保数据库结构与种子数据就绪(幂等,可重复执行)。

在 api 容器启动时由 run.py 调用,也可在宿主机手动执行:
    python3 tools/bootstrap.py

设计要点:
  - **带重试等待数据库就绪** —— compose 里 db 的 healthcheck 虽然等到 gh_repo 可查,
    但容器编排/网络抖动仍可能让首次连接失败,这里做最后一道保险;
  - 只补小表种子(01 结构 + 02 配置 + 03 供应商);
    gh_repo 全量数据(4-*)由 initdb 阶段用 psql 导入,或手动跑 load_data.py。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as cfg  # noqa: E402
from app import db  # noqa: E402
from tools.load_data import _fix_jsonb_literals, exec_file  # noqa: E402

SEED_DIRS = ["/seed", str(cfg.BASE_DIR.parent / "docs" / "sql")]


def _find_seed_dir() -> Path | None:
    for d in SEED_DIRS:
        p = Path(d)
        if p.is_dir() and (p / "01-schema.sql").exists():
            return p
    return None


def wait_for_db(timeout: int = 180, interval: float = 3.0) -> bool:
    """等待数据库可连接(重试),返回是否成功。"""
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            db.close_pool()
            db.init_pool()
            if db.query_scalar("SELECT 1") == 1:
                return True
        except Exception as e:  # noqa: BLE001
            if attempt == 1 or attempt % 5 == 0:
                print(f"    [bootstrap] 等待数据库就绪… ({attempt} 次,{e.__class__.__name__})",
                      flush=True)
            time.sleep(interval)
    return False


def bootstrap(verbose: bool = True, wait_timeout: int = 180) -> dict:
    seed = _find_seed_dir()
    if seed is None:
        return {"ok": False, "error": "未找到种子目录(01-schema.sql)"}

    if not wait_for_db(wait_timeout):
        return {"ok": False, "error": f"数据库 {cfg.DB_HOST}:{cfg.DB_PORT} 等待超时"}

    if verbose:
        print(f"==> [bootstrap] 数据库就绪,种子目录: {seed}", flush=True)

    # 快路径：库里已经有仓库数据 → 说明 initdb 已经导过整套资料包，直接跳过，
    # 避免每次重启都把 1300 行 DDL 重放一遍。
    ready = False
    try:
        ready = (db.query_scalar("SELECT count(*) FROM gh_repo") or 0) > 0
    except Exception:  # noqa: BLE001
        ready = False

    cfg_done = False
    if ready:
        if verbose:
            print("==> [bootstrap] 数据已就绪(gh_repo 有数据)，跳过建表与种子", flush=True)
    else:
        exec_file(seed / "01-schema.sql", label="01-schema.sql")
        # 新资料包：配置在 10-config.sql（COPY 块）；老资料包：02/03
        for name in ("10-config.sql", "02-seed-config.sql"):
            f = seed / name
            if f.exists() and f.stat().st_size > 0:
                if verbose:
                    print(f"==> [bootstrap] 配置/任务/名单种子 {name}", flush=True)
                try:
                    res = exec_file(f, transform=_fix_jsonb_literals, label=name,
                                    segmented=True, tolerate_errors=True)
                    cfg_done = res.get("failed", 0) == 0
                except Exception as e:  # noqa: BLE001
                    print(f"!! [bootstrap] {name} 失败(已跳过): {e}", flush=True)
                break
        prov = seed / "03-llm-providers.sql"
        if prov.exists() and prov.stat().st_size > 0:
            try:
                exec_file(prov, label="03-llm-providers.sql", segmented=True, tolerate_errors=True)
            except Exception as e:  # noqa: BLE001
                print(f"!! [bootstrap] 03 供应商失败(已跳过): {e}", flush=True)

    counts = {}
    for t in ("gh_repo", "gh_app_setting", "gh_llm_task", "gh_rank_whitelist", "gh_stat_period"):
        try:
            counts[t] = db.query_scalar(f"SELECT count(*) FROM {t}")
        except Exception:  # noqa: BLE001
            counts[t] = None
    if verbose:
        print(f"==> [bootstrap] 完成: {counts}", flush=True)
    return {"ok": True, "seed_dir": str(seed), "seed_config_applied": cfg_done, "counts": counts}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.parse_args()
    print(bootstrap())
