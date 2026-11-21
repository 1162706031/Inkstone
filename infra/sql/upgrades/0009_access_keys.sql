-- 成员级外部接入 Key。Key 绑定创建者与显式智能体白名单，用量归创建者的个人工作区。
-- 既有会话的 access_key_id 为 NULL，表示来自工作台。
BEGIN;
DO $$
DECLARE
    current_version text;
BEGIN
    LOCK TABLE public.alembic_version IN SHARE ROW EXCLUSIVE MODE;
    SELECT string_agg(version_num, ',') INTO current_version FROM public.alembic_version;
    IF current_version = '0009_access_keys' THEN RETURN; END IF;
    IF current_version IS DISTINCT FROM '0008_agent_releases' THEN
        RAISE EXCEPTION 'Expected 0008_agent_releases, found %', current_version;
    END IF;

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

    UPDATE public.alembic_version SET version_num='0009_access_keys';
END
$$;
COMMIT;
