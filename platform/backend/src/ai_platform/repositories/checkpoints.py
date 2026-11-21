"""SQL for the LangGraph checkpoint schema and its fencing receipts."""


def create_receipts_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS graph.write_receipts (
        thread_id uuid NOT NULL, checkpoint_id text NOT NULL, run_id uuid NOT NULL,
        fencing_token bigint NOT NULL, task_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(thread_id,checkpoint_id,run_id,fencing_token,task_id))""")


def insert_receipt(conn, thread_id, checkpoint_id, run_id, fencing_token, task_id):
    conn.execute(
        """INSERT INTO graph.write_receipts(thread_id,checkpoint_id,run_id,fencing_token,task_id)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (thread_id, checkpoint_id, run_id, fencing_token, task_id),
    )


def set_run_checkpoint(conn, run_id, thread_id, checkpoint_id):
    conn.execute("UPDATE app.runs SET checkpoint_id=%s WHERE id=%s AND thread_id=%s",
                 (checkpoint_id, run_id, thread_id))


def confirmed_checkpoint(conn, thread_id, run_id):
    """Latest checkpoint of this thread written by this run or a completed run, with a durable receipt."""
    return conn.execute(
        """SELECT r.checkpoint_id FROM app.runs r WHERE r.thread_id=%s
        AND r.checkpoint_id IS NOT NULL AND (r.id=%s OR r.status='completed')
        AND EXISTS (SELECT 1 FROM graph.write_receipts w WHERE w.run_id=r.id AND w.thread_id=r.thread_id
            AND w.checkpoint_id=r.checkpoint_id AND w.task_id='') ORDER BY r.created_at DESC LIMIT 1""",
        (thread_id, run_id),
    ).fetchone()


def thread_has_checkpoints(conn, thread_id):
    return conn.execute("SELECT 1 FROM graph.checkpoints WHERE thread_id=%s LIMIT 1", (str(thread_id),)).fetchone()
