#!/usr/bin/env python3
"""启动脚本:等待数据库就绪并补齐种子 → uvicorn(默认端口 9951,GH_PORT 可覆盖)。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app import config as cfg  # noqa: E402


def ensure_db() -> None:
    """启动时补齐表结构与种子(幂等);失败不阻塞启动,但会明确打印结果。"""
    if os.getenv("GH_BOOTSTRAP", "true").strip().lower() not in ("1", "true", "yes", "on"):
        print("==> [bootstrap] 已禁用(GH_BOOTSTRAP=false)", flush=True)
        return
    try:
        from tools.bootstrap import bootstrap
        res = bootstrap(verbose=True)
        if not res.get("ok"):
            print(f"!! [bootstrap] 未完成: {res.get('error')}", flush=True)
            print("   表未建好或种子缺失时,请手动执行:python3 tools/load_data.py --seed-dir /seed",
                  flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"!! [bootstrap] 异常({e});若表未建好,请手动执行 tools/load_data.py", flush=True)


def main() -> None:
    os.makedirs(cfg.README_TMP_DIR, exist_ok=True)
    os.makedirs(cfg.DATA_DIR, exist_ok=True)
    ensure_db()
    print(f"==> GitHub 飙升雷达 启动中: http://{cfg.HOST}:{cfg.PORT}/  (docs: /docs)", flush=True)
    print(f"==> 数据库: {cfg.DB_HOST}:{cfg.DB_PORT}/{cfg.DB_NAME}", flush=True)
    uvicorn.run("app.main:app", host=cfg.HOST, port=cfg.PORT, reload=False,
                log_level="debug" if cfg.DEBUG else "info")


if __name__ == "__main__":
    main()
