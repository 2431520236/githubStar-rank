"""自检:环境 + 数据库 + 各接口冒烟。

用法:python3 tools/selfcheck.py [base_url]
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9951"

ENDPOINTS = [
    "/api", "/api/system/health", "/api/system/config", "/api/system/counts",
    "/api/stats/overview", "/api/stats/dashboard", "/api/ranks?metric=total&top_n=3",
    "/api/ranks/periods?period_type=week", "/api/repos/meta/langs",
    "/api/repos?top_n=3", "/api/history/summary", "/api/ai/tasks", "/api/ai/providers",
    "/api/watch/list",
]


def get(path: str):
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def main() -> None:
    print(f"==> 冒烟测试 {BASE}")
    passed = 0
    for p in ENDPOINTS:
        code, body = get(p)
        okk = code == 200 and isinstance(body, dict) and body.get("code") == 0
        data = body.get("data") if isinstance(body, dict) else None
        size = len(json.dumps(data, ensure_ascii=False, default=str)) if data is not None else 0
        print(f"    [{'OK ' if okk else 'FAIL'}] {p:48s} http={code} size={size}")
        passed += 1 if okk else 0
    print(f"==> {passed}/{len(ENDPOINTS)} 通过")
    sys.exit(0 if passed == len(ENDPOINTS) else 1)


if __name__ == "__main__":
    main()
