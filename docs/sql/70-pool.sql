-- 历史池名次（派生数据，导入后执行；幂等）
-- 口径：周/日历史与日周月榜 gain 只统计 star 排名前 2000 的仓库；总榜不受限。
UPDATE gh_repo SET in_history_pool = FALSE, pool_rank = NULL
 WHERE COALESCE(in_history_pool, FALSE) = TRUE OR pool_rank IS NOT NULL;

WITH ranked AS (
    SELECT id, row_number() OVER (ORDER BY stars DESC NULLS LAST, id) AS rn
      FROM gh_repo WHERE stars IS NOT NULL
)
UPDATE gh_repo r SET in_history_pool = TRUE, pool_rank = k.rn
  FROM ranked k WHERE r.id = k.id AND k.rn <= 2000;

-- 序列复位：COPY 显式写了 id，序列不会自动前进
SELECT setval(pg_get_serial_sequence('gh_llm_task','id'),       COALESCE((SELECT MAX(id) FROM gh_llm_task),1));
SELECT setval(pg_get_serial_sequence('gh_llm_provider','id'),   COALESCE((SELECT MAX(id) FROM gh_llm_provider),1));
SELECT setval(pg_get_serial_sequence('gh_llm_model','id'),      COALESCE((SELECT MAX(id) FROM gh_llm_model),1));
SELECT setval(pg_get_serial_sequence('gh_rank_whitelist','id'), COALESCE((SELECT MAX(id) FROM gh_rank_whitelist),1));
SELECT setval(pg_get_serial_sequence('gh_watch_target','id'),   COALESCE((SELECT MAX(id) FROM gh_watch_target),1));
SELECT setval(pg_get_serial_sequence('gh_repo','id'),           COALESCE((SELECT MAX(id) FROM gh_repo),1));
