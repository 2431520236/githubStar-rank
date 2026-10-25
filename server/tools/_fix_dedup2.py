"""一次性治理脚本(2026-10): 删除「重复 star 历史」的冗余仓。用完即删。

背景
----
官方 GET /repos/{o}/{r}/stargazers/history 对 **fork** 与 **改名仓旧名** 会返回
归属仓(父仓 / 新名)的同一份历史, 于是同一份数据在 gh_repo_star_weekly / _daily 里
按多个 full_name 各存一份, 造成 star 增量重复统计。

判据
----
指纹 = md5(全部 (week_start, weekly_new) 的有序拼接)。同指纹多仓 = 同一份数据。
收紧: 周数 >= 5 且周增合计 >= 500(排除「只有 1 周 1 星」的小仓随机撞指纹)。

保留谁
------
用 GitHub API 判定(一次 GraphQL 批量查, 不逐仓请求):
  1) 名字 == GitHub 返回的 nameWithOwner 的优先(那是改名后的主仓; 旧名会被 301 到它)
  2) 同为旧名/同为主名时 stargazerCount 大者优先
  3) 再相同则 id 小者优先(先入库的)
其余仓的 gh_repo_star_weekly / gh_repo_star_daily 行删除, 并把判定出的真实
stars 回写 gh_repo(顺带修掉一批 stars=0)。

用法(容器内)
------------
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/_fix_dedup2.py
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/_fix_dedup2.py --write
"""
import argparse
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app import config as cfg  # noqa: E402
from app import db  # noqa: E402

GRAPHQL = "https://api.github.com/graphql"

FP_SQL = """
WITH fp AS (
  SELECT repo_id,
         md5(string_agg(week_start::text || ':' || weekly_new::text, ','
                        ORDER BY week_start)) AS f,
         count(*) AS wk, sum(weekly_new) AS wn
  FROM gh_repo_star_weekly
  GROUP BY repo_id
), g AS (
  SELECT f FROM fp GROUP BY f
  HAVING count(*) > 1 AND max(wk) >= 5 AND max(wn) >= 500
)
SELECT fp.f, fp.repo_id, r.full_name, COALESCE(r.stars, 0) AS stars, fp.wk, fp.wn
FROM fp JOIN g ON g.f = fp.f JOIN gh_repo r ON r.id = fp.repo_id
ORDER BY fp.f, fp.repo_id
"""


def _aliases(rows):
    parts, meta = [], {}
    for i, r in enumerate(rows):
        fn = r["full_name"]
        if "/" not in fn:
            continue
        owner, name = fn.split("/", 1)
        a = "r%d" % i
        meta[a] = r
        parts.append(
            '%s: repository(owner: %s, name: %s) { nameWithOwner stargazerCount isFork '
            'parent { nameWithOwner stargazerCount } }'
            % (a, json.dumps(owner), json.dumps(name))
        )
    return parts, meta


def fetch_gh(rows):
    import requests
    parts, meta = _aliases(rows)
    out = {}
    tok = getattr(cfg, "GITHUB_TOKEN", "") or ""
    hdrs = {"Authorization": "Bearer " + tok} if tok else {}
    for i in range(0, len(parts), 40):
        chunk = parts[i:i + 40]
        body = {"query": "query {" + " ".join(chunk) + "}"}
        try:
            resp = requests.post(GRAPHQL, json=body, headers=hdrs, timeout=40, verify=False)
            data = (resp.json() or {}).get("data") or {}
        except Exception as e:  # noqa: BLE001
            print("[warn] GraphQL 批次失败: %s" % e, flush=True)
            continue
        for a, v in data.items():
            r = meta.get(a)
            if not r or not isinstance(v, dict):
                continue
            out[r["full_name"]] = {
                "stars": int(v.get("stargazerCount") or 0),
                "fork": bool(v.get("isFork")),
                "parent": (v.get("parent") or {}).get("nameWithOwner"),
            }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真删；不带则只报告")
    args = ap.parse_args()

    rows = db.query_all(FP_SQL)
    groups = {}
    for r in rows:
        groups.setdefault(r["f"], []).append(r)
    print("[plan] 重复组 = %d, 涉及仓 = %d" % (len(groups), len(rows)), flush=True)

    info = fetch_gh(rows)
    print("[plan] GitHub 判定命中 = %d/%d" % (len(info), len(rows)), flush=True)

    drops = []
    for f, members in groups.items():
        def rank(m):
            gh = info.get(m["full_name"]) or {}
            return (0 if gh.get("fork") else 1, gh.get("stars", -1), -m["repo_id"])
        keep = sorted(members, key=rank, reverse=True)[0]
        for m in members:
            if m["repo_id"] != keep["repo_id"]:
                drops.append((m["repo_id"], m["full_name"], keep["full_name"]))

    print("[plan] 待删冗余仓 = %d" % len(drops), flush=True)
    for rid, fn, keep in drops[:20]:
        gh = info.get(fn) or {}
        print("       - %-46s (%s) fork=%s stars=%s -> 保留 %s"
              % (fn, rid, gh.get("fork"), gh.get("stars"), keep), flush=True)

    fixed = 0
    if args.write and info:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                for fn, gh in info.items():
                    if gh["stars"] <= 0:
                        continue
                    cur.execute("UPDATE gh_repo SET stars = %s WHERE full_name = %s "
                                "AND COALESCE(stars,0) = 0", [gh["stars"], fn])
                    fixed += cur.rowcount
            conn.commit()
        print("[write] 回写 stars = %d 行" % fixed, flush=True)

    if not drops:
        print("[done] 无冗余, 未改动", flush=True)
        return
    if not args.write:
        print("[dry-run] 未写库。加 --write 真删。", flush=True)
        return

    ids = [d[0] for d in drops]
    nw = db.execute("DELETE FROM gh_repo_star_weekly WHERE repo_id = ANY(%s)", [ids])
    nd = db.execute("DELETE FROM gh_repo_star_daily WHERE repo_id = ANY(%s)", [ids])
    print("[done] 删除 weekly=%s daily=%s (仓 %d)" % (nw, nd, len(ids)), flush=True)


if __name__ == "__main__":
    main()