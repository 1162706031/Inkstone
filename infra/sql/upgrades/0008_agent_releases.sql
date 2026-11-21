-- 历史对话保留原快照。旧智能体由创建者或管理员确认可见性后首次发布。
BEGIN;
DO $$
DECLARE
    current_version text;
BEGIN
    LOCK TABLE public.alembic_version IN SHARE ROW EXCLUSIVE MODE;
    SELECT string_agg(version_num, ',') INTO current_version FROM public.alembic_version;
    IF current_version = '0008_agent_releases' THEN RETURN; END IF;
    IF current_version IS DISTINCT FROM '0007_shared_agents' THEN
        RAISE EXCEPTION 'Expected 0007_shared_agents, found %', current_version;
    END IF;

    ALTER TABLE app.agents ADD COLUMN visibility text NOT NULL DEFAULT 'private'
        CHECK (visibility IN ('private','public'));

    CREATE TABLE app.agent_releases (
        agent_id uuid NOT NULL,
        number integer NOT NULL CHECK (number > 0),
        version_id uuid NOT NULL UNIQUE,
        PRIMARY KEY (agent_id,number),
        FOREIGN KEY (agent_id,version_id) REFERENCES app.agent_versions(agent_id,id)
    );
    UPDATE public.alembic_version SET version_num='0008_agent_releases';
END
$$;
COMMIT;
