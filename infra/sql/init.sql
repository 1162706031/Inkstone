-- AI 平台 PostgreSQL 初始化脚本 | 结构版本 0010_member_rate_limits
-- 修改记录（结构版本）
-- 0001_identity              用户、个人工作区、会话认证与审计
-- 0002_models                供应商连接、模型配置、Agent 及其版本
-- 0003_execution             对话、消息、运行任务与事件
-- 0004_metering              模型调用、用量、配额与账本
-- 0005_conversation_deletion 会话删除标记及待归档索引
-- 0006_member_deletion       成员删除后释放登录名，保留工作区历史与审计记录
-- 0007_shared_agents         成员可以在自己的工作区使用其他成员创建的智能体快照
-- 0008_agent_releases        智能体可见性与不可变发布版本
-- 0009_access_keys           成员级外部接入 Key、智能体白名单与 API 会话归属
-- 0010_member_rate_limits    成员模型调用频率和并发上限
-- 2026-11-21 合并原 01–03 初始化步骤，保留 0005 表结构和数据库版本记录。
-- 2026-11-21 支持在图形客户端整份执行；本地建角色和数据库的命令移至 ../bootstrap-dev.psql。
--
-- 在 Navicat 或 DBeaver 中，以库所有者连接目标空库（本地示例：ai_dev / ai_platform_dev），
-- 以 UTF-8 打开并运行整个文件。仅首次初始化时执行。

-- 空库初始化。业务表、检查点、版本标记与初始管理员在同一事务内提交。
BEGIN;

DO $$
BEGIN
    IF current_database() IN ('postgres', 'template0', 'template1') THEN
        RAISE EXCEPTION '当前连接的是系统数据库 %，请切换到应用库。', current_database()
            USING HINT = '本地开发请选择 ai_platform_dev，以 ai_dev 连接。若该库尚不存在，请先由管理员创建并指定 ai_dev 为所有者。';
    END IF;
    IF current_user <> (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()) THEN
        RAISE EXCEPTION '请以数据库 % 的所有者执行 init.sql，当前用户为 %。', current_database(), current_user;
    END IF;
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
END
$$;

-- ============ 0001_identity ============

CREATE SCHEMA app;

CREATE TABLE app.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username text UNIQUE NOT NULL CHECK (username ~ '^[a-z0-9][a-z0-9._-]{2,63}$'),
    display_name text NOT NULL,
    password_hash text,
    must_change_password boolean NOT NULL DEFAULT false,
    role text NOT NULL CHECK (role IN ('member','admin')),
    status text NOT NULL CHECK (status IN ('pending','active','disabled')),
    revision integer NOT NULL DEFAULT 1,
    mfa_secret_ciphertext bytea,
    mfa_key_version text,
    mfa_last_counter bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'active' OR password_hash IS NOT NULL),
    CHECK ((mfa_secret_ciphertext IS NULL) = (mfa_key_version IS NULL))
);

CREATE TABLE app.workspaces (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id uuid UNIQUE NOT NULL REFERENCES app.users(id),
    kind text NOT NULL DEFAULT 'personal' CHECK (kind = 'personal'),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(id,owner_user_id)
);

CREATE TABLE app.auth_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES app.users(id),
    token_hash bytea UNIQUE NOT NULL,
    csrf_hash bytea NOT NULL,
    stage text NOT NULL CHECK (stage IN ('anonymous','preauth','full')),
    purpose text CHECK (purpose IN ('initial_password_change','mfa_setup','mfa_verify')),
    expires_at timestamptz NOT NULL,
    idle_expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    verified_until timestamptz,
    pending_mfa_ciphertext bytea,
    pending_mfa_key_version text,
    pending_mfa_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((stage = 'anonymous') = (user_id IS NULL)),
    CHECK ((stage = 'preauth') = (purpose IS NOT NULL))
);

CREATE INDEX auth_sessions_user ON app.auth_sessions(user_id, revoked_at);

CREATE INDEX auth_sessions_expiry ON app.auth_sessions(expires_at);

CREATE TABLE app.verification_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES app.users(id),
    purpose text NOT NULL CHECK (purpose IN ('activation','password_reset')),
    token_hash bytea UNIQUE NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX verification_tokens_unconsumed
ON app.verification_tokens(user_id,purpose) WHERE consumed_at IS NULL;

CREATE TABLE app.mfa_recovery_codes (
    user_id uuid NOT NULL REFERENCES app.users(id),
    code_hash bytea NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id,code_hash)
);

CREATE TABLE app.audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id uuid REFERENCES app.users(id),
    workspace_id uuid REFERENCES app.workspaces(id),
    action text NOT NULL,
    target_type text NOT NULL,
    target_id uuid,
    result text NOT NULL,
    request_id text NOT NULL,
    safe_details jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_workspace ON app.audit_events(workspace_id,created_at);

CREATE INDEX audit_target ON app.audit_events(target_type,target_id,created_at);

-- ============ 0002_models ============

CREATE TABLE app.credential_secrets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), ciphertext bytea NOT NULL,
    nonce bytea NOT NULL, key_version text NOT NULL, version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.model_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL, base_url text NOT NULL,
    protocol text NOT NULL CHECK (protocol = 'chat_completions'),
    credential_id uuid UNIQUE NOT NULL REFERENCES app.credential_secrets(id),
    credential_version integer NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX model_connection_status ON app.model_connections(status);
CREATE TABLE app.model_definitions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id uuid NOT NULL REFERENCES app.model_connections(id),
    upstream_model text NOT NULL, display_name text NOT NULL,
    context_limit_tokens bigint NOT NULL CHECK (context_limit_tokens > 0),
    max_output_tokens bigint NOT NULL CHECK (max_output_tokens > 0 AND max_output_tokens < context_limit_tokens),
    supports_temperature boolean NOT NULL,
    usage_support text NOT NULL CHECK (usage_support IN ('unknown','supported','unsupported')),
    capability_status text NOT NULL DEFAULT 'unverified' CHECK (capability_status IN ('unverified','verified')),
    strict_budget_eligible boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    revision integer NOT NULL DEFAULT 1, request_config_generation bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(connection_id,upstream_model), UNIQUE(connection_id,id)
);
CREATE TABLE app.model_config_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id uuid NOT NULL,
    model_definition_id uuid NOT NULL,
    connection_revision integer NOT NULL, model_revision integer NOT NULL, snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(connection_id,model_definition_id) REFERENCES app.model_definitions(connection_id,id),
    UNIQUE(connection_id,model_definition_id,connection_revision,model_revision)
);
CREATE TABLE app.agents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES app.workspaces(id),
    name text NOT NULL, description text NOT NULL, system_prompt text NOT NULL,
    model_definition_id uuid NOT NULL REFERENCES app.model_definitions(id),
    temperature double precision, max_output_tokens integer NOT NULL, context_budget_tokens integer NOT NULL,
    context_compression_threshold_percent integer NOT NULL, history_max_messages integer NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')), revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(workspace_id,id)
);
CREATE INDEX agents_workspace ON app.agents(workspace_id,status,updated_at);
CREATE TABLE app.agent_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, agent_id uuid NOT NULL,
    revision integer NOT NULL, model_config_version_id uuid NOT NULL REFERENCES app.model_config_versions(id),
    config jsonb NOT NULL, config_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(workspace_id,agent_id) REFERENCES app.agents(workspace_id,id),
    UNIQUE(workspace_id,id), UNIQUE(workspace_id,agent_id,id),
    UNIQUE(agent_id,revision,model_config_version_id)
);
CREATE FUNCTION app.reject_snapshot_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Snapshots are immutable'; END
$$;
CREATE TRIGGER immutable_model_config BEFORE UPDATE ON app.model_config_versions
    FOR EACH ROW EXECUTE FUNCTION app.reject_snapshot_update();
CREATE TRIGGER immutable_agent_version BEFORE UPDATE ON app.agent_versions
    FOR EACH ROW EXECUTE FUNCTION app.reject_snapshot_update();

-- ============ 0003_execution ============

CREATE TABLE app.conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL,
    owner_user_id uuid NOT NULL REFERENCES app.users(id), agent_id uuid NOT NULL, agent_version_id uuid NOT NULL,
    title text NOT NULL, status text NOT NULL CHECK (status IN ('active','deleting')),
    next_message_seq bigint NOT NULL DEFAULT 1, thread_id uuid UNIQUE NOT NULL,
    deletion_requested_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(workspace_id,id),
    FOREIGN KEY(workspace_id,owner_user_id) REFERENCES app.workspaces(id,owner_user_id),
    FOREIGN KEY(workspace_id,agent_id,agent_version_id) REFERENCES app.agent_versions(workspace_id,agent_id,id)
);
CREATE INDEX conversations_workspace ON app.conversations(workspace_id,created_at,id);
CREATE TABLE app.runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES app.workspaces(id),
    actor_user_id uuid NOT NULL REFERENCES app.users(id), conversation_id uuid, agent_version_id uuid,
    kind text NOT NULL CHECK (kind IN ('chat','connection_test')),
    status text NOT NULL CHECK (status IN ('queued','running','cancel_requested','completed','failed','cancelled','interrupted')),
    context_compression text, input_message_id uuid, output_message_id uuid, checkpoint_id text, thread_id uuid,
    fencing_token bigint NOT NULL DEFAULT 0, last_event_seq bigint NOT NULL DEFAULT 0, error_code text,
    started_at timestamptz, finished_at timestamptz, payload_purged_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(workspace_id,id), UNIQUE(workspace_id,conversation_id,id),
    FOREIGN KEY(workspace_id,actor_user_id) REFERENCES app.workspaces(id,owner_user_id),
    FOREIGN KEY(workspace_id,conversation_id) REFERENCES app.conversations(workspace_id,id),
    FOREIGN KEY(workspace_id,agent_version_id) REFERENCES app.agent_versions(workspace_id,id),
    CHECK (kind <> 'chat' OR payload_purged_at IS NOT NULL OR
        (conversation_id IS NOT NULL AND agent_version_id IS NOT NULL)),
    CHECK (payload_purged_at IS NULL OR (
        status IN ('completed','failed','cancelled','interrupted') AND conversation_id IS NULL
        AND input_message_id IS NULL AND output_message_id IS NULL AND checkpoint_id IS NULL AND thread_id IS NULL
    ))
);
CREATE UNIQUE INDEX runs_active_conversation ON app.runs(conversation_id)
    WHERE status IN ('queued','running','cancel_requested');
CREATE TABLE app.messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, conversation_id uuid NOT NULL,
    run_id uuid NOT NULL, sequence bigint NOT NULL, role text NOT NULL CHECK (role IN ('user','assistant')),
    content jsonb NOT NULL, status text NOT NULL, projected_event_seq bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(conversation_id,sequence), UNIQUE(run_id,role), UNIQUE(workspace_id,conversation_id,run_id,id),
    FOREIGN KEY(workspace_id,conversation_id,run_id) REFERENCES app.runs(workspace_id,conversation_id,id)
        DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE app.runs ADD FOREIGN KEY(workspace_id,conversation_id,id,input_message_id)
    REFERENCES app.messages(workspace_id,conversation_id,run_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE app.runs ADD FOREIGN KEY(workspace_id,conversation_id,id,output_message_id)
    REFERENCES app.messages(workspace_id,conversation_id,run_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE app.conversation_context_summaries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, conversation_id uuid NOT NULL,
    run_id uuid UNIQUE NOT NULL, covered_through_seq bigint NOT NULL, previous_summary_id uuid,
    summary_text text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(workspace_id,conversation_id,id),
    FOREIGN KEY(workspace_id,conversation_id,run_id) REFERENCES app.runs(workspace_id,conversation_id,id),
    FOREIGN KEY(workspace_id,conversation_id,previous_summary_id)
        REFERENCES app.conversation_context_summaries(workspace_id,conversation_id,id)
);
CREATE INDEX summaries_watermark ON app.conversation_context_summaries(workspace_id,conversation_id,covered_through_seq);
CREATE TABLE app.execution_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), run_id uuid UNIQUE NOT NULL REFERENCES app.runs(id),
    status text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','leased','done')),
    lease_owner text, lease_until timestamptz, fencing_token bigint NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_available ON app.execution_jobs(status,available_at,lease_until);
CREATE TABLE app.idempotency_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL,
    actor_user_id uuid NOT NULL REFERENCES app.users(id), operation text NOT NULL, resource_id uuid NOT NULL,
    key_hash bytea NOT NULL, request_hash bytea NOT NULL, run_id uuid NOT NULL, expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(workspace_id,actor_user_id,operation,resource_id,key_hash),
    FOREIGN KEY(workspace_id,actor_user_id) REFERENCES app.workspaces(id,owner_user_id),
    FOREIGN KEY(workspace_id,run_id) REFERENCES app.runs(workspace_id,id)
);
CREATE INDEX idempotency_expiry ON app.idempotency_records(expires_at);
CREATE TABLE app.run_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), root_run_id uuid NOT NULL REFERENCES app.runs(id),
    run_id uuid NOT NULL REFERENCES app.runs(id), sequence bigint NOT NULL, schema_version int NOT NULL DEFAULT 1,
    type text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(root_run_id,sequence), CHECK(root_run_id=run_id)
);
CREATE INDEX run_events_time ON app.run_events(created_at);
CREATE TABLE app.model_test_requests (
    run_id uuid PRIMARY KEY REFERENCES app.runs(id), model_definition_id uuid NOT NULL REFERENCES app.model_definitions(id),
    request_config_generation bigint NOT NULL, credential_version int NOT NULL,
    accepted_order bigint GENERATED ALWAYS AS IDENTITY UNIQUE
);
CREATE TABLE app.model_connection_test_results (
    run_id uuid PRIMARY KEY REFERENCES app.model_test_requests(run_id),
    model_definition_id uuid NOT NULL REFERENCES app.model_definitions(id),
    request_config_generation bigint NOT NULL, credential_version int NOT NULL, accepted_order bigint NOT NULL,
    status text NOT NULL CHECK (status IN ('passed','failed')), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX test_results_current ON app.model_connection_test_results
    (model_definition_id,request_config_generation,credential_version,accepted_order DESC);

-- ============ 0004_metering ============

CREATE TABLE app.model_calls (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, run_id uuid NOT NULL,
    model_definition_id uuid NOT NULL REFERENCES app.model_definitions(id), logical_call_key text NOT NULL,
    purpose text NOT NULL CHECK (purpose IN ('chat','connection_test','context_summary')),
    status text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(workspace_id,id), UNIQUE(run_id,logical_call_key),
    FOREIGN KEY(workspace_id,run_id) REFERENCES app.runs(workspace_id,id)
);
CREATE TABLE app.model_call_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), model_call_id uuid NOT NULL, workspace_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    state text NOT NULL CHECK (state IN ('reserved','dispatching','completed','failed','interrupted')),
    credential_version int NOT NULL, dispatched_at timestamptz, deadline_at timestamptz NOT NULL,
    provider_request_id text, result_message_id uuid, recoverable_result jsonb, result_expires_at timestamptz,
    error_code text, finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(workspace_id,id), UNIQUE(model_call_id,attempt_number),
    FOREIGN KEY(workspace_id,model_call_id) REFERENCES app.model_calls(workspace_id,id)
);
CREATE INDEX attempts_deadline ON app.model_call_attempts(state,deadline_at);
CREATE TABLE app.usage_records (
    attempt_id uuid PRIMARY KEY, workspace_id uuid NOT NULL, input_tokens bigint, output_tokens bigint,
    raw_usage jsonb, normalized_usage jsonb, estimated_cost numeric(20,8), currency text, price_snapshot jsonb,
    source text NOT NULL CHECK (source IN ('reported','unknown')),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(workspace_id,attempt_id) REFERENCES app.model_call_attempts(workspace_id,id),
    CHECK ((source='unknown' AND input_tokens IS NULL AND output_tokens IS NULL) OR
           (source='reported' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL
            AND input_tokens >= 0 AND output_tokens >= 0)),
    CHECK ((estimated_cost IS NULL) = (currency IS NULL))
);
CREATE TABLE app.quota_policies (
    workspace_id uuid PRIMARY KEY REFERENCES app.workspaces(id),
    mode text NOT NULL DEFAULT 'soft' CHECK (mode IN ('soft','strict')),
    monthly_token_limit bigint NOT NULL DEFAULT 1000000 CHECK (monthly_token_limit >= 0),
    revision int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.quota_buckets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES app.workspaces(id),
    period_start timestamptz NOT NULL, period_end timestamptz NOT NULL, limit_tokens bigint NOT NULL CHECK(limit_tokens >= 0),
    settled_tokens bigint NOT NULL DEFAULT 0 CHECK (settled_tokens >= 0),
    reserved_tokens bigint NOT NULL DEFAULT 0 CHECK (reserved_tokens >= 0),
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(workspace_id,period_start), UNIQUE(workspace_id,id)
);
CREATE TABLE app.quota_reservations (
    attempt_id uuid PRIMARY KEY, workspace_id uuid NOT NULL, bucket_id uuid NOT NULL,
    reserved_tokens bigint NOT NULL CHECK (reserved_tokens > 0),
    state text NOT NULL CHECK (state IN ('reserved','settled','uncertain','released')),
    expires_at timestamptz NOT NULL, settled_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(workspace_id,attempt_id) REFERENCES app.model_call_attempts(workspace_id,id),
    FOREIGN KEY(workspace_id,bucket_id) REFERENCES app.quota_buckets(workspace_id,id)
);
CREATE INDEX reservations_state ON app.quota_reservations(state,expires_at);
CREATE TABLE app.quota_ledger (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, bucket_id uuid NOT NULL,
    attempt_id uuid, operation text NOT NULL, amount bigint NOT NULL, settlement_key text UNIQUE NOT NULL,
    reason text NOT NULL, actor_user_id uuid REFERENCES app.users(id), created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(workspace_id,bucket_id) REFERENCES app.quota_buckets(workspace_id,id),
    FOREIGN KEY(workspace_id,attempt_id) REFERENCES app.model_call_attempts(workspace_id,id)
);
CREATE INDEX ledger_workspace ON app.quota_ledger(workspace_id,created_at,id);

-- ============ 0005_conversation_deletion ============

CREATE TABLE app.conversation_deletion_markers (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES app.workspaces(id), conversation_id uuid NOT NULL,
    deleted_at timestamptz NOT NULL DEFAULT now(), archived_at timestamptz,
    UNIQUE(workspace_id,conversation_id)
);
CREATE INDEX pending_deletions ON app.conversation_deletion_markers(deleted_at) WHERE archived_at IS NULL;

-- ============ 0006_member_deletion ============

ALTER TABLE app.users DROP CONSTRAINT users_status_check,
    ADD CONSTRAINT users_status_check CHECK (status IN ('pending','active','disabled','deleted')),
    DROP CONSTRAINT users_username_key;
CREATE UNIQUE INDEX users_current_username ON app.users(username) WHERE status <> 'deleted';

-- ============ 0007_shared_agents ============

ALTER TABLE app.agent_versions ADD UNIQUE (agent_id,id);
ALTER TABLE app.conversations
    DROP CONSTRAINT conversations_workspace_id_agent_id_agent_version_id_fkey,
    ADD FOREIGN KEY (agent_id,agent_version_id) REFERENCES app.agent_versions(agent_id,id),
    ADD UNIQUE (workspace_id,id,agent_version_id);
ALTER TABLE app.runs
    DROP CONSTRAINT runs_workspace_id_agent_version_id_fkey,
    ADD FOREIGN KEY (agent_version_id) REFERENCES app.agent_versions(id),
    ADD FOREIGN KEY (workspace_id,conversation_id,agent_version_id)
        REFERENCES app.conversations(workspace_id,id,agent_version_id);

-- ============ 0008_agent_releases ============

ALTER TABLE app.agents ADD COLUMN visibility text NOT NULL DEFAULT 'private'
    CHECK (visibility IN ('private','public'));

CREATE TABLE app.agent_releases (
    agent_id uuid NOT NULL,
    number integer NOT NULL CHECK (number > 0),
    version_id uuid NOT NULL UNIQUE,
    PRIMARY KEY (agent_id,number),
    FOREIGN KEY (agent_id,version_id) REFERENCES app.agent_versions(agent_id,id)
);

-- ============ 0009_access_keys ============

-- Key 绑定创建者与显式智能体白名单，用量归创建者的个人工作区。
CREATE TABLE app.access_keys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL,
    creator_user_id uuid NOT NULL REFERENCES app.users(id),
    name text NOT NULL,
    key_prefix text NOT NULL,
    key_hash bytea NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('active','revoked')),
    expires_at timestamptz,
    last_used_at timestamptz,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(workspace_id,id),
    FOREIGN KEY(workspace_id,creator_user_id) REFERENCES app.workspaces(id,owner_user_id)
);
CREATE INDEX access_keys_owner ON app.access_keys(workspace_id,created_at DESC,id);

-- 白名单是创建时的显式快照，不随创建者可见范围变化而扩大。
CREATE TABLE app.access_key_agents (
    access_key_id uuid NOT NULL REFERENCES app.access_keys(id) ON DELETE CASCADE,
    agent_id uuid NOT NULL REFERENCES app.agents(id),
    PRIMARY KEY (access_key_id,agent_id)
);

-- access_key_id 为 NULL 表示会话来自工作台，非 NULL 表示来自外部 API。
ALTER TABLE app.conversations
    ADD COLUMN access_key_id uuid REFERENCES app.access_keys(id),
    ADD COLUMN external_user_id text,
    ADD CONSTRAINT conversations_external_pair
        CHECK ((access_key_id IS NULL) = (external_user_id IS NULL)),
    ADD CONSTRAINT conversations_external_user_length
        CHECK (external_user_id IS NULL OR char_length(external_user_id) BETWEEN 1 AND 200);
CREATE INDEX conversations_external
    ON app.conversations(access_key_id,external_user_id,created_at DESC,id)
    WHERE access_key_id IS NOT NULL;

ALTER TABLE app.runs ADD COLUMN access_key_id uuid REFERENCES app.access_keys(id);

-- ============ 0010_member_rate_limits ============

ALTER TABLE app.quota_policies
    ADD COLUMN requests_per_minute integer CHECK (requests_per_minute > 0),
    ADD COLUMN max_concurrent integer CHECK (max_concurrent > 0);

-- ============ Alembic 版本记录 ============

CREATE TABLE public.alembic_version (
    version_num varchar(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
INSERT INTO public.alembic_version (version_num) VALUES ('0010_member_rate_limits');

-- ============ 图检查点（ai-platform init-checkpoints） ============

CREATE SCHEMA graph;
SET LOCAL search_path TO graph;

CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);

CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);

CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);

ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';

INSERT INTO graph.checkpoint_migrations (v) SELECT generate_series(0, 9);

CREATE TABLE IF NOT EXISTS graph.write_receipts (
    thread_id uuid NOT NULL, checkpoint_id text NOT NULL, run_id uuid NOT NULL,
    fencing_token bigint NOT NULL, task_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(thread_id,checkpoint_id,run_id,fencing_token,task_id)
);

-- ============ 默认管理员 admin / admin（等同 ai-platform bootstrap-admin） ============

-- 首次登录必须修改密码并绑定 TOTP。下面是密码 admin 的 argon2id 哈希。
WITH new_admin AS (
    INSERT INTO app.users (username, display_name, password_hash, must_change_password, role, status)
    SELECT 'admin', 'Administrator',
           '$argon2id$v=19$m=65536,t=3,p=4$KCxU9iznYc+CNdjaFWoZZA$j/s/OtsMCmedm2NRugKu/JJ+qXfW/xW+7ZQmjl0698A',
           true, 'admin', 'active'
    WHERE NOT EXISTS (SELECT 1 FROM app.users WHERE role = 'admin' OR username = 'admin')
    RETURNING id
), new_workspace AS (
    INSERT INTO app.workspaces (owner_user_id)
    SELECT id FROM new_admin
    RETURNING id
)
INSERT INTO app.audit_events (actor_user_id, workspace_id, action, target_type, target_id, result, request_id)
SELECT NULL, NULL, 'admin.bootstrap', 'user', id, 'success', 'sql'
FROM new_admin;

COMMIT;
