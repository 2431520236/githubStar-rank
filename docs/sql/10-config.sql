-- 10-config：配置 / 名单 / 任务等小表（集中在一个文件）
-- COPY 格式，导入快且不受引号/转义影响

-- ===== gh_app_setting (12 行) =====
COPY gh_app_setting ("key", "value", "description", "updated_at") FROM stdin;
collect.api_base	"https://api.github.com"	GitHub API 基址	2026-10-22 05:47:52.445043+00
collect.concurrency	6	GitHub API 采集并发线程数	2026-10-22 05:47:52.445043+00
collect.history_top_n	200	历史 star 采集默认处理的仓库数	2026-10-22 05:47:52.445043+00
collect.token	""	GitHub Token(空=匿名, 限速 60/小时)	2026-10-22 05:47:52.445043+00
filter.default_min_stars	50	默认过滤:star 下限	2026-10-22 05:47:52.445043+00
filter.hide_major	false	默认过滤:是否隐藏熟知名单项目	2026-10-22 05:47:52.445043+00
filter.max_age_days	1095	默认过滤:最后更新时间在 N 天内(3年)	2026-10-22 05:47:52.445043+00
scheduler.daily_snapshot_at	"23:50"	每日快照执行时刻(HH:MM)	2026-10-22 05:47:52.445043+00
scheduler.enabled	true	内置调度开关	2026-10-22 05:47:52.445043+00
scheduler.weekly_rank_at	"01:00"	每周榜单计算时刻(HH:MM)	2026-10-22 05:47:52.445043+00
stat.start_date	"2025-01-01"	图表统计起始日期	2026-10-22 05:47:52.445043+00
\.

-- ===== gh_llm_task (4 行) =====
COPY gh_llm_task ("id", "task_key", "name", "description", "task_type", "system_prompt", "prompt_template", "model_id", "concurrency", "batch_size", "max_readme_len", "enabled", "last_run_at", "created_at", "updated_at") FROM stdin;
1	repo_summary	项目概要生成	扫描仓库描述与 README,产出 120 字以内中文概要,写入 gh_repo.summary	summary	你是开源项目分析助手。只输出中文概要,不要客套话、不要 Markdown 标题。	仓库:{full_name}\n语言:{lang}\n协议:{license}\n官方简介:{description}\nREADME 片段:\n{readme}\n\n请用 120 字以内中文概括:这个项目解决什么问题、核心能力、适用人群。	\N	3	20	6000	t	\N	2026-10-22 05:47:52.448118+00	2026-10-22 05:47:52.448118+00
2	version_note	版本迭代备注	根据最新 release 说明提炼本次迭代要点,写入 gh_repo.version_note	summary	你是版本发布编辑。只输出中文要点,不超过 80 字。	仓库:{full_name}\n最新版本:{latest_version}\n发布说明:\n{release_body}\n\n请用 80 字以内中文说明本次版本迭代的关键变化。	\N	3	20	6000	t	\N	2026-10-22 05:47:52.448118+00	2026-10-22 05:47:52.448118+00
3	trend_digest	趋势速读	对榜单 Top 项目做一句话点评,用于大屏趋势面板	summary	你是技术趋势分析师,输出简洁中文结论。	当前周期:{period_label}\n榜单 Top 项目:\n{repo_list}\n\n请用 200 字以内中文总结这一周期开源趋势(热点方向、共性特征)。	\N	2	10	6000	t	\N	2026-10-22 05:47:52.448118+00	2026-10-22 05:47:52.448118+00
4	repo_ai_summary	项目 AI 概要	阅读单个项目 README(英文先翻译),用流畅中文写一段/几段介绍(是什么/解决什么/主要能力),写 gh_repo.ai_summary;一次性生成,不随 star/内容更新重复提取	summary	你是开源项目分析助手。阅读给定项目材料后,用流畅、简洁、有逻辑的中文写成一段或几段项目介绍,自然讲清:它是什么、有什么用、主要功能。禁止分点罗列、禁止小标题、禁止复述指令、禁止输出思考过程或与本项目无关的内容。	项目:{full_name}\n语言:{lang}\n协议:{license}\nGitHub 官方简介:{description}\n\nREADME 原文(可能为英文,请理解后翻译):\n{readme}\n\n请仅依据以上材料,用流畅的中文写 1~3 段话介绍这个项目,自然涵盖:它是什么、解决什么问题、主要能力;语言要通顺连贯、有逻辑,不要分点、不要小标题、不要额外说明(总字数约 120~250 字)。	\N	4	50	6000	t	\N	2026-10-22 05:47:52.448118+00	2026-10-22 05:47:52.448118+00
\.

-- ===== gh_llm_provider (1 行) =====
COPY gh_llm_provider ("id", "name", "provider_type", "base_url", "api_key", "api_prefix", "headers", "timeout_s", "max_retries", "enabled", "is_default", "remark", "created_at", "updated_at") FROM stdin;
8	llm_gateway	openai_response	http://127.0.0.1:28080	REPLACE_WITH_REAL_KEY	/v1	{}	120	2	t	f	\N	2026-10-22 05:50:31.377048+00	2026-10-22 05:50:31.377048+00
\.

-- ===== gh_llm_model (5 行) =====
COPY gh_llm_model ("id", "provider_id", "model_name", "display_name", "model_type", "max_tokens", "temperature", "enabled", "is_default", "extra", "created_at", "updated_at") FROM stdin;
1	8	deepseek-v4-flash	deepseek-v4-flash	chat	8192	0.30	t	t	{}	2026-10-22 05:50:31.377048+00	2026-10-22 05:50:31.377048+00
2	8	deepseek-flash	deepseek-flash	chat	4096	0.30	t	f	{}	2026-10-22 05:50:31.377048+00	2026-10-22 05:50:31.377048+00
3	8	deepseek-v4-flash-vision-exp	deepseek-v4-flash-vision-exp	chat	4096	0.30	t	f	{}	2026-10-22 05:50:31.377048+00	2026-10-22 05:50:31.377048+00
4	8	deepseek-v4-pro	deepseek-v4-pro	chat	4096	0.30	t	f	{}	2026-10-22 05:50:31.377048+00	2026-10-22 05:50:31.377048+00
5	8	deepseek-v4.1-flash	deepseek-v4.1-flash	chat	4096	0.30	t	f	{}	2026-10-22 05:50:31.377048+00	2026-10-22 05:50:31.377048+00
\.

-- ===== gh_rank_whitelist (25 行) =====
COPY gh_rank_whitelist ("id", "full_name", "list_type", "note", "source", "enabled", "created_at", "updated_at") FROM stdin;
1	torvalds/linux	major	Linux 内核	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
2	facebook/react	major	前端框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
3	vuejs/vue	major	前端框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
4	tensorflow/tensorflow	major	深度学习框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
5	pytorch/pytorch	major	深度学习框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
6	microsoft/vscode	major	编辑器	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
7	golang/go	major	编程语言	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
8	rust-lang/rust	major	编程语言	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
9	nodejs/node	major	运行时	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
10	kubernetes/kubernetes	major	容器编排	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
11	ollama/ollama	major	本地大模型	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
12	langchain-ai/langchain	major	LLM 框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
13	openai/openai-python	major	官方 SDK	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
14	deepseek-ai/DeepSeek-V3	major	大模型	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
15	home-assistant/core	major	智能家居	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
16	electron/electron	major	桌面框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
17	django/django	major	Web 框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
18	spring-projects/spring-boot	major	Java 框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
19	apache/spark	major	大数据	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
20	redis/redis	major	数据库	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
21	vercel/next.js	major	前端框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
22	vitejs/vite	major	构建工具	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
23	denoland/deno	major	运行时	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
24	sveltejs/svelte	major	前端框架	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
25	mui/material-ui	major	UI 库	seed	t	2026-10-22 05:47:52.446375+00	2026-10-22 05:47:52.446375+00
\.

