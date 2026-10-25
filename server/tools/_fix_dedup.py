"""一次性清理脚本(2026-10): 删除改名仓旧名持有的重复 star 历史。

背景: 官方 GET /repos/{o}/{r}/stargazers/history 对改名仓会返回改名后主仓的完整历史,
于是「同一个仓的多个历史名字」各自被拉了一份完全相同的逐周历史 -> 逐周数值逐位相同。
用 md5(全部 (week_start, weekly_new)) 做指纹, 同指纹多仓 = 重复。

保留规则(每组): ① stars>0 的优先; ② 都有/都无 stars 时取 id 最小(先入库的那个)。
其余仓的 gh_repo_star_weekly / gh_repo_star_daily 行删除。

用法(容器内):
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/_fix_dedup.py --dry-run
    docker compose exec -T -e PYTHONPATH=/app api python3 tools/_fix_dedup.py --write
"""
import argparse
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app import db  # noqa: E402

FP_SQL = """
WITH fp AS (
  SELECT repo_id,
         md5(string_agg(week_start::text || ':' || weekly_new::text, ','
                        ORDER BY week_start)) AS f,
         count(*) AS wk, sum(weekly_new) AS wn
  FROM gh_repo_star_weekly
  GROUP BY repo_id
), g AS (
  -- 收紧判据: 排除退化组。否则「只有 1 周 1 星」的小仓会互相撞指纹(实测 445 个假阳性),
  -- 那些既不是改名变体、删了也没意义。真重复(改名仓旧名)是长序列逐周相同。
  SELECT f FROM fp GROUP BY f
  HAVING count(*) > 1 AND max(wk) >= 5 AND max(wn) >= 500
)
SELECT fp.f, fp.repo_id, COALESCE(r.stars, 0) AS stars, r.full_name, fp.wn
FROM fp JOIN g ON g.f = fp.f JOIN gh_repo r ON r.id = fp.repo_id
ORDER BY fp.f, COALESCE(r.stars, 0) DESC, fp.repo_id
"""


def plan() -> list[tuple[int, str]]:
    rows = db.query_all(FP_SQL)
    keep: dict[str, int] = {}
    for r in rows:
        keep.setdefault(r["f"], r["repo_id"])   # 排序已保证第一条是保留者
    drops = [(r["repo_id"], r["full_name"]) for r in rows if keep[r["f"]] != r["repo_id"]]
    return drops


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真删；不带则只报告")
    args = ap.parse_args()
    drops = plan()
    ids = [d[0] for d in drops]
    print("[plan] 待删仓数 = %d（保留每组首个）" % len(ids), flush=True)
    for rid, fn in drops[:15]:
        print("       - %s (%s)" % (fn, rid), flush=True)
    if not ids:
        print("[done] 无重复，未改动", flush=True)
        return
    if not args.write:
        print("[dry-run] 未写库。加 --write 真删。", flush=True)
        return
    nw = db.execute("DELETE FROM gh_repo_star_weekly WHERE repo_id = ANY(%s)", [ids])
    nd = db.execute("DELETE FROM gh_repo_star_daily WHERE repo_id = ANY(%s)", [ids])
    print("[done] 删除 weekly=%s daily=%s" % (nw, nd), flush=True)


if __name__ == "__main__":
    main()