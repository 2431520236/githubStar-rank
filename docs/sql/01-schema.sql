--
-- PostgreSQL database dump
--


-- Dumped from database version 15.19 (Debian 15.19-1.pgdg13+2)
-- Dumped by pg_dump version 15.19 (Debian 15.19-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: gh_app_setting; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_app_setting (
    key text NOT NULL,
    value jsonb NOT NULL,
    description text,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_job_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_job_log (
    id bigint NOT NULL,
    job_name text NOT NULL,
    trigger_type text DEFAULT 'manual'::text,
    status text DEFAULT 'running'::text,
    total integer DEFAULT 0,
    succeeded integer DEFAULT 0,
    failed integer DEFAULT 0,
    threads integer DEFAULT 1,
    duration_ms integer DEFAULT 0,
    message text,
    stats jsonb DEFAULT '{}'::jsonb,
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone
);


--
-- Name: gh_job_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_job_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_job_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_job_log_id_seq OWNED BY public.gh_job_log.id;


--
-- Name: gh_llm_model; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_llm_model (
    id bigint NOT NULL,
    provider_id bigint NOT NULL,
    model_name text NOT NULL,
    display_name text,
    model_type text DEFAULT 'chat'::text,
    max_tokens integer DEFAULT 4096,
    temperature numeric(3,2) DEFAULT 0.30,
    enabled boolean DEFAULT true,
    is_default boolean DEFAULT false,
    extra jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_gh_llm_model_type CHECK ((model_type = ANY (ARRAY['chat'::text, 'response'::text, 'embedding'::text])))
);


--
-- Name: gh_llm_model_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_llm_model_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_llm_model_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_llm_model_id_seq OWNED BY public.gh_llm_model.id;


--
-- Name: gh_llm_provider; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_llm_provider (
    id bigint NOT NULL,
    name text NOT NULL,
    provider_type text DEFAULT 'openai_response'::text,
    base_url text NOT NULL,
    api_key text,
    api_prefix text DEFAULT ''::text,
    headers jsonb DEFAULT '{}'::jsonb,
    proxy text,
    timeout_s integer DEFAULT 120,
    max_retries integer DEFAULT 2,
    enabled boolean DEFAULT true,
    is_default boolean DEFAULT false,
    remark text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_llm_provider_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_llm_provider_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_llm_provider_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_llm_provider_id_seq OWNED BY public.gh_llm_provider.id;


--
-- Name: gh_llm_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_llm_run (
    id bigint NOT NULL,
    task_key text,
    repo_id bigint,
    full_name text,
    provider_name text,
    model_name text,
    status text DEFAULT 'running'::text,
    prompt text,
    output text,
    prompt_tokens integer DEFAULT 0,
    completion_tokens integer DEFAULT 0,
    total_tokens integer DEFAULT 0,
    latency_ms integer DEFAULT 0,
    error text,
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone
);


--
-- Name: gh_llm_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_llm_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_llm_run_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_llm_run_id_seq OWNED BY public.gh_llm_run.id;


--
-- Name: gh_llm_task; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_llm_task (
    id bigint NOT NULL,
    task_key text NOT NULL,
    name text NOT NULL,
    description text,
    task_type text DEFAULT 'summary'::text,
    system_prompt text,
    prompt_template text NOT NULL,
    model_id bigint,
    concurrency integer DEFAULT 3,
    batch_size integer DEFAULT 20,
    max_readme_len integer DEFAULT 6000,
    enabled boolean DEFAULT true,
    last_run_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_llm_task_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_llm_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_llm_task_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_llm_task_id_seq OWNED BY public.gh_llm_task.id;


--
-- Name: gh_rank_snapshot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_rank_snapshot (
    id bigint NOT NULL,
    period_type text NOT NULL,
    period_key text NOT NULL,
    period_label text,
    period_start date,
    period_end date,
    metric text NOT NULL,
    rank_no integer NOT NULL,
    repo_id bigint,
    full_name text NOT NULL,
    stars integer DEFAULT 0,
    gain integer DEFAULT 0,
    lang text,
    license_spdx text,
    summary text,
    html_url text,
    payload jsonb DEFAULT '{}'::jsonb,
    is_final boolean DEFAULT false,
    computed_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_gh_rank_metric CHECK ((metric = ANY (ARRAY['total'::text, 'gain'::text])))
);


--
-- Name: gh_rank_snapshot_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_rank_snapshot_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_rank_snapshot_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_rank_snapshot_id_seq OWNED BY public.gh_rank_snapshot.id;


--
-- Name: gh_rank_whitelist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_rank_whitelist (
    id bigint NOT NULL,
    full_name text NOT NULL,
    list_type text DEFAULT 'major'::text,
    note text,
    source text DEFAULT 'manual'::text,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_rank_whitelist_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_rank_whitelist_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_rank_whitelist_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_rank_whitelist_id_seq OWNED BY public.gh_rank_whitelist.id;


--
-- Name: gh_repo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_repo (
    id bigint NOT NULL,
    full_name text NOT NULL,
    owner text NOT NULL,
    name text NOT NULL,
    html_url text,
    api_url text,
    readme_url text,
    readme_en_url text,
    readme_ch_url text,
    homepage text,
    description text,
    summary text DEFAULT ''::text,
    ai_summary text DEFAULT ''::text,
    ai_summary_tries integer DEFAULT 0,
    lang text,
    license_spdx text,
    license_name text,
    topics jsonb DEFAULT '[]'::jsonb,
    stars integer DEFAULT 0,
    forks integer DEFAULT 0,
    watchers integer DEFAULT 0,
    open_issues integer DEFAULT 0,
    size_kb bigint DEFAULT 0,
    contributors_count integer DEFAULT 0,
    latest_version text,
    version_note text,
    latest_release_at timestamp with time zone,
    default_branch text,
    is_archived boolean DEFAULT false,
    is_fork boolean DEFAULT false,
    repo_created_at timestamp with time zone,
    repo_pushed_at timestamp with time zone,
    repo_updated_at timestamp with time zone,
    star_gain_7d integer DEFAULT 0,
    star_gain_30d integer DEFAULT 0,
    star_gain_60d integer DEFAULT 0,
    star_gain_1d integer DEFAULT 0,
    daily_velocity numeric(12,2) DEFAULT 0,
    sources jsonb DEFAULT '[]'::jsonb,
    seed_metrics jsonb DEFAULT '{}'::jsonb,
    is_major boolean DEFAULT false,
    detail_synced_at timestamp with time zone,
    history_synced_at timestamp with time zone,
    readme_synced_at timestamp with time zone,
    ai_summary_synced_at timestamp with time zone,
    first_seen_at timestamp with time zone DEFAULT now(),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    github_id bigint,
    in_history_pool boolean DEFAULT false,
    pool_rank integer
);


--
-- Name: gh_repo_contributor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_repo_contributor (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    login text NOT NULL,
    avatar_url text,
    html_url text,
    contributions integer DEFAULT 0,
    rank_no integer,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_repo_contributor_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_repo_contributor_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_repo_contributor_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_repo_contributor_id_seq OWNED BY public.gh_repo_contributor.id;


--
-- Name: gh_repo_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_repo_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_repo_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_repo_id_seq OWNED BY public.gh_repo.id;


--
-- Name: gh_repo_release; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_repo_release (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    tag_name text NOT NULL,
    name text,
    body text,
    published_at timestamp with time zone,
    is_prerelease boolean DEFAULT false,
    is_latest boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_repo_release_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_repo_release_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_repo_release_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_repo_release_id_seq OWNED BY public.gh_repo_release.id;


--
-- Name: gh_repo_star_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_repo_star_daily (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    stat_date date NOT NULL,
    stars integer DEFAULT 0,
    forks integer DEFAULT 0,
    watchers integer DEFAULT 0,
    open_issues integer DEFAULT 0,
    delta_stars integer DEFAULT 0,
    delta_forks integer DEFAULT 0,
    source text DEFAULT 'github_api'::text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_repo_star_daily_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_repo_star_daily_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_repo_star_daily_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_repo_star_daily_id_seq OWNED BY public.gh_repo_star_daily.id;


--
-- Name: gh_repo_star_weekly; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_repo_star_weekly (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    week_start date NOT NULL,
    week_key text,
    week_label text,
    week_index integer,
    weekly_new integer DEFAULT 0,
    cumulative integer DEFAULT 0,
    days jsonb DEFAULT '[]'::jsonb,
    source text DEFAULT 'stargazers_history'::text,
    fetched_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_repo_star_weekly_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_repo_star_weekly_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_repo_star_weekly_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_repo_star_weekly_id_seq OWNED BY public.gh_repo_star_weekly.id;


--
-- Name: gh_stat_period; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_stat_period (
    id bigint NOT NULL,
    period_type text NOT NULL,
    period_key text NOT NULL,
    period_label text,
    period_start date NOT NULL,
    period_end date NOT NULL,
    is_complete boolean DEFAULT false,
    is_locked boolean DEFAULT false,
    repo_count integer DEFAULT 0,
    gain_sum bigint DEFAULT 0,
    computed_at timestamp with time zone,
    note text,
    CONSTRAINT ck_gh_stat_period_type CHECK ((period_type = ANY (ARRAY['day'::text, 'week'::text, 'month'::text])))
);


--
-- Name: gh_stat_period_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_stat_period_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_stat_period_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_stat_period_id_seq OWNED BY public.gh_stat_period.id;


--
-- Name: gh_watch_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_watch_event (
    id bigint NOT NULL,
    watch_id bigint,
    repo_id bigint,
    full_name text NOT NULL,
    event_type text DEFAULT 'star_gain'::text,
    level text DEFAULT 'info'::text,
    title text NOT NULL,
    content text,
    payload jsonb DEFAULT '{}'::jsonb,
    is_read boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_watch_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_watch_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_watch_event_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_watch_event_id_seq OWNED BY public.gh_watch_event.id;


--
-- Name: gh_watch_target; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gh_watch_target (
    id bigint NOT NULL,
    repo_id bigint,
    full_name text NOT NULL,
    watch_type text DEFAULT 'any'::text,
    threshold integer DEFAULT 0,
    interval_min integer DEFAULT 60,
    channels jsonb DEFAULT '[]'::jsonb,
    webhook_url text,
    note text,
    enabled boolean DEFAULT true,
    last_checked_at timestamp with time zone,
    last_event_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: gh_watch_target_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gh_watch_target_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gh_watch_target_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gh_watch_target_id_seq OWNED BY public.gh_watch_target.id;


--
-- Name: gh_job_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_job_log ALTER COLUMN id SET DEFAULT nextval('public.gh_job_log_id_seq'::regclass);


--
-- Name: gh_llm_model id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_model ALTER COLUMN id SET DEFAULT nextval('public.gh_llm_model_id_seq'::regclass);


--
-- Name: gh_llm_provider id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_provider ALTER COLUMN id SET DEFAULT nextval('public.gh_llm_provider_id_seq'::regclass);


--
-- Name: gh_llm_run id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_run ALTER COLUMN id SET DEFAULT nextval('public.gh_llm_run_id_seq'::regclass);


--
-- Name: gh_llm_task id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_task ALTER COLUMN id SET DEFAULT nextval('public.gh_llm_task_id_seq'::regclass);


--
-- Name: gh_rank_snapshot id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_rank_snapshot ALTER COLUMN id SET DEFAULT nextval('public.gh_rank_snapshot_id_seq'::regclass);


--
-- Name: gh_rank_whitelist id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_rank_whitelist ALTER COLUMN id SET DEFAULT nextval('public.gh_rank_whitelist_id_seq'::regclass);


--
-- Name: gh_repo id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo ALTER COLUMN id SET DEFAULT nextval('public.gh_repo_id_seq'::regclass);


--
-- Name: gh_repo_contributor id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_contributor ALTER COLUMN id SET DEFAULT nextval('public.gh_repo_contributor_id_seq'::regclass);


--
-- Name: gh_repo_release id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_release ALTER COLUMN id SET DEFAULT nextval('public.gh_repo_release_id_seq'::regclass);


--
-- Name: gh_repo_star_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_star_daily ALTER COLUMN id SET DEFAULT nextval('public.gh_repo_star_daily_id_seq'::regclass);


--
-- Name: gh_repo_star_weekly id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_star_weekly ALTER COLUMN id SET DEFAULT nextval('public.gh_repo_star_weekly_id_seq'::regclass);


--
-- Name: gh_stat_period id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_stat_period ALTER COLUMN id SET DEFAULT nextval('public.gh_stat_period_id_seq'::regclass);


--
-- Name: gh_watch_event id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_watch_event ALTER COLUMN id SET DEFAULT nextval('public.gh_watch_event_id_seq'::regclass);


--
-- Name: gh_watch_target id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_watch_target ALTER COLUMN id SET DEFAULT nextval('public.gh_watch_target_id_seq'::regclass);


--
-- Name: gh_app_setting gh_app_setting_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_app_setting
    ADD CONSTRAINT gh_app_setting_pkey PRIMARY KEY (key);


--
-- Name: gh_job_log gh_job_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_job_log
    ADD CONSTRAINT gh_job_log_pkey PRIMARY KEY (id);


--
-- Name: gh_llm_model gh_llm_model_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_model
    ADD CONSTRAINT gh_llm_model_pkey PRIMARY KEY (id);


--
-- Name: gh_llm_provider gh_llm_provider_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_provider
    ADD CONSTRAINT gh_llm_provider_name_key UNIQUE (name);


--
-- Name: gh_llm_provider gh_llm_provider_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_provider
    ADD CONSTRAINT gh_llm_provider_pkey PRIMARY KEY (id);


--
-- Name: gh_llm_run gh_llm_run_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_run
    ADD CONSTRAINT gh_llm_run_pkey PRIMARY KEY (id);


--
-- Name: gh_llm_task gh_llm_task_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_task
    ADD CONSTRAINT gh_llm_task_pkey PRIMARY KEY (id);


--
-- Name: gh_llm_task gh_llm_task_task_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_task
    ADD CONSTRAINT gh_llm_task_task_key_key UNIQUE (task_key);


--
-- Name: gh_rank_snapshot gh_rank_snapshot_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_rank_snapshot
    ADD CONSTRAINT gh_rank_snapshot_pkey PRIMARY KEY (id);


--
-- Name: gh_rank_whitelist gh_rank_whitelist_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_rank_whitelist
    ADD CONSTRAINT gh_rank_whitelist_pkey PRIMARY KEY (id);


--
-- Name: gh_repo_contributor gh_repo_contributor_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_contributor
    ADD CONSTRAINT gh_repo_contributor_pkey PRIMARY KEY (id);


--
-- Name: gh_repo gh_repo_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo
    ADD CONSTRAINT gh_repo_pkey PRIMARY KEY (id);


--
-- Name: gh_repo_release gh_repo_release_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_release
    ADD CONSTRAINT gh_repo_release_pkey PRIMARY KEY (id);


--
-- Name: gh_repo_star_daily gh_repo_star_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_star_daily
    ADD CONSTRAINT gh_repo_star_daily_pkey PRIMARY KEY (id);


--
-- Name: gh_repo_star_weekly gh_repo_star_weekly_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_star_weekly
    ADD CONSTRAINT gh_repo_star_weekly_pkey PRIMARY KEY (id);


--
-- Name: gh_stat_period gh_stat_period_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_stat_period
    ADD CONSTRAINT gh_stat_period_pkey PRIMARY KEY (id);


--
-- Name: gh_watch_event gh_watch_event_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_watch_event
    ADD CONSTRAINT gh_watch_event_pkey PRIMARY KEY (id);


--
-- Name: gh_watch_target gh_watch_target_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_watch_target
    ADD CONSTRAINT gh_watch_target_pkey PRIMARY KEY (id);


--
-- Name: gh_repo_contributor uq_gh_contributor; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_contributor
    ADD CONSTRAINT uq_gh_contributor UNIQUE (repo_id, login);


--
-- Name: gh_llm_model uq_gh_llm_model; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_model
    ADD CONSTRAINT uq_gh_llm_model UNIQUE (provider_id, model_name);


--
-- Name: gh_rank_snapshot uq_gh_rank_snapshot; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_rank_snapshot
    ADD CONSTRAINT uq_gh_rank_snapshot UNIQUE (period_type, period_key, metric, full_name);


--
-- Name: gh_repo_release uq_gh_release; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_release
    ADD CONSTRAINT uq_gh_release UNIQUE (repo_id, tag_name);


--
-- Name: gh_repo uq_gh_repo_full_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo
    ADD CONSTRAINT uq_gh_repo_full_name UNIQUE (full_name);


--
-- Name: gh_repo_star_daily uq_gh_star_daily; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_star_daily
    ADD CONSTRAINT uq_gh_star_daily UNIQUE (repo_id, stat_date);


--
-- Name: gh_repo_star_weekly uq_gh_star_weekly; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_star_weekly
    ADD CONSTRAINT uq_gh_star_weekly UNIQUE (repo_id, week_start);


--
-- Name: gh_stat_period uq_gh_stat_period; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_stat_period
    ADD CONSTRAINT uq_gh_stat_period UNIQUE (period_type, period_key);


--
-- Name: gh_watch_target uq_gh_watch_target; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_watch_target
    ADD CONSTRAINT uq_gh_watch_target UNIQUE (full_name);


--
-- Name: gh_rank_whitelist uq_gh_whitelist; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_rank_whitelist
    ADD CONSTRAINT uq_gh_whitelist UNIQUE (list_type, full_name);


--
-- Name: idx_gh_contributor_repo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_contributor_repo ON public.gh_repo_contributor USING btree (repo_id, rank_no);


--
-- Name: idx_gh_job_log; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_job_log ON public.gh_job_log USING btree (job_name, started_at DESC);


--
-- Name: idx_gh_llm_model_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_llm_model_provider ON public.gh_llm_model USING btree (provider_id);


--
-- Name: idx_gh_llm_run_repo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_llm_run_repo ON public.gh_llm_run USING btree (repo_id, started_at DESC);


--
-- Name: idx_gh_llm_run_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_llm_run_task ON public.gh_llm_run USING btree (task_key, started_at DESC);


--
-- Name: idx_gh_rank_snapshot_q; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_rank_snapshot_q ON public.gh_rank_snapshot USING btree (period_type, period_key, metric, rank_no);


--
-- Name: idx_gh_release_repo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_release_repo ON public.gh_repo_release USING btree (repo_id, published_at DESC);


--
-- Name: idx_gh_repo_ai_summary_null; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_ai_summary_null ON public.gh_repo USING btree (id) WHERE ((ai_summary IS NULL) OR (ai_summary = ''::text));


--
-- Name: idx_gh_repo_gain1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_gain1 ON public.gh_repo USING btree (star_gain_1d DESC);


--
-- Name: idx_gh_repo_gain7; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_gain7 ON public.gh_repo USING btree (star_gain_7d DESC);


--
-- Name: idx_gh_repo_history_sync; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_history_sync ON public.gh_repo USING btree (history_synced_at NULLS FIRST);


--
-- Name: idx_gh_repo_in_pool; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_in_pool ON public.gh_repo USING btree (pool_rank) WHERE (COALESCE(in_history_pool, false) = true);


--
-- Name: idx_gh_repo_lang; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_lang ON public.gh_repo USING btree (lang);


--
-- Name: idx_gh_repo_license; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_license ON public.gh_repo USING btree (license_spdx);


--
-- Name: idx_gh_repo_major; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_major ON public.gh_repo USING btree (is_major);


--
-- Name: idx_gh_repo_pushed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_pushed ON public.gh_repo USING btree (repo_pushed_at DESC);


--
-- Name: idx_gh_repo_readme_sync; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_readme_sync ON public.gh_repo USING btree (readme_synced_at);


--
-- Name: idx_gh_repo_search; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_search ON public.gh_repo USING gin (to_tsvector('simple'::regconfig, ((((COALESCE(full_name, ''::text) || ' '::text) || COALESCE(description, ''::text)) || ' '::text) || COALESCE(summary, ''::text))));


--
-- Name: idx_gh_repo_stars; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_stars ON public.gh_repo USING btree (stars DESC);


--
-- Name: idx_gh_repo_summary_null; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_repo_summary_null ON public.gh_repo USING btree (id) WHERE ((summary IS NULL) OR (summary = ''::text));


--
-- Name: idx_gh_star_daily_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_star_daily_date ON public.gh_repo_star_daily USING btree (stat_date DESC);


--
-- Name: idx_gh_star_daily_delta; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_star_daily_delta ON public.gh_repo_star_daily USING btree (stat_date DESC, delta_stars DESC);


--
-- Name: idx_gh_star_daily_repo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_star_daily_repo ON public.gh_repo_star_daily USING btree (repo_id, stat_date DESC);


--
-- Name: idx_gh_star_weekly_repo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_star_weekly_repo ON public.gh_repo_star_weekly USING btree (repo_id, week_start DESC);


--
-- Name: idx_gh_star_weekly_week; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_star_weekly_week ON public.gh_repo_star_weekly USING btree (week_start DESC, weekly_new DESC);


--
-- Name: idx_gh_stat_period; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_stat_period ON public.gh_stat_period USING btree (period_type, period_start DESC);


--
-- Name: idx_gh_watch_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_watch_event ON public.gh_watch_event USING btree (is_read, created_at DESC);


--
-- Name: idx_gh_watch_event_repo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_watch_event_repo ON public.gh_watch_event USING btree (repo_id, created_at DESC);


--
-- Name: idx_gh_whitelist_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gh_whitelist_type ON public.gh_rank_whitelist USING btree (list_type, enabled);


--
-- Name: uq_gh_repo_github_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_gh_repo_github_id ON public.gh_repo USING btree (github_id) WHERE (github_id IS NOT NULL);


--
-- Name: gh_llm_model gh_llm_model_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_model
    ADD CONSTRAINT gh_llm_model_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.gh_llm_provider(id) ON DELETE CASCADE;


--
-- Name: gh_llm_run gh_llm_run_repo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_run
    ADD CONSTRAINT gh_llm_run_repo_id_fkey FOREIGN KEY (repo_id) REFERENCES public.gh_repo(id) ON DELETE SET NULL;


--
-- Name: gh_llm_task gh_llm_task_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_llm_task
    ADD CONSTRAINT gh_llm_task_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.gh_llm_model(id) ON DELETE SET NULL;


--
-- Name: gh_rank_snapshot gh_rank_snapshot_repo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_rank_snapshot
    ADD CONSTRAINT gh_rank_snapshot_repo_id_fkey FOREIGN KEY (repo_id) REFERENCES public.gh_repo(id) ON DELETE SET NULL;


--
-- Name: gh_repo_contributor gh_repo_contributor_repo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_contributor
    ADD CONSTRAINT gh_repo_contributor_repo_id_fkey FOREIGN KEY (repo_id) REFERENCES public.gh_repo(id) ON DELETE CASCADE;


--
-- Name: gh_repo_release gh_repo_release_repo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_release
    ADD CONSTRAINT gh_repo_release_repo_id_fkey FOREIGN KEY (repo_id) REFERENCES public.gh_repo(id) ON DELETE CASCADE;


--
-- Name: gh_repo_star_daily gh_repo_star_daily_repo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_star_daily
    ADD CONSTRAINT gh_repo_star_daily_repo_id_fkey FOREIGN KEY (repo_id) REFERENCES public.gh_repo(id) ON DELETE CASCADE;


--
-- Name: gh_repo_star_weekly gh_repo_star_weekly_repo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_repo_star_weekly
    ADD CONSTRAINT gh_repo_star_weekly_repo_id_fkey FOREIGN KEY (repo_id) REFERENCES public.gh_repo(id) ON DELETE CASCADE;


--
-- Name: gh_watch_event gh_watch_event_repo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_watch_event
    ADD CONSTRAINT gh_watch_event_repo_id_fkey FOREIGN KEY (repo_id) REFERENCES public.gh_repo(id) ON DELETE SET NULL;


--
-- Name: gh_watch_event gh_watch_event_watch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_watch_event
    ADD CONSTRAINT gh_watch_event_watch_id_fkey FOREIGN KEY (watch_id) REFERENCES public.gh_watch_target(id) ON DELETE CASCADE;


--
-- Name: gh_watch_target gh_watch_target_repo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gh_watch_target
    ADD CONSTRAINT gh_watch_target_repo_id_fkey FOREIGN KEY (repo_id) REFERENCES public.gh_repo(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


