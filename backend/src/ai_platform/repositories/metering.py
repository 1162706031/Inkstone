"""SQL for quota policies, buckets, reservations, usage records and the ledger."""

from psycopg.types.json import Jsonb

from ai_platform.repositories.common import fetch_page


def ensure_policy(conn, workspace):
    conn.execute("INSERT INTO app.quota_policies(workspace_id) VALUES (%s) ON CONFLICT DO NOTHING", (workspace,))


def get_policy(conn, workspace, *, lock=False):
    return conn.execute(
        "SELECT * FROM app.quota_policies WHERE workspace_id=%s"
        + (" FOR UPDATE" if lock else ""), (workspace,),
    ).fetchone()


def update_policy(conn, workspace, limit, mode, requests_per_minute, max_concurrent):
    return conn.execute("""
        UPDATE app.quota_policies SET monthly_token_limit=%s,mode=%s,requests_per_minute=%s,max_concurrent=%s,
            revision=revision+1,updated_at=now()
        WHERE workspace_id=%s RETURNING *
    """, (limit, mode, requests_per_minute, max_concurrent, workspace)).fetchone()


def ensure_bucket(conn, workspace, start, end, limit):
    conn.execute("""
        INSERT INTO app.quota_buckets(workspace_id,period_start,period_end,limit_tokens)
        VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING
    """, (workspace, start, end, limit))


def reserve_bucket(conn, workspace, start, estimate):
    """Add ``estimate`` to the bucket's reservation when it fits under the limit; returns the bucket id row."""
    return conn.execute("""
        UPDATE app.quota_buckets SET reserved_tokens=reserved_tokens+%s,updated_at=now()
        WHERE workspace_id=%s AND period_start=%s AND settled_tokens+reserved_tokens+%s<=limit_tokens
        RETURNING id
    """, (estimate, workspace, start, estimate)).fetchone()


def set_bucket_limit(conn, workspace, start, limit):
    conn.execute("""
        UPDATE app.quota_buckets SET limit_tokens=%s,updated_at=now() WHERE workspace_id=%s AND period_start=%s
    """, (limit, workspace, start))


def settle_bucket(conn, bucket, amount, reserved):
    conn.execute("""
        UPDATE app.quota_buckets SET settled_tokens=settled_tokens+%s,
            reserved_tokens=reserved_tokens-%s,updated_at=now() WHERE id=%s
    """, (amount, reserved, bucket))


def insert_reservation(conn, attempt, workspace, bucket, estimate, deadline):
    conn.execute("""
        INSERT INTO app.quota_reservations(attempt_id,workspace_id,bucket_id,reserved_tokens,state,expires_at)
        VALUES (%s,%s,%s,%s,'reserved',%s)
    """, (attempt, workspace, bucket, estimate, deadline))


def lock_reservation(conn, attempt):
    return conn.execute("""
        SELECT * FROM app.quota_reservations WHERE attempt_id=%s FOR UPDATE
    """, (attempt,)).fetchone()


def set_reservation_state(conn, attempt, state):
    conn.execute("""
        UPDATE app.quota_reservations SET state=%s,settled_at=now() WHERE attempt_id=%s
    """, (state, attempt))


def insert_ledger(conn, workspace, bucket, attempt, operation, amount, reason):
    conn.execute("""
        INSERT INTO app.quota_ledger(workspace_id,bucket_id,attempt_id,operation,amount,settlement_key,reason)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (workspace, bucket, attempt, operation, amount, f"{attempt}:{operation}", reason))


def insert_usage(conn, attempt, workspace, first, second, normalized, source):
    conn.execute("""
        INSERT INTO app.usage_records(attempt_id,workspace_id,input_tokens,output_tokens,normalized_usage,source)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (attempt, workspace, first, second, normalized, source))


def report_usage(conn, attempt, first, second):
    conn.execute("""
        UPDATE app.usage_records SET input_tokens=%s,output_tokens=%s,source='reported',normalized_usage=%s
        WHERE attempt_id=%s
    """, (first, second, Jsonb({"input_tokens": first, "output_tokens": second}), attempt))


def lock_admission(conn):
    # All dispatchers use this short transaction lock.
    conn.execute("SELECT pg_advisory_xact_lock(1848103263)")


def admission_counts(conn, workspace):
    return conn.execute("""
        SELECT count(*) FILTER (WHERE workspace_id=%s AND dispatched_at>now()-interval '60 seconds') AS recent,
               count(*) FILTER (WHERE workspace_id=%s AND state='dispatching' AND deadline_at>now()) AS local,
               count(*) FILTER (WHERE state='dispatching' AND deadline_at>now()) AS global
        FROM app.model_call_attempts
        WHERE dispatched_at>now()-interval '60 seconds' OR (state='dispatching' AND deadline_at>now())
    """, (workspace, workspace)).fetchone()


def usage_totals(conn, workspace, start):
    return conn.execute("""
        SELECT b.limit_tokens,b.settled_tokens,b.reserved_tokens,
            coalesce(sum(r.reserved_tokens) FILTER (WHERE r.state='uncertain'),0) AS uncertain,
            count(*) FILTER (WHERE u.source='unknown') AS unknown,
            count(*) FILTER (WHERE u.attempt_id IS NOT NULL AND u.estimated_cost IS NULL) AS unpriced
        FROM app.quota_buckets b LEFT JOIN app.quota_reservations r ON r.bucket_id=b.id
        LEFT JOIN app.usage_records u ON u.attempt_id=r.attempt_id
        WHERE b.workspace_id=%s AND b.period_start=%s GROUP BY b.id
    """, (workspace, start)).fetchone()


ATTEMPTS = """
    SELECT a.id,a.model_call_id,c.run_id,c.model_definition_id,c.purpose,a.state,
        u.input_tokens,u.output_tokens,u.estimated_cost::text AS cost,u.currency,
        coalesce(u.source,'unknown') AS usage_source,a.dispatched_at AS started_at,
        a.finished_at,a.error_code,a.created_at
    FROM app.model_call_attempts a JOIN app.model_calls c ON c.id=a.model_call_id
    LEFT JOIN app.usage_records u ON u.attempt_id=a.id
    WHERE a.workspace_id=%s AND a.created_at>=%s AND a.created_at<%s
        AND (%s::text IS NULL OR a.state=%s) AND (%s::uuid IS NULL OR c.model_definition_id=%s)
"""


def list_attempts(conn, workspace, start, end, status, model_definition_id, boundary, limit):
    return fetch_page(conn, ATTEMPTS, "a", (workspace, start, end, status, status, model_definition_id,
                                            model_definition_id), boundary, limit)
