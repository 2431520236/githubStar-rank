"""验证写入拦截真的生效(用完即删)。喂脏数据 + 合法数据, 看脏行是否被拒。"""
import sys
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app import db  # noqa: E402
from app.services import facts_service  # noqa: E402

rid = db.query_scalar("SELECT id FROM gh_repo WHERE full_name = %s", ["codecrafters-io/build-your-own-x"])
if not rid:
    rid = db.query_scalar("SELECT id FROM gh_repo ORDER BY id LIMIT 1")
print("[目标仓] repo_id =", rid, flush=True)

TEST_DATE = "1999-01-04"
db.execute("DELETE FROM gh_repo_star_daily WHERE repo_id=%s AND stat_date=%s", [rid, TEST_DATE])

dirty = [
    {"date": TEST_DATE, "count": 550234, "stars": 0},
    {"date": TEST_DATE, "count": 999, "stars": 100},
    {"date": TEST_DATE, "count": -5, "stars": 100},
]
good = [{"date": TEST_DATE, "count": 7, "stars": 100}]

res_dirty = facts_service.save_history(rid, [], dirty)
print("[脏数据] 返回 =", res_dirty, flush=True)
n_dirty = db.query_scalar(
    "SELECT count(*) FROM gh_repo_star_daily WHERE repo_id=%s AND stat_date=%s", [rid, TEST_DATE])
print("[脏数据] 实际入库行数 =", n_dirty, "(应为 0)", flush=True)

res_good = facts_service.save_history(rid, [], good)
print("[合法数据] 返回 =", res_good, flush=True)
row = db.query_all(
    "SELECT stars, delta_stars FROM gh_repo_star_daily WHERE repo_id=%s AND stat_date=%s",
    [rid, TEST_DATE])
print("[合法数据] 实际入库 =", row, "(应为 1 行 stars=100 delta=7)", flush=True)

db.execute("DELETE FROM gh_repo_star_daily WHERE repo_id=%s AND stat_date=%s", [rid, TEST_DATE])
print("[清理] 测试行已删", flush=True)

ok = (n_dirty == 0 and len(row) == 1 and row[0]["delta_stars"] == 7)
print("[结论]", "拦截生效" if ok else "拦截未生效", flush=True)