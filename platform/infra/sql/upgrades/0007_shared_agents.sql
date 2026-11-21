-- 以库所有者连接应用库后执行。已有智能体、会话和快照原地保留。
BEGIN;
DO $$
DECLARE
    current_version text;
BEGIN
    LOCK TABLE public.alembic_version IN SHARE ROW EXCLUSIVE MODE;
    SELECT string_agg(version_num, ', ' ORDER BY version_num) INTO current_version FROM public.alembic_version;
    IF current_version = '0007_shared_agents' THEN
        RAISE NOTICE '数据库 % 已是 0007_shared_agents，跳过升级。', current_database();
        RETURN;
    END IF;
    IF current_version IS DISTINCT FROM '0006_member_deletion' THEN
        RAISE EXCEPTION '当前版本为 %，本脚本适用于从 0006_member_deletion 升级到 0007_shared_agents。', coalesce(current_version, '（无版本记录）');
    END IF;

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
    UPDATE public.alembic_version SET version_num='0007_shared_agents';
END
$$;
COMMIT;
