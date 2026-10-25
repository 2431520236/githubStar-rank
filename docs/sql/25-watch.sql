-- 25-watch：配置 / 名单 / 任务等小表（集中在一个文件）
-- COPY 格式，导入快且不受引号/转义影响

-- ===== gh_watch_target (1 行) =====
COPY gh_watch_target ("id", "repo_id", "full_name", "watch_type", "threshold", "interval_min", "channels", "webhook_url", "note", "enabled", "last_checked_at", "last_event_at", "created_at", "updated_at") FROM stdin;
20	273	freeCodeCamp/freeCodeCamp	any	50	60	["site"]	\N		t	2026-09-30 06:39:38.927027+00	2026-09-30 06:39:38.927027+00	2026-09-29 01:42:45.421968+00	2026-09-29 01:42:45.421968+00
\.

