"""SQL for conversations, messages, chat runs, context summaries and deletion."""

from psycopg.types.json import Jsonb

from ai_platform.repositories.common import fetch_page
from ai_platform.repositories.runs import ACTIVE_SQL, RUNS

CONVERSATIONS = f"""
    SELECT c.*, v.config->>'name' AS agent_name, r.number AS release_version,
        (SELECT id FROM app.runs WHERE conversation_id=c.id AND status IN {ACTIVE_SQL}) AS active_run_id
    FROM app.conversations c
    JOIN app.agent_versions v ON v.id=c.agent_version_id
    LEFT JOIN app.agent_releases r ON r.version_id=v.id
"""


# --- conversations -----------------------------------------------------------

def get_conversation(conn, workspace, conversation_id, lock=False):
    return conn.execute(
        CONVERSATIONS + " WHERE c.workspace_id=%s AND c.id=%s AND c.status='active'"
        + (" FOR UPDATE OF c" if lock else ""),
        (workspace, conversation_id),
    ).fetchone()


def list_conversations(conn, workspace, agent_id, boundary, limit):
    return fetch_page(
        conn,
        CONVERSATIONS + """ WHERE c.workspace_id=%s AND c.status='active'
        AND (%s::uuid IS NULL OR c.agent_id=%s)""",
        "c", (workspace, agent_id, agent_id), boundary, limit,
    )


def insert_conversation(conn, workspace, owner, agent_id, version_id, title, thread_id):
    return conn.execute(
        """INSERT INTO app.conversations(workspace_id,owner_user_id,agent_id,
        agent_version_id,title,status,thread_id) VALUES (%s,%s,%s,%s,%s,'active',%s) RETURNING id""",
        (workspace, owner, agent_id, version_id, title, thread_id),
    ).fetchone()["id"]


def rename(conn, conversation_id, title):
    conn.execute("UPDATE app.conversations SET title=%s,updated_at=now() WHERE id=%s", (title, conversation_id))


def advance_messages(conn, conversation_id, title):
    conn.execute(
        """UPDATE app.conversations SET next_message_seq=next_message_seq+2,
        title=CASE WHEN title='' THEN %s ELSE title END,updated_at=now() WHERE id=%s""",
        (title, conversation_id),
    )


def rotate_thread(conn, conversation_id, thread_id):
    conn.execute(
        "UPDATE app.conversations SET thread_id=gen_random_uuid() WHERE id=%s AND thread_id=%s",
        (conversation_id, thread_id),
    )


def history_page(conn, workspace, conversation_id, cursor, limit):
    # One statement snapshot includes the projection and the matching per-run watermarks.
    return conn.execute(
        """SELECT m.*,r.last_event_seq,r.context_compression FROM app.conversations c
        LEFT JOIN app.messages m ON m.conversation_id=c.id AND m.sequence>%s
        LEFT JOIN app.runs r ON r.id=m.run_id WHERE c.id=%s AND c.workspace_id=%s AND c.status='active'
        ORDER BY m.sequence LIMIT %s""",
        (cursor, conversation_id, workspace, limit + 1),
    ).fetchall()


def completed_history(conn, workspace, conversation_id, after, before):
    return conn.execute(
        """SELECT m.sequence,m.role,m.content->0->>'text' AS text
        FROM app.messages m JOIN app.runs r ON r.id=m.run_id
        WHERE m.conversation_id=%s AND m.workspace_id=%s AND r.status='completed'
            AND m.sequence>%s AND m.sequence<%s ORDER BY m.sequence""",
        (conversation_id, workspace, after, before),
    ).fetchall()


def recently_used(conn, workspace, conversation_id):
    return conn.execute(
        """SELECT 1 FROM app.conversations c WHERE c.workspace_id=%s AND c.id=%s
        AND c.status='active' AND EXISTS (SELECT 1 FROM app.runs r WHERE r.conversation_id=c.id
        AND (r.finished_at IS NULL OR greatest(r.created_at,r.finished_at)>now()-interval '30 minutes'))""",
        (workspace, conversation_id),
    ).fetchone()


# --- runs and messages -------------------------------------------------------

def visible_run(conn, workspace, run_id):
    return conn.execute(
        RUNS + """ WHERE id=%s AND workspace_id=%s AND payload_purged_at IS NULL
        AND (kind='connection_test' OR EXISTS (SELECT 1 FROM app.conversations c
            WHERE c.id=runs.conversation_id AND c.status='active'))""",
        (run_id, workspace),
    ).fetchone()


def output_snapshot(conn, message_id, workspace):
    # Read the message and advertised watermark together even if a batch committed since the check.
    return conn.execute(
        """SELECT row_to_json(m) AS output_message,r.status,r.last_event_seq
        AS last_event_sequence,r.finished_at,r.error_code AS error,r.context_compression
        FROM app.messages m JOIN app.runs r ON r.id=m.run_id JOIN app.conversations c ON c.id=m.conversation_id
        WHERE m.id=%s AND c.workspace_id=%s AND c.status='active'""",
        (message_id, workspace),
    ).fetchone()


def insert_chat_run(conn, run, workspace, actor, conversation, version, input_id, output_id, thread_id):
    conn.execute(
        """INSERT INTO app.runs(id,workspace_id,actor_user_id,conversation_id,agent_version_id,kind,
        status,input_message_id,output_message_id,thread_id,context_compression)
        VALUES (%s,%s,%s,%s,%s,'chat','queued',%s,%s,%s,'none')""",
        (run, workspace, actor, conversation, version, input_id, output_id, thread_id),
    )


def insert_message(conn, message_id, workspace, conversation, run, sequence, role, content):
    conn.execute(
        """INSERT INTO app.messages(id,workspace_id,conversation_id,run_id,sequence,role,
        content,status) VALUES (%s,%s,%s,%s,%s,%s,%s,'accepted')""",
        (message_id, workspace, conversation, run, sequence, role, Jsonb(content)),
    )


def lock_message_content(conn, message_id):
    return conn.execute("SELECT content FROM app.messages WHERE id=%s FOR UPDATE", (message_id,)).fetchone()


def project_message(conn, message_id, content, sequence):
    conn.execute(
        """UPDATE app.messages SET content=%s,status='streaming',projected_event_seq=%s,
        updated_at=now() WHERE id=%s""",
        (Jsonb(content), sequence, message_id),
    )


def set_message_status(conn, message_id, status):
    return conn.execute(
        "UPDATE app.messages SET status=%s,updated_at=now() WHERE id=%s RETURNING content", (status, message_id),
    ).fetchone()


def set_projected_seq(conn, message_id, sequence):
    conn.execute("UPDATE app.messages SET projected_event_seq=%s WHERE id=%s", (sequence, message_id))


def complete_input(conn, message_id):
    conn.execute("UPDATE app.messages SET status='completed' WHERE id=%s", (message_id,))


def run_events(conn, run_id, after):
    return conn.execute(
        """SELECT sequence,type,payload FROM app.run_events WHERE root_run_id=%s
        AND sequence>%s AND created_at>now()-interval '7 days' ORDER BY sequence LIMIT 100""",
        (run_id, after),
    ).fetchall()


def has_active_run(conn, conversation_id):
    return conn.execute(
        f"SELECT 1 FROM app.runs WHERE conversation_id=%s AND status IN {ACTIVE_SQL}", (conversation_id,)
    ).fetchone()


# --- chat execution ----------------------------------------------------------

def claim_chat_job(conn):
    return conn.execute("""SELECT j.run_id,j.fencing_token,r.started_at,r.created_at,r.workspace_id,r.actor_user_id,
        r.status,r.conversation_id,r.thread_id,r.input_message_id,r.output_message_id,v.config,m.snapshot,
        m.model_definition_id,i.sequence AS input_sequence,i.content->0->>'text' AS input_text
        FROM app.execution_jobs j JOIN app.runs r ON r.id=j.run_id
        JOIN app.agent_versions v ON v.id=r.agent_version_id
        JOIN app.model_config_versions m ON m.id=v.model_config_version_id JOIN app.messages i ON i.id=r.input_message_id
        WHERE r.kind='chat' AND ((j.status='ready' AND j.available_at<=now()) OR
            (j.status='leased' AND j.lease_until<=now())) ORDER BY j.available_at,j.id
        LIMIT 1 FOR UPDATE OF j SKIP LOCKED""").fetchone()


def pending_attempt(conn, run):
    # An abandoned dispatch holds admission until its original upstream deadline.
    return conn.execute(
        """SELECT a.*,c.id AS call FROM app.model_calls c JOIN app.model_call_attempts a
        ON a.model_call_id=c.id WHERE c.run_id=%s AND a.state='dispatching'""",
        (run,),
    ).fetchone()


def lease_chat_job(conn, run, worker, fencing_token):
    conn.execute(
        """UPDATE app.execution_jobs SET status='leased',lease_owner=%s,fencing_token=%s,
        lease_until=clock_timestamp()+interval '30 seconds' WHERE run_id=%s""",
        (worker, fencing_token, run),
    )


def start_chat_run(conn, run, fencing_token):
    return conn.execute(
        """UPDATE app.runs SET status='running',started_at=coalesce(started_at,now()),
        fencing_token=%s WHERE id=%s RETURNING started_at""",
        (fencing_token, run),
    ).fetchone()


def finish_chat_job(conn, run):
    conn.execute("UPDATE app.execution_jobs SET status='done',lease_until=NULL WHERE run_id=%s", (run,))


def keep_recoverable_results(conn, run):
    conn.execute(
        """UPDATE app.model_call_attempts SET result_expires_at=now()+interval '24 hours'
        FROM app.model_calls c WHERE model_call_attempts.model_call_id=c.id AND c.run_id=%s
        AND recoverable_result IS NOT NULL""",
        (run,),
    )


def finish_chat_attempt(conn, attempt, state, error, result):
    conn.execute(
        """UPDATE app.model_call_attempts SET state=%s,error_code=%s,finished_at=now(),
        recoverable_result=%s,result_expires_at=now()+interval '7 days' WHERE id=%s""",
        (state, error, Jsonb(result) if result is not None else None, attempt),
    )


def prior_result(conn, run, purpose):
    return conn.execute(
        """SELECT a.state,a.error_code,a.recoverable_result FROM app.model_calls c
        JOIN app.model_call_attempts a ON a.model_call_id=c.id WHERE c.run_id=%s AND c.logical_call_key=%s""",
        (run, purpose),
    ).fetchone()


def live_chat_model(conn, actor, model_id, conversation, workspace, endpoint):
    """Lock every row that decides whether this actor may still call the model."""
    return conn.execute(
        """SELECT m.id,m.strict_budget_eligible,m.request_config_generation,c.id AS connection_id,
        c.credential_version,k.nonce,k.ciphertext,k.key_version FROM app.model_definitions m
        JOIN app.model_connections c ON c.id=m.connection_id JOIN app.credential_secrets k ON k.id=c.credential_id
        JOIN app.users u ON u.id=%s JOIN app.workspaces w ON w.owner_user_id=u.id
        JOIN app.conversations v ON v.workspace_id=w.id JOIN app.agents a ON a.id=v.agent_id
        JOIN app.workspaces aw ON aw.id=a.workspace_id JOIN app.users creator ON creator.id=aw.owner_user_id
        WHERE m.id=%s AND v.id=%s AND w.id=%s AND v.status='active' AND a.status='active' AND w.status='active'
        AND u.status='active' AND c.status='active' AND m.status='active' AND c.base_url=%s
        AND (u.role='admin' OR (aw.status='active' AND creator.status='active'
            AND (creator.id=u.id OR a.visibility='public')))
        FOR SHARE OF m,c,u,w,a,v,aw,creator""",
        (actor, model_id, conversation, workspace, endpoint),
    ).fetchone()


def set_compression(conn, run, mode):
    conn.execute("UPDATE app.runs SET context_compression=%s WHERE id=%s", (mode, run))


def latest_summary(conn, workspace, conversation):
    return conn.execute(
        """SELECT * FROM app.conversation_context_summaries WHERE
        workspace_id=%s AND conversation_id=%s ORDER BY covered_through_seq DESC LIMIT 1""",
        (workspace, conversation),
    ).fetchone()


def insert_summary(conn, workspace, conversation, run, covered_through, previous, text):
    conn.execute(
        """INSERT INTO app.conversation_context_summaries(workspace_id,conversation_id,
        run_id,covered_through_seq,previous_summary_id,summary_text) VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT(run_id) DO NOTHING""",
        (workspace, conversation, run, covered_through, previous, text),
    )


# --- deletion ----------------------------------------------------------------

def lock_any_conversation(conn, workspace, conversation_id):
    return conn.execute(
        "SELECT id FROM app.conversations WHERE workspace_id=%s AND id=%s FOR UPDATE", (workspace, conversation_id),
    ).fetchone()


def deletion_marker(conn, workspace, conversation_id):
    return conn.execute(
        """SELECT * FROM app.conversation_deletion_markers
        WHERE workspace_id=%s AND conversation_id=%s""",
        (workspace, conversation_id),
    ).fetchone()


def mark_deleting(conn, conversation_id):
    conn.execute(
        "UPDATE app.conversations SET status='deleting',deletion_requested_at=now() WHERE id=%s",
        (conversation_id,),
    )


def insert_deletion_marker(conn, workspace, conversation_id):
    return conn.execute(
        """INSERT INTO app.conversation_deletion_markers(workspace_id,conversation_id)
        VALUES (%s,%s) RETURNING *""",
        (workspace, conversation_id),
    ).fetchone()


def confirm_marker(conn, event_id):
    conn.execute(
        """UPDATE app.conversation_deletion_markers SET archived_at=coalesce(archived_at,now())
        WHERE event_id=%s""",
        (event_id,),
    )


def pending_markers(conn):
    return conn.execute("""SELECT m.* FROM app.conversation_deletion_markers m
        JOIN app.conversations c ON c.id=m.conversation_id WHERE c.status='deleting'
        ORDER BY m.archived_at NULLS LAST,m.deleted_at""").fetchall()


def lock_deleting(conn, conversation_id):
    return conn.execute(
        "SELECT id FROM app.conversations WHERE id=%s AND status='deleting' FOR UPDATE SKIP LOCKED",
        (conversation_id,),
    ).fetchone()


def purge_conversation(conn, cid):
    conn.execute(
        """DELETE FROM graph.write_receipts WHERE thread_id IN (
        SELECT thread_id FROM app.runs WHERE conversation_id=%s)""",
        (cid,),
    )
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        conn.execute(
            f"""DELETE FROM graph.{table} WHERE thread_id IN (
            SELECT thread_id::text FROM app.runs WHERE conversation_id=%s)""",
            (cid,),
        )
    conn.execute(
        """UPDATE app.model_call_attempts SET recoverable_result=NULL,result_message_id=NULL,
        result_expires_at=NULL FROM app.model_calls c JOIN app.runs r ON r.id=c.run_id
        WHERE model_call_attempts.model_call_id=c.id AND r.conversation_id=%s""",
        (cid,),
    )
    conn.execute(
        "DELETE FROM app.run_events WHERE run_id IN (SELECT id FROM app.runs WHERE conversation_id=%s)",
        (cid,),
    )
    conn.execute("DELETE FROM app.conversation_context_summaries WHERE conversation_id=%s", (cid,))
    conn.execute("DELETE FROM app.messages WHERE conversation_id=%s", (cid,))
    conn.execute(
        """UPDATE app.runs SET payload_purged_at=now(),conversation_id=NULL,thread_id=NULL,
        checkpoint_id=NULL,input_message_id=NULL,output_message_id=NULL WHERE conversation_id=%s""",
        (cid,),
    )
    conn.execute("DELETE FROM app.conversations WHERE id=%s", (cid,))


def workspace_exists(conn, workspace):
    return conn.execute("SELECT 1 FROM app.workspaces WHERE id=%s", (workspace,)).fetchone()


def restore_marker(conn, event_id, workspace, conversation_id, deleted_at):
    conn.execute(
        """INSERT INTO app.conversation_deletion_markers(event_id,workspace_id,conversation_id,
        deleted_at,archived_at) VALUES (%s,%s,%s,%s,now()) ON CONFLICT DO NOTHING""",
        (event_id, workspace, conversation_id, deleted_at),
    )
    conn.execute(
        """UPDATE app.conversation_deletion_markers SET archived_at=coalesce(archived_at,now())
        WHERE workspace_id=%s AND conversation_id=%s""",
        (workspace, conversation_id),
    )


def cancel_deleted_runs(conn, conversation_id):
    runs = conn.execute(
        f"""UPDATE app.runs SET status='cancelled',error_code='CONVERSATION_DELETED',
        finished_at=now(),updated_at=now() WHERE conversation_id=%s AND status IN {ACTIVE_SQL}
        RETURNING id""",
        (conversation_id,),
    ).fetchall()
    for run in runs:
        conn.execute("UPDATE app.execution_jobs SET status='done',lease_until=NULL WHERE run_id=%s", (run["id"],))


def hide_conversation(conn, workspace, conversation_id, deleted_at):
    return conn.execute(
        """UPDATE app.conversations SET status='deleting',
        deletion_requested_at=coalesce(deletion_requested_at,%s) WHERE workspace_id=%s AND id=%s
        RETURNING id""",
        (deleted_at, workspace, conversation_id),
    ).fetchone()


def count_deleting(conn):
    return conn.execute("SELECT count(*) AS n FROM app.conversations WHERE status='deleting'").fetchone()["n"]
