"""关注提醒服务:关注列表 CRUD、事件检测与推送。

对应手册《04 · 后端服务层逻辑》10. watch_service。
"""
import logging
from typing import Any, Optional

import requests

from .. import db
from ..utils import as_int

log = logging.getLogger("ghradar.watch")


def list_watches(limit: int = 200) -> list[dict]:
    """关注列表。带出 gh_repo 的项目信息(语言/star/最后更新时间)供卡片展示。

    排序按**项目最后更新时间**倒序(用户 2026-10 定): 最近有更新的项目排前面;
    项目没有更新时间(如刚建的占位行)则回落到关注时间。
    """
    return db.query_all("""
        SELECT w.id, w.repo_id, w.full_name, w.watch_type, w.threshold, w.interval_min,
               w.enabled, w.note, w.created_at,
               r.lang, r.stars, r.repo_updated_at, r.repo_pushed_at, r.description,
               r.ai_summary, r.summary, r.html_url, r.is_major
        FROM gh_watch_target w
        LEFT JOIN gh_repo r ON r.id = w.repo_id
        ORDER BY COALESCE(r.repo_updated_at, r.repo_pushed_at, w.created_at) DESC
        LIMIT %s
    """, [as_int(limit, 200)])


def add_watch(full_name: str, watch_type: str = "any", threshold: int = 50,
              interval_min: int = 60, webhook_url: Optional[str] = None,
              note: Optional[str] = None, channels: Optional[list] = None) -> dict:
    """新增关注(自动建 gh_repo 占位行)。"""
    fn = (full_name or "").strip()
    if "/" not in fn:
        raise ValueError("full_name 需为 owner/repo")
    repo = db.query_one("SELECT id FROM gh_repo WHERE full_name = %s", [fn])
    if not repo:
        owner, name = fn.split("/", 1)
        rows = db.execute("""
            INSERT INTO gh_repo (full_name, owner, name, html_url, api_url)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (full_name) DO UPDATE SET updated_at = now() RETURNING id
        """, [fn, owner, name, f"https://github.com/{fn}", f"https://api.github.com/repos/{fn}"],
            returning=True)
        repo = rows[0]
    rows = db.execute("""
        INSERT INTO gh_watch_target
            (repo_id, full_name, watch_type, threshold, interval_min, channels, webhook_url, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (full_name) DO UPDATE SET
            watch_type = EXCLUDED.watch_type, threshold = EXCLUDED.threshold,
            interval_min = EXCLUDED.interval_min, webhook_url = EXCLUDED.webhook_url,
            note = EXCLUDED.note, enabled = TRUE, updated_at = now()
        RETURNING *
    """, [repo["id"], fn, watch_type, as_int(threshold, 50), as_int(interval_min, 60),
          db.Json(channels or ["ui"]), webhook_url, note], returning=True)
    return rows[0] if rows else {}


def update_watch(wid: int, **fields: Any) -> dict:
    allowed = {"enabled", "threshold", "interval_min", "watch_type", "webhook_url", "note"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k} = %s")
            params.append(v)
    if not sets:
        return {}
    params.append(as_int(wid))
    rows = db.execute(f"UPDATE gh_watch_target SET {', '.join(sets)}, updated_at = now() "
                      f"WHERE id = %s RETURNING *", params, returning=True)
    return rows[0] if rows else {}


def remove_watch(wid: int) -> int:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gh_watch_target WHERE id = %s", [as_int(wid)])
            n = cur.rowcount
        conn.commit()
    return n


def _detect(target: dict) -> Optional[dict]:
    """判定是否触发事件。"""
    rid = target["repo_id"]
    since = target.get("last_checked_at")
    wt = target.get("watch_type") or "any"
    threshold = as_int(target.get("threshold"), 0)

    if wt in ("star_gain", "any"):
        gain = db.query_scalar(
            "SELECT COALESCE(SUM(delta_stars),0) FROM gh_repo_star_daily "
            "WHERE repo_id = %s AND stat_date >= CURRENT_DATE - 7", [rid])
        gain = as_int(gain)
        if gain == 0:
            gain = as_int(db.query_scalar("SELECT COALESCE(star_gain_7d,0) FROM gh_repo WHERE id = %s",
                                          [rid]))
        if gain >= threshold and threshold > 0:
            return {"event_type": "star_gain", "level": "warning" if gain >= threshold * 2 else "info",
                    "title": f"近 7 天新增 {gain} star",
                    "content": f"{target['full_name']} 近 7 天新增 {gain} star(阈值 {threshold})",
                    "payload": {"gain_7d": gain, "threshold": threshold}}
    if wt in ("release", "any"):
        row = db.query_one("SELECT latest_release_at, latest_version FROM gh_repo WHERE id = %s", [rid])
        if row and row.get("latest_release_at") and (since is None or row["latest_release_at"] > since):
            return {"event_type": "release", "level": "info",
                    "title": f"新版本 {row.get('latest_version') or ''}",
                    "content": f"{target['full_name']} 发布新版本 {row.get('latest_version') or ''}",
                    "payload": {"version": row.get("latest_version")}}
    if wt in ("info_change", "any"):
        row = db.query_one("SELECT repo_pushed_at FROM gh_repo WHERE id = %s", [rid])
        if row and row.get("repo_pushed_at") and (since is None or row["repo_pushed_at"] > since):
            return {"event_type": "info_change", "level": "info", "title": "项目有更新",
                    "content": f"{target['full_name']} 最近有代码推送",
                    "payload": {"pushed_at": str(row.get("repo_pushed_at"))}}
    return None


def check_watches(force: bool = False, watch_id: Optional[int] = None) -> dict:
    """遍历关注项,判定并写事件 + webhook 推送。"""
    if watch_id:
        targets = db.query_all("SELECT * FROM gh_watch_target WHERE id = %s", [as_int(watch_id)])
    elif force:
        targets = db.query_all("SELECT * FROM gh_watch_target WHERE enabled")
    else:
        targets = db.query_all("""
            SELECT * FROM gh_watch_target WHERE enabled
              AND (last_checked_at IS NULL OR last_checked_at < now() - make_interval(mins => interval_min))
        """)
    fired = 0
    for t in targets:
        ev = _detect(t)
        if ev:
            db.execute("""
                INSERT INTO gh_watch_event
                    (watch_id, repo_id, full_name, event_type, level, title, content, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, [t["id"], t["repo_id"], t["full_name"], ev["event_type"], ev["level"],
                  ev["title"], ev["content"], db.Json(ev["payload"])])
            fired += 1
            if t.get("webhook_url"):
                _push(t["webhook_url"], {**ev, "full_name": t["full_name"]})
            db.execute("UPDATE gh_watch_target SET last_event_at = now() WHERE id = %s", [t["id"]])
        db.execute("UPDATE gh_watch_target SET last_checked_at = now() WHERE id = %s", [t["id"]])
    return {"checked": len(targets), "fired": fired}


def _push(url: str, payload: dict) -> None:
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:  # noqa: BLE001
        log.warning("webhook 推送失败 %s: %s", url, e)


def list_events(unread_only: bool = False, limit: int = 100) -> list[dict]:
    cond = "WHERE NOT is_read" if unread_only else ""
    return db.query_all(f"SELECT * FROM gh_watch_event {cond} ORDER BY created_at DESC LIMIT %s",
                        [as_int(limit, 100)])


def mark_read(all_: bool = False, ids: Optional[list] = None) -> int:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            if all_:
                cur.execute("UPDATE gh_watch_event SET is_read = TRUE WHERE NOT is_read")
            elif ids:
                cur.execute("UPDATE gh_watch_event SET is_read = TRUE WHERE id = ANY(%s)",
                            [list(ids)])
            else:
                return 0
            n = cur.rowcount
        conn.commit()
    return n
