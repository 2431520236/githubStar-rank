-- 导入后自检（只读）
\echo '== 表数 =='
SELECT count(*) AS tables FROM information_schema.tables
 WHERE table_schema='public' AND table_type='BASE TABLE';

\echo '== 各表行数 =='
SELECT 'gh_repo' t, count(*) FROM gh_repo
UNION ALL SELECT 'gh_repo_star_weekly',  count(*) FROM gh_repo_star_weekly
UNION ALL SELECT 'gh_repo_star_daily',   count(*) FROM gh_repo_star_daily
UNION ALL SELECT 'gh_repo_contributor',  count(*) FROM gh_repo_contributor
UNION ALL SELECT 'gh_repo_release',      count(*) FROM gh_repo_release
UNION ALL SELECT 'gh_rank_snapshot',     count(*) FROM gh_rank_snapshot
UNION ALL SELECT 'gh_stat_period',       count(*) FROM gh_stat_period
UNION ALL SELECT 'gh_app_setting',       count(*) FROM gh_app_setting
UNION ALL SELECT 'gh_llm_task',          count(*) FROM gh_llm_task
UNION ALL SELECT 'gh_rank_whitelist',    count(*) FROM gh_rank_whitelist
ORDER BY t;

\echo '== 关键列 =='
SELECT count(*) total,
       count(*) FILTER (WHERE github_id IS NOT NULL) github_id_ok,
       count(*) FILTER (WHERE COALESCE(ai_summary,'') <> '') ai_ok,
       count(*) FILTER (WHERE COALESCE(in_history_pool,FALSE)) in_pool
  FROM gh_repo;
