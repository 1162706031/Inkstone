"""SQL for runs, run events, execution jobs, model calls and idempotency records.

Shared by connection tests and chat runs.
"""

from psycopg.types.json import Jsonb

ACTIVE = ("queued", "running", "cancel_requested")
ACTIVE_SQL = "('queued','running','cancel_requested')"
RUNS = """
    SELECT id,conversation_id,kind,agent_version_id,status,error_code AS error,context_compression,
           input_message_id,output_message_id,created_at,started_at,finished_at,last_event_seq AS last_event_sequence
    FROM app.runs
"""


# --- runs and events ---------------------------------------------------------

def get_run(conn, run_id):
    return conn.execute(RUNS + " WHERE id=%s", (run_id,)).fetchone()


def get_run_as_job(conn, run_id):
    return conn.execute("SELECT *,id AS run_id FROM app.runs WHERE id=%s", (run_id,)).fetchone()


def run_status(conn, run_id):
    return conn.execute("SELECT status FROM app.runs WHERE id=%s", (run_id,)).fetchone()["status"]


def append_event(conn, run, kind, payload):
    row = conn.execute("""
        UPDATE app.runs SET last_event_seq=last_event_seq+1,updated_at=now()
        WHERE id=%s RETURNING last_event_seq
    """, (run,)).fetchone()
    conn.execute("""
        INSERT INTO app.run_events(root_run_id,run_id,sequence,type,payload) VALUES (%s,%s,%s,%s,%s)
    """, (run, run, row["last_event_seq"], kind, Jsonb(payload)))
    return row["last_event_seq"]


def insert_test_run(conn, run, workspace, actor):
    conn.execute("""
        INSERT INTO app.runs(id,workspace_id,actor_user_id,kind,status)
        VALUES (%s,%s,%s,'connection_test','queued')
    """, (run, workspace, actor))


def finish_run(conn, run, status, error):
    conn.execute("""
        UPDATE app.runs SET status=%s,error_code=%s,finished_at=now(),updated_at=now() WHERE id=%s
    """, (status, error, run))


def request_cancel(conn, run):
    conn.execute("UPDATE app.runs SET status='cancel_requested' WHERE id=%s", (run,))


def start_test_run(conn, run, fencing_token):
    return conn.execute("""
        UPDATE app.runs SET status='running',started_at=coalesce(started_at,now()),
            fencing_token=%s,updated_at=now() WHERE id=%s RETURNING started_at
    """, (fencing_token, run)).fetchone()


def purge_old_events(conn):
    conn.execute("DELETE FROM app.run_events WHERE created_at<=now()-interval '7 days'")


# --- execution jobs ----------------------------------------------------------

def insert_job(conn, run):
    conn.execute("INSERT INTO app.execution_jobs(run_id) VALUES (%s)", (run,))


def lock_job(conn, run):
    conn.execute("SELECT run_id FROM app.execution_jobs WHERE run_id=%s FOR UPDATE", (run,))


def finish_job(conn, run):
    conn.execute("UPDATE app.execution_jobs SET status='done',lease_until=NULL,updated_at=now() WHERE run_id=%s",
                 (run,))


def defer_job(conn, run, available_at):
    conn.execute("""
        UPDATE app.execution_jobs SET status='ready',lease_owner=NULL,lease_until=NULL,available_at=%s
        WHERE run_id=%s
    """, (available_at, run))


def locked_job(conn, run, worker, fencing_token):
    """Lock the job only while ``worker`` still holds an unexpired lease with this fencing token."""
    return conn.execute("""
        SELECT run_id FROM app.execution_jobs WHERE run_id=%s AND status='leased' AND lease_owner=%s
            AND fencing_token=%s AND lease_until>clock_timestamp() FOR UPDATE
    """, (run, worker, fencing_token)).fetchone()


def extend_lease(conn, run, *, touch=False):
    conn.execute("""
        UPDATE app.execution_jobs SET lease_until=clock_timestamp()+interval '30 seconds'"""
                 + (",updated_at=now()" if touch else "") + " WHERE run_id=%s", (run,))


def claim_test_job(conn):
    return conn.execute("""
        SELECT j.run_id,j.fencing_token,r.workspace_id,r.actor_user_id,r.created_at,r.started_at,r.status,
               t.model_definition_id,t.request_config_generation,t.credential_version
        FROM app.execution_jobs j JOIN app.runs r ON r.id=j.run_id
        JOIN app.model_test_requests t ON t.run_id=j.run_id
        WHERE r.kind='connection_test' AND (
            (j.status='ready' AND j.available_at<=now()) OR (j.status='leased' AND j.lease_until<=now()))
        ORDER BY j.available_at,j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED
    """).fetchone()


def lease_test_job(conn, run, worker, fencing_token):
    conn.execute("""
        UPDATE app.execution_jobs SET status='leased',lease_owner=%s,lease_until=now()+interval '30 seconds',
            fencing_token=%s,updated_at=now() WHERE run_id=%s
    """, (worker, fencing_token, run))


# --- actors and live models --------------------------------------------------

def share_actor(conn, user_id):
    conn.execute("SELECT id FROM app.users WHERE id=%s FOR SHARE", (user_id,))


def lock_actor_status(conn, user_id):
    return conn.execute("SELECT status FROM app.users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()


def lock_test_model(conn, model_id):
    return conn.execute("""
        SELECT m.*,c.status AS connection_status,c.base_url,c.credential_version,
               k.ciphertext,k.nonce,k.key_version
        FROM app.model_definitions m JOIN app.model_connections c ON c.id=m.connection_id
        JOIN app.credential_secrets k ON k.id=c.credential_id
        WHERE m.id=%s FOR UPDATE OF c,m
    """, (model_id,)).fetchone()


# --- model calls and attempts ------------------------------------------------

def insert_call(conn, call, workspace, run, model_id, purpose):
    conn.execute("""
        INSERT INTO app.model_calls(id,workspace_id,run_id,model_definition_id,logical_call_key,purpose,status)
        VALUES (%s,%s,%s,%s,%s,%s,'dispatching')
    """, (call, workspace, run, model_id, purpose, purpose))


def insert_attempt(conn, attempt, call, workspace, credential_version, deadline):
    conn.execute("""
        INSERT INTO app.model_call_attempts
            (id,model_call_id,workspace_id,attempt_number,state,credential_version,deadline_at,dispatched_at)
        VALUES (%s,%s,%s,1,'dispatching',%s,%s,now())
    """, (attempt, call, workspace, credential_version, deadline))


def prior_test_attempt(conn, run):
    return conn.execute("""
        SELECT a.id,a.model_call_id,a.deadline_at FROM app.model_call_attempts a
        JOIN app.model_calls c ON c.id=a.model_call_id WHERE c.run_id=%s
    """, (run,)).fetchone()


def interrupt_attempt(conn, attempt):
    conn.execute("""
        UPDATE app.model_call_attempts SET state='interrupted',error_code='RESULT_UNKNOWN',finished_at=now()
        WHERE id=%s
    """, (attempt,))


def finish_test_attempt(conn, attempt, state, error):
    conn.execute("""
        UPDATE app.model_call_attempts SET state=%s,error_code=%s,finished_at=now(),updated_at=now() WHERE id=%s
    """, (state, error, attempt))


def set_call_status(conn, call, status, *, touch=False):
    conn.execute("UPDATE app.model_calls SET status=%s" + (",updated_at=now()" if touch else "") + " WHERE id=%s",
                 (status, call))


def expire_recoverable_results(conn):
    conn.execute("UPDATE app.model_call_attempts SET recoverable_result=NULL "
                 "WHERE result_expires_at<=now() AND recoverable_result IS NOT NULL")


# --- idempotency -------------------------------------------------------------

def find_idempotency(conn, operation, workspace, actor, resource, key_hash, *, lock=False):
    return conn.execute("""
        SELECT * FROM app.idempotency_records WHERE operation=%s
        AND (workspace_id,actor_user_id,resource_id,key_hash)=(%s,%s,%s,%s)""" + (" FOR UPDATE" if lock else ""),
        (operation, workspace, actor, resource, key_hash)).fetchone()


def delete_idempotency(conn, record_id):
    conn.execute("DELETE FROM app.idempotency_records WHERE id=%s", (record_id,))


def insert_idempotency(conn, operation, workspace, actor, resource, key_hash, request_hash, run):
    conn.execute("""
        INSERT INTO app.idempotency_records(workspace_id,actor_user_id,resource_id,key_hash,
            operation,request_hash,run_id,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,now()+interval '24 hours')
    """, (workspace, actor, resource, key_hash, operation, request_hash, run))
