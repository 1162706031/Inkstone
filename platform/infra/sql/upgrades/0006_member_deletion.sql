-- 以库所有者连接应用库后执行整份文件。0005 会升级，已是 0006 时直接结束。
BEGIN;
DO $$
DECLARE
    current_version text;
BEGIN
    LOCK TABLE public.alembic_version IN SHARE ROW EXCLUSIVE MODE;
    SELECT string_agg(version_num, ', ' ORDER BY version_num) INTO current_version
    FROM public.alembic_version;

    IF current_version = '0006_member_deletion' THEN
        RAISE NOTICE '数据库 % 已是 0006_member_deletion，跳过升级。', current_database();
        RETURN;
    END IF;
    IF current_version IS DISTINCT FROM '0005_conversation_deletion' THEN
        RAISE EXCEPTION '数据库 % 当前版本为 %，本脚本适用于从 0005_conversation_deletion 升级到 0006_member_deletion。',
            current_database(), coalesce(current_version, '（无版本记录）');
    END IF;

    ALTER TABLE app.users DROP CONSTRAINT users_status_check,
        ADD CONSTRAINT users_status_check CHECK (status IN ('pending','active','disabled','deleted')),
        DROP CONSTRAINT users_username_key;
    CREATE UNIQUE INDEX users_current_username ON app.users(username) WHERE status <> 'deleted';
    UPDATE public.alembic_version SET version_num='0006_member_deletion';
END
$$;
COMMIT;
