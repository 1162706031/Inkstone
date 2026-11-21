"""SQL for member-level external access keys and their agent allowlists."""

from ai_platform.repositories.common import fetch_page

KEY_VIEW = """
    SELECT k.*, creator.status AS creator_status,
        to_jsonb(creator) - 'password_hash' - 'mfa_secret_ciphertext' - 'mfa_key_version'
            - 'mfa_last_counter' AS creator,
        COALESCE(allowed.agent_ids,'{}'::uuid[]) AS agent_ids
    FROM app.access_keys k
    JOIN app.users creator ON creator.id=k.creator_user_id
    LEFT JOIN LATERAL (SELECT array_agg(agent_id ORDER BY agent_id) AS agent_ids
        FROM app.access_key_agents WHERE access_key_id=k.id) allowed ON true
"""


def list_keys(conn, workspace, boundary, limit):
    return fetch_page(conn, KEY_VIEW + " WHERE k.workspace_id=%s", "k", (workspace,), boundary, limit)


def get_key(conn, workspace, key_id, lock=False):
    return conn.execute(
        KEY_VIEW + " WHERE k.workspace_id=%s AND k.id=%s" + (" FOR UPDATE OF k" if lock else ""),
        (workspace, key_id),
    ).fetchone()


def resolve(conn, key_hash):
    """Look a key up by token digest for an inbound API request. Status and expiry are checked by the caller."""
    return conn.execute(KEY_VIEW + " WHERE k.key_hash=%s", (key_hash,)).fetchone()


def insert_key(conn, workspace, creator, name, prefix, key_hash, expires_at):
    return conn.execute(
        """INSERT INTO app.access_keys(workspace_id,creator_user_id,name,key_prefix,key_hash,status,expires_at)
        VALUES (%s,%s,%s,%s,%s,'active',%s) RETURNING id""",
        (workspace, creator, name, prefix, key_hash, expires_at),
    ).fetchone()["id"]


def update_key(conn, key_id, changes):
    """``changes`` keys must be validated column names."""
    conn.execute(
        f"""UPDATE app.access_keys SET {",".join(k + "=%s" for k in changes)},
        revision=revision+1,updated_at=now() WHERE id=%s""",
        (*changes.values(), key_id),
    )


def rotate_secret(conn, key_id, prefix, key_hash):
    conn.execute(
        """UPDATE app.access_keys SET key_prefix=%s,key_hash=%s,revision=revision+1,
        last_used_at=NULL,updated_at=now() WHERE id=%s""",
        (prefix, key_hash, key_id),
    )


def touch(conn, key_id):
    conn.execute("UPDATE app.access_keys SET last_used_at=now() WHERE id=%s", (key_id,))


# --- allowlist ---------------------------------------------------------------

def replace_allowlist(conn, key_id, agent_ids):
    conn.execute("DELETE FROM app.access_key_agents WHERE access_key_id=%s", (key_id,))
    for agent_id in agent_ids:
        conn.execute(
            "INSERT INTO app.access_key_agents(access_key_id,agent_id) VALUES (%s,%s)", (key_id, agent_id)
        )


def allows_agent(conn, key_id, agent_id):
    return conn.execute(
        "SELECT 1 FROM app.access_key_agents WHERE access_key_id=%s AND agent_id=%s", (key_id, agent_id)
    ).fetchone() is not None
