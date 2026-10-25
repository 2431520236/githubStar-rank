"""仓库服务:gh_repo 读写核心(UPSERT 合并策略 / 过滤 / 候选挑选)。

对应手册《04 · 后端服务层逻辑》1. repo_service。
"""
from typing import Any, Iterable, Optional, Sequence

from psycopg2.extras import Json, execute_values

from .. import config as cfg
from .. import db
from ..utils import as_int, csv_list, like_terms, norm_page, page_result

# ---------------- 排序白名单(防注入) ----------------
SORT_MAP = {
    "stars": "stars DESC",
    "forks": "forks DESC",
    "gain_1d": "star_gain_1d DESC",
    "gain_7d": "star_gain_7d DESC",
    "gain_30d": "star_gain_30d DESC",
    "gain_60d": "star_gain_60d DESC",
    "velocity": "daily_velocity DESC",
    "updated": "repo_updated_at DESC",
    "pushed": "repo_pushed_at DESC",
    "release": "latest_release_at DESC",
    "seen": "first_seen_at DESC",
    "name": "full_name ASC",
}

# ---------------- UPSERT 列(42 列,不含 id/created_at/updated_at) ----------------
UPSERT_COLS = [
    "full_name", "owner", "name", "html_url", "api_url", "readme_url",
    "readme_en_url", "readme_ch_url", "homepage", "description", "summary",
    "ai_summary", "ai_summary_tries", "lang", "license_spdx", "license_name",
    "topics", "stars", "forks", "watchers", "open_issues", "size_kb",
    "contributors_count", "latest_version", "version_note", "latest_release_at",
    "default_branch", "is_archived", "is_fork", "repo_created_at",
    "repo_pushed_at", "repo_updated_at", "github_id", "star_gain_7d",
    "star_gain_30d", "star_gain_60d", "star_gain_1d", "daily_velocity", "sources",
    "seed_metrics", "is_major", "detail_synced_at",
]

# JSONB 列需要 Json() 包装
_JSON_COLS = {"topics", "sources", "seed_metrics"}
_INT_COLS = {"stars", "forks", "watchers", "open_issues", "size_kb",
             "contributors_count", "ai_summary_tries", "star_gain_7d",
             "star_gain_30d", "star_gain_60d", "star_gain_1d"}
_BOOL_COLS = {"is_archived", "is_fork", "is_major"}

# 合并策略分组
_ALWAYS = {"sources", "seed_metrics", "is_major", "stars", "forks", "watchers"}
_KEEP_TEXT = {"summary", "description", "lang", "license_spdx", "license_name",
              "homepage", "readme_url", "readme_en_url", "readme_ch_url",
              "latest_version", "version_note", "default_branch"}
_KEEP_TS = {"detail_synced_at", "history_synced_at", "readme_synced_at",
            "ai_summary_synced_at"}


def _defaults(row: dict) -> dict:
    """补全缺失列 + 类型规范化。"""
    out: dict[str, Any] = {}
    for col in UPSERT_COLS:
        v = row.get(col)
        if col in _JSON_COLS:
            if v is None:
                v = [] if col != "seed_metrics" else {}
            out[col] = Json(v)
        elif col == "github_id":
            # 缺失/非法一律 None（不是 0）—— 0 会被当成有效值覆盖旧的非空 id。
            # upsert 的 COALESCE(EXCLUDED, gh_repo) 分支据此保留库内已有的 id。
            out[col] = as_int(v, 0) or None
        elif col in _INT_COLS:
            out[col] = as_int(v, 0)
        elif col in _BOOL_COLS:
            out[col] = bool(v)
        else:
            out[col] = v
    fn = out.get("full_name") or ""
    if "/" not in fn:
        raise ValueError(f"full_name 非法(需 owner/repo): {fn!r}")
    if not out.get("owner"):
        out["owner"] = fn.split("/", 1)[0]
    if not out.get("name"):
        out["name"] = fn.split("/", 1)[1]
    return out


def _upsert_sql() -> str:
    SQ = chr(39)  # 单引号常量，避免嵌套转义
    cols = ", ".join(f'"{c}"' for c in UPSERT_COLS)
    sets: list[str] = []
    for c in UPSERT_COLS:
        if c == "full_name":
            continue
        if c in _ALWAYS:
            if c == "sources":
                # sources 是 JSONB 数组 → 合并去重
                sets.append(
                    '"' + c + '" = (SELECT COALESCE(jsonb_agg(DISTINCT e), ' + SQ + '[]' + SQ + '::jsonb) '
                    'FROM jsonb_array_elements(COALESCE(gh_repo."' + c + '", ' + SQ + '[]' + SQ + '::jsonb) '
                    '|| COALESCE(EXCLUDED."' + c + '", ' + SQ + '[]' + SQ + '::jsonb)) e)'
                )
            elif c == "seed_metrics":
                # seed_metrics 是 JSONB 对象({}) → 用 || 合并, 不能走 jsonb_array_elements
                sets.append(
                    '"' + c + '" = COALESCE(gh_repo."' + c + '", ' + SQ + '{}' + SQ + '::jsonb) '
                    '|| COALESCE(EXCLUDED."' + c + '", ' + SQ + '{}' + SQ + '::jsonb)'
                )
            elif c == "is_major":
                sets.append(f'\"{c}\" = (gh_repo."{c}" OR EXCLUDED."{c}")')
            else:  # stars/forks/watchers 非 0 覆盖
                sets.append(f'"{c}" = CASE WHEN EXCLUDED."{c}" > 0 THEN EXCLUDED."{c}" '
                            f'ELSE gh_repo."{c}" END')
        elif c in _KEEP_TEXT:
            sets.append(f'"{c}" = COALESCE(NULLIF(gh_repo."{c}", \'\'), EXCLUDED."{c}")')
        elif c in _KEEP_TS:
            sets.append(f'"{c}" = COALESCE(gh_repo."{c}", EXCLUDED."{c}")')
        elif c == "contributors_count":
            sets.append(f'"{c}" = CASE WHEN COALESCE(gh_repo."{c}", 0) > 0 '
                        f'THEN gh_repo."{c}" ELSE EXCLUDED."{c}" END')
        elif c == "ai_summary":
            # AI 概要只补空,绝不覆盖已有(手册 06 §4)
            sets.append(f'"{c}" = CASE WHEN COALESCE(gh_repo."{c}", \'\') = \'\' '
                        f'THEN COALESCE(EXCLUDED."{c}", \'\') ELSE gh_repo."{c}" END')
        else:
            sets.append(f'"{c}" = COALESCE(EXCLUDED."{c}", gh_repo."{c}")')
    sets.append('"updated_at" = now()')
    return (f'INSERT INTO gh_repo ({cols}) VALUES %s '
            f'ON CONFLICT (full_name) DO UPDATE SET ' + ", ".join(sets))


def _merge_renames(prepared: Sequence[dict]) -> dict:
    """按 `github_id` 归并「改名仓」—— 同一个 GitHub 仓库只允许存在一行。

    为什么必须做:仓库改名后官方 `stargazers/history` 会按**新名**返回完整历史,
    而 `ON CONFLICT (full_name)` 匹配不到旧名的行,于是**新建一行** ——
    同一个仓的旧名与新名各持一份逐周数值逐位相同的周/日历史,被统计 N 遍。
    实测 51 组 / 105 仓 / 虚增 1,001,882(占周表总量 3.2%)。

    做法:upsert 前先把库内同 `github_id` 的行**改名到新名**;
    若新名已被另一行占用(就是重复的形态),先把它名下的历史行迁移到保留行
    (按唯一键 NOT EXISTS 过滤,避免撞约束),再删掉它 —— 其残留行由 FK
    `ON DELETE CASCADE` 一并清除,使业务唯一键回到同一行。

    `github_id` 缺失的行(非官方详情采集导入的仓)不做任何处理。
    """
    # 两类改名都要识别：
    #  (1) 库内行已有 github_id → 按 id 匹配官方新名；
    #  (2) 库内行 github_id 仍为 NULL（回填阶段）→ 按**采集时用的旧名**匹配，
    #      采集脚本把请求用的名字放在 `_prev_full_name` 里。
    jobs: dict = {}
    pairs = {p["github_id"]: p["full_name"] for p in prepared if p.get("github_id")}
    if pairs:
        for r in db.query_all(
                "SELECT id, github_id, full_name FROM gh_repo WHERE github_id = ANY(%s)",
                [list(pairs)]):
            new_name = pairs.get(r["github_id"])
            if new_name and new_name != r["full_name"]:
                jobs[r["id"]] = new_name
    prevs = {(p.get("_prev_full_name") or "").lower(): p["full_name"] for p in prepared
             if p.get("_prev_full_name") and p["full_name"] != p["_prev_full_name"]}
    if prevs:
        for r in db.query_all(
                "SELECT id, full_name FROM gh_repo WHERE lower(full_name) = ANY(%s)",
                [list(prevs)]):
            new_name = prevs.get((r["full_name"] or "").lower())
            if new_name and new_name != r["full_name"]:
                jobs[r["id"]] = new_name
    if not jobs:
        return {"renamed": 0, "merged": 0}
    renamed = merged = 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for rid, new_name in jobs.items():
                cur.execute("SELECT id FROM gh_repo WHERE full_name = %s", [new_name])
                dup = cur.fetchone()
                if dup and dup[0] != rid:
                    keep, drop = rid, dup[0]
                    # 删行前先把被删行的**基础信息（含中文概要）合并到保留行** ——
                    # 两行是同一个仓库的不同名字阶段，概要/描述可能只写在其中一行上，不能丢。
                    cur.execute("""
                        UPDATE gh_repo k SET
                            description = COALESCE(NULLIF(k.description, ''), d.description),
                            summary = CASE WHEN COALESCE(k.summary, '') = ''
                                           THEN COALESCE(d.summary, '') ELSE k.summary END,
                            ai_summary = CASE WHEN COALESCE(k.ai_summary, '') = ''
                                              THEN COALESCE(d.ai_summary, '') ELSE k.ai_summary END,
                            ai_summary_tries = GREATEST(COALESCE(k.ai_summary_tries, 0),
                                                        COALESCE(d.ai_summary_tries, 0)),
                            lang = COALESCE(k.lang, d.lang),
                            license_spdx = COALESCE(k.license_spdx, d.license_spdx),
                            license_name = COALESCE(k.license_name, d.license_name),
                            homepage = COALESCE(k.homepage, d.homepage),
                            topics = CASE WHEN COALESCE(jsonb_array_length(k.topics), 0) = 0
                                          THEN d.topics ELSE k.topics END,
                            stars = GREATEST(COALESCE(k.stars, 0), COALESCE(d.stars, 0)),
                            forks = GREATEST(COALESCE(k.forks, 0), COALESCE(d.forks, 0)),
                            is_major = (COALESCE(k.is_major, FALSE) OR COALESCE(d.is_major, FALSE)),
                            detail_synced_at = COALESCE(k.detail_synced_at, d.detail_synced_at),
                            readme_synced_at = COALESCE(k.readme_synced_at, d.readme_synced_at),
                            ai_summary_synced_at = COALESCE(k.ai_summary_synced_at,
                                                            d.ai_summary_synced_at),
                            first_seen_at = LEAST(k.first_seen_at, d.first_seen_at)
                        FROM gh_repo d WHERE k.id = %s AND d.id = %s
                    """, [keep, drop])
                    cur.execute(
                        "UPDATE gh_repo_star_weekly w SET repo_id = %s WHERE w.repo_id = %s "
                        "AND NOT EXISTS (SELECT 1 FROM gh_repo_star_weekly x "
                        "                WHERE x.repo_id = %s AND x.week_start = w.week_start)",
                        [keep, drop, keep])
                    cur.execute(
                        "UPDATE gh_repo_star_daily d SET repo_id = %s WHERE d.repo_id = %s "
                        "AND NOT EXISTS (SELECT 1 FROM gh_repo_star_daily x "
                        "                WHERE x.repo_id = %s AND x.stat_date = d.stat_date)",
                        [keep, drop, keep])
                    cur.execute("DELETE FROM gh_repo WHERE id = %s", [drop])
                    merged += 1
                owner, _, name = new_name.partition("/")
                cur.execute(
                    "UPDATE gh_repo SET full_name = %s, owner = %s, name = %s, updated_at = now() "
                    "WHERE id = %s", [new_name, owner, name, rid])
                renamed += 1
        conn.commit()
    return {"renamed": renamed, "merged": merged}


def upsert_repos(rows: Sequence[dict]) -> int:
    """批量 upsert,返回写入条数。

    写入前先做**改名归并**(见 `_merge_renames`) —— 这是「同一仓库被算两遍」的根治处。
    """
    if not rows:
        return 0
    # 归并必须在 _defaults 之前：_defaults 只保留 UPSERT_COLS，会丢掉
    # 采集脚本用于识别改名的 `_prev_full_name`（库内 github_id 尚为 NULL 时唯一的线索）。
    _merge_renames(rows)
    prepared = [_defaults(r) for r in rows]
    values = [tuple(p[c] for c in UPSERT_COLS) for p in prepared]
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, _upsert_sql(), values, page_size=500)
        conn.commit()
    return len(values)


def upsert_repos_report(rows: Sequence[dict]) -> dict:
    """入库并识别新项目(用于「本次新增 N 个」)。"""
    if not rows:
        return {"upserted": 0, "new": 0, "new_names": []}
    names = [r["full_name"] for r in rows if r.get("full_name")]
    existed = set()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT full_name FROM gh_repo WHERE full_name = ANY(%s)", (names,))
            existed = {r[0] for r in cur.fetchall()}
    n = upsert_repos(rows)
    new_names = [x for x in names if x not in existed]
    return {"upserted": n, "new": len(new_names), "new_names": new_names}


def refresh_major_flags() -> int:
    """按白名单 list_type='major' 同步 gh_repo.is_major,返回受影响行数。"""
    sql = """
    UPDATE gh_repo r SET is_major = EXISTS (
        SELECT 1 FROM gh_rank_whitelist w
        WHERE w.full_name = r.full_name AND w.list_type = 'major' AND w.enabled
    ), updated_at = now()
    WHERE r.is_major IS DISTINCT FROM EXISTS (
        SELECT 1 FROM gh_rank_whitelist w
        WHERE w.full_name = r.full_name AND w.list_type = 'major' AND w.enabled
    )
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            n = cur.rowcount
        conn.commit()
    return n


def save_contributors(repo_id: int, contributors: Sequence[dict]) -> int:
    if not contributors:
        return 0
    rows = [(repo_id, c.get("login"), c.get("avatar_url"), c.get("html_url"),
             as_int(c.get("contributions")), i + 1)
            for i, c in enumerate(contributors) if c.get("login")]
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO gh_repo_contributor
                    (repo_id, login, avatar_url, html_url, contributions, rank_no)
                VALUES %s
                ON CONFLICT (repo_id, login) DO UPDATE SET
                    avatar_url = EXCLUDED.avatar_url,
                    html_url = EXCLUDED.html_url,
                    contributions = EXCLUDED.contributions,
                    rank_no = EXCLUDED.rank_no,
                    updated_at = now()
            """, rows)
        conn.commit()
    return len(rows)


def save_releases(repo_id: int, releases: Sequence[dict]) -> int:
    if not releases:
        return 0
    rows = [(repo_id, r.get("tag_name"), r.get("name"), r.get("body"),
             r.get("published_at"), bool(r.get("prerelease")), bool(r.get("is_latest")))
            for r in releases if r.get("tag_name")]
    if not rows:
        return 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO gh_repo_release
                    (repo_id, tag_name, name, body, published_at, is_prerelease, is_latest)
                VALUES %s
                ON CONFLICT (repo_id, tag_name) DO UPDATE SET
                    name = EXCLUDED.name,
                    body = EXCLUDED.body,
                    published_at = EXCLUDED.published_at,
                    is_prerelease = EXCLUDED.is_prerelease,
                    is_latest = EXCLUDED.is_latest
            """, rows)
        conn.commit()
    return len(rows)


# ---------------- 过滤条件 ----------------
def _filter_clauses(min_stars: Optional[int] = None, max_age_days: Optional[int] = None,
                    lang: Optional[str] = None, langs: Optional[Sequence[str]] = None,
                    license_spdx: Optional[str] = None, topic: Optional[str] = None,
                    source: Optional[str] = None, min_gain: Optional[int] = None,
                    major: Optional[bool] = None, archived: Optional[bool] = None,
                    has_summary: Optional[bool] = None,
                    q: Optional[str] = None) -> tuple[list[str], list[Any]]:
    ms = cfg.DEFAULT_MIN_STARS if min_stars is None else as_int(min_stars)
    mad = cfg.DEFAULT_MAX_AGE_DAYS if max_age_days is None else as_int(max_age_days)
    where: list[str] = []
    params: list[Any] = []

    if ms > 0:
        where.append("stars >= %s")
        params.append(ms)
    if mad > 0:
        where.append("COALESCE(repo_pushed_at, repo_updated_at, repo_created_at, first_seen_at) "
                     ">= now() - make_interval(days => %s)")
        params.append(mad)
    if lang:
        where.append("lang = %s")
        params.append(lang)
    if langs:
        l = list(langs)
        if "__none__" in l:
            l = [x for x in l if x != "__none__"]
            if l:
                where.append("(lang IS NULL OR lang = '' OR lang = ANY(%s))")
                params.append(l)
            else:
                where.append("(lang IS NULL OR lang = '')")
        else:
            where.append("lang = ANY(%s)")
            params.append(l)
    if license_spdx:
        where.append("license_spdx = %s")
        params.append(license_spdx)
    if topic:
        where.append("topics @> %s::jsonb")
        params.append(Json([topic]))
    if source:
        where.append("sources @> %s::jsonb")
        params.append(Json([source]))
    if min_gain:
        where.append("GREATEST(COALESCE(star_gain_1d,0), COALESCE(star_gain_7d,0), "
                     "COALESCE(star_gain_30d,0), COALESCE(star_gain_60d,0)) >= %s")
        params.append(as_int(min_gain))
    if major is True:
        where.append("is_major = TRUE")
    elif major is False:
        where.append("is_major = FALSE")
    if archived is True:
        where.append("is_archived = TRUE")
    elif archived is False:
        where.append("is_archived = FALSE")
    if has_summary is True:
        where.append("COALESCE(ai_summary,'') <> ''")
    elif has_summary is False:
        where.append("COALESCE(ai_summary,'') = ''")
    for t in like_terms(q):
        where.append("(full_name ILIKE %s OR COALESCE(description,'') ILIKE %s "
                     "OR COALESCE(summary,'') ILIKE %s OR COALESCE(ai_summary,'') ILIKE %s)")
        params.extend([f"%{t}%"] * 4)
    return where, params


def list_repos(q=None, lang=None, langs=None, license_spdx=None, topic=None, source=None,
               min_stars=None, max_age_days=None, min_gain=None, major=None, archived=None,
               has_summary=None, sort="stars", order="desc", page=1, page_size=None,
               top_n=None) -> dict:
    where, params = _filter_clauses(min_stars, max_age_days, lang, langs, license_spdx,
                                    topic, source, min_gain, major, archived, has_summary, q)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order_sql = SORT_MAP.get(sort or "stars", SORT_MAP["stars"])
    if (order or "desc").lower() == "asc":
        order_sql = order_sql.replace(" DESC", " ASC").replace(" ASC", " ASC")
    order_sql = f"{order_sql}, stars DESC, full_name ASC"

    if top_n:
        sql = f"SELECT * FROM gh_repo {where_sql} ORDER BY {order_sql} LIMIT %s"
        rows = db.query_all(sql, params + [as_int(top_n)])
        return page_result(rows, len(rows), 1, len(rows) or 1)

    total = db.query_scalar(f"SELECT count(*) FROM gh_repo {where_sql}", params) or 0
    p, size = norm_page(page, page_size)
    sql = (f"SELECT * FROM gh_repo {where_sql} ORDER BY {order_sql} "
           f"OFFSET %s LIMIT %s")
    rows = db.query_all(sql, params + [(p - 1) * size, size])
    return page_result(rows, total, p, size)


def batch_get(ids: Optional[Sequence[int]] = None, names: Optional[Sequence[str]] = None) -> list[dict]:
    if ids:
        return db.query_all("SELECT * FROM gh_repo WHERE id = ANY(%s) ORDER BY stars DESC",
                            [list(ids)])
    if names:
        return db.query_all("SELECT * FROM gh_repo WHERE full_name = ANY(%s) ORDER BY stars DESC",
                            [list(names)])
    return []


def get_repo(full_name: str) -> Optional[dict]:
    """单仓详情聚合(主表 + 参与者 + 版本 + 日/周事实 + 关注)。"""
    repo = db.query_one("SELECT * FROM gh_repo WHERE full_name = %s", [full_name])
    if not repo:
        return None
    rid = repo["id"]
    repo["contributors"] = db.query_all(
        "SELECT login, avatar_url, html_url, contributions, rank_no FROM gh_repo_contributor "
        "WHERE repo_id = %s ORDER BY rank_no NULLS LAST, contributions DESC LIMIT 20", [rid])
    repo["releases"] = db.query_all(
        "SELECT tag_name, name, body, published_at, is_prerelease, is_latest FROM gh_repo_release "
        "WHERE repo_id = %s ORDER BY published_at DESC NULLS LAST LIMIT 10", [rid])
    repo["daily"] = db.query_all(
        "SELECT stat_date, stars, delta_stars FROM gh_repo_star_daily "
        "WHERE repo_id = %s ORDER BY stat_date DESC LIMIT 2000", [rid])
    repo["weekly"] = db.query_all(
        "SELECT week_start, week_key, week_label, weekly_new, cumulative FROM gh_repo_star_weekly "
        "WHERE repo_id = %s ORDER BY week_start DESC LIMIT 2000", [rid])
    repo["watch"] = db.query_one(
        "SELECT id, watch_type, threshold, interval_min, enabled FROM gh_watch_target "
        "WHERE repo_id = %s LIMIT 1", [rid])
    repo["gain_7d_fact"] = db.query_scalar(
        "SELECT COALESCE(SUM(delta_stars),0) FROM gh_repo_star_daily "
        "WHERE repo_id = %s AND stat_date >= CURRENT_DATE - 7", [rid])
    return repo


def list_langs(min_stars=None, limit=50) -> list[dict]:
    ms = cfg.DEFAULT_MIN_STARS if min_stars is None else as_int(min_stars)
    return db.query_all(
        "SELECT lang, count(*) AS n, sum(stars)::bigint AS stars FROM gh_repo "
        "WHERE lang IS NOT NULL AND lang <> '' AND stars >= %s "
        "GROUP BY lang ORDER BY n DESC LIMIT %s", [ms, as_int(limit, 50)])


def list_licenses(min_stars=None, limit=50) -> list[dict]:
    ms = cfg.DEFAULT_MIN_STARS if min_stars is None else as_int(min_stars)
    return db.query_all(
        "SELECT license_spdx, count(*) AS n FROM gh_repo "
        "WHERE license_spdx IS NOT NULL AND license_spdx <> '' AND stars >= %s "
        "GROUP BY license_spdx ORDER BY n DESC LIMIT %s", [ms, as_int(limit, 50)])


# ---------------- 断点续采候选 ----------------
def candidates_for_detail(limit=100, ids=None, only_missing=True) -> list[dict]:
    if ids:
        return db.query_all("SELECT id, full_name FROM gh_repo WHERE id = ANY(%s)", [list(ids)])
    cond = "WHERE (detail_synced_at IS NULL OR lang IS NULL OR lang = '')" if only_missing else ""
    return db.query_all(
        f"SELECT id, full_name FROM gh_repo {cond} "
        f"ORDER BY stars DESC NULLS LAST LIMIT %s", [as_int(limit, 100)])


def candidates_for_history(limit=200, ids=None, refresh_after_days=7, only_missing=True) -> list[dict]:
    """star 历史采集候选 —— **只从历史池内取**（pool_service 是唯一口径来源）。

    排序特意打破「马太效应」：`star_gain_7d` 本身由 rollup 从「已有周历史」算出，
    若按它倒序，没历史的仓恒为 0/NULL 永远排末尾 → 每天被采的永远是已采过的头部，
    新仓/长尾仓永远轮不到（实测覆盖率长期卡在 7.8%）。
    故排序键改为「上次采集时间升序优先」（从未采的最先），再按 stars 兜底。

    `ids` 分支是**显式指定**（清库重拉 / 补采名单），不受池限制。
    """
    from . import pool_service
    if ids:
        return db.query_all(
            "SELECT id, full_name FROM gh_repo WHERE id = ANY(%s)", [list(ids)])
    where = [pool_service.pool_where("")]
    params: list[Any] = []
    if only_missing:
        where.append("(history_synced_at IS NULL OR "
                     "history_synced_at < now() - make_interval(days => %s))")
        params.append(as_int(refresh_after_days, 7))
    sql = ("SELECT id, full_name, stars, star_gain_7d, history_synced_at FROM gh_repo "
           f"WHERE {' AND '.join(where)} "
           "ORDER BY history_synced_at ASC NULLS FIRST, stars DESC LIMIT %s")
    return db.query_all(sql, params + [as_int(limit, 200)])


def candidates_for_readme(limit=200, ids=None, refresh_after_days=30) -> list[dict]:
    if ids:
        return db.query_all("SELECT id, full_name, default_branch FROM gh_repo WHERE id = ANY(%s)",
                            [list(ids)])
    return db.query_all(
        "SELECT id, full_name, default_branch FROM gh_repo "
        "WHERE readme_synced_at IS NULL "
        "   OR readme_synced_at < now() - make_interval(days => %s) "
        "ORDER BY stars DESC NULLS LAST LIMIT %s",
        [as_int(refresh_after_days, 30), as_int(limit, 200)])


_HISTORY_TIER_HIGH = 20
_HISTORY_TIER_LOW = 5
_HISTORY_CUTOFF = 1000


def history_gain_threshold(stars: int, gain7: int = 0) -> int:
    """依据仓库热度决定历史采集优先阈值(高热度先采)。"""
    s = as_int(stars)
    g = as_int(gain7)
    if s >= _HISTORY_CUTOFF or g >= _HISTORY_TIER_HIGH:
        return _HISTORY_TIER_HIGH
    if g >= _HISTORY_TIER_LOW:
        return _HISTORY_TIER_LOW
    return 1


def counts() -> list[dict]:
    tables = ["gh_repo", "gh_repo_contributor", "gh_repo_release", "gh_repo_star_daily",
              "gh_repo_star_weekly", "gh_rank_snapshot", "gh_stat_period",
              "gh_rank_whitelist", "gh_app_setting", "gh_llm_task", "gh_llm_run",
              "gh_watch_target", "gh_watch_event", "gh_job_log"]
    out = []
    for t in tables:
        try:
            out.append({"t": t, "n": db.query_scalar(f"SELECT count(*) FROM {t}")})
        except Exception:  # noqa: BLE001
            out.append({"t": t, "n": None})
    return out
