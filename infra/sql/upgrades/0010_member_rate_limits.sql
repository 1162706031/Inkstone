BEGIN;
DO $$
DECLARE
    current_version text;
BEGIN
    LOCK TABLE public.alembic_version IN SHARE ROW EXCLUSIVE MODE;
    SELECT string_agg(version_num, ',') INTO current_version FROM public.alembic_version;
    IF current_version = '0010_member_rate_limits' THEN RETURN; END IF;
    IF current_version IS DISTINCT FROM '0009_access_keys' THEN
        RAISE EXCEPTION 'Expected 0009_access_keys, found %', current_version;
    END IF;

    ALTER TABLE app.quota_policies
        ADD COLUMN requests_per_minute integer CHECK (requests_per_minute > 0),
        ADD COLUMN max_concurrent integer CHECK (max_concurrent > 0);
    UPDATE public.alembic_version SET version_num='0010_member_rate_limits';
END
$$;
COMMIT;
