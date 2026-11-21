"""SQL for users, sessions, verification tokens and MFA."""


def insert_audit_event(conn, action, target, actor, request_id):
    conn.execute("""
        INSERT INTO app.audit_events(actor_user_id, workspace_id, action, target_type,
                                     target_id, result, request_id)
        VALUES (%s, (SELECT id FROM app.workspaces WHERE owner_user_id = %s),
                %s, 'user', %s, 'success', %s)
    """, (actor, actor, action, target, request_id))


# --- administrator bootstrap -------------------------------------------------

def lock_bootstrap(conn):
    conn.execute("SELECT pg_advisory_xact_lock(1848103262)")


def find_admin_or_reserved(conn):
    return conn.execute("SELECT id FROM app.users WHERE role = 'admin' OR username = 'admin'").fetchone()


def insert_admin(conn, password_hash):
    return conn.execute("""
        INSERT INTO app.users(username,display_name,password_hash,must_change_password,role,status)
        VALUES ('admin','Administrator',%s,true,'admin','active') RETURNING id
    """, (password_hash,)).fetchone()


def insert_workspace(conn, user_id):
    conn.execute("INSERT INTO app.workspaces(owner_user_id) VALUES (%s)", (user_id,))


def clear_admin_mfa(conn, username):
    return conn.execute("""
        UPDATE app.users SET mfa_secret_ciphertext=NULL, mfa_key_version=NULL, mfa_last_counter=NULL,
                             revision=revision+1, updated_at=now()
        WHERE username=%s AND role='admin' RETURNING id
    """, (username,)).fetchone()


# --- users -------------------------------------------------------------------

def lock_user(conn, user_id):
    return conn.execute("SELECT * FROM app.users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()


def lock_user_by_username(conn, username):
    return conn.execute(
        "SELECT * FROM app.users WHERE username=%s AND status<>'deleted' FOR UPDATE", (username,),
    ).fetchone()


def update_password(conn, user_id, password_hash):
    conn.execute("""
        UPDATE app.users SET password_hash=%s,must_change_password=false,
            status='active',revision=revision+1,updated_at=now() WHERE id=%s
    """, (password_hash, user_id))


def list_users(conn, status, boundary, limit):
    return conn.execute("""
        SELECT * FROM app.users WHERE status<>'deleted' AND (%s::text IS NULL OR status=%s)
        AND (%s::timestamptz IS NULL OR (created_at,id)>(%s,%s))
        ORDER BY created_at,id LIMIT %s
    """, (status, status, boundary[0], *boundary, limit+1)).fetchall()


def insert_member(conn, username, display_name):
    """Raises ``psycopg.errors.UniqueViolation`` when the username is taken."""
    return conn.execute("""
        INSERT INTO app.users(username,display_name,role,status)
        VALUES (%s,%s,'member','pending') RETURNING *
    """, (username, display_name)).fetchone()


def set_user_status(conn, user_id, status):
    return conn.execute("""
        UPDATE app.users SET status=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *
    """, (status, user_id)).fetchone()


def mark_user_deleted(conn, user_id):
    conn.execute("""
        UPDATE app.users SET status='deleted', password_hash=NULL, revision=revision+1, updated_at=now()
        WHERE id=%s
    """, (user_id,))


def disable_workspace(conn, user_id):
    conn.execute("UPDATE app.workspaces SET status='disabled' WHERE owner_user_id=%s", (user_id,))


# --- sessions ----------------------------------------------------------------

def session_owner(conn, token_hash):
    return conn.execute("SELECT user_id FROM app.auth_sessions WHERE token_hash=%s", (token_hash,)).fetchone()


def live_session(conn, token_hash):
    return conn.execute("""
        SELECT * FROM app.auth_sessions WHERE token_hash=%s AND revoked_at IS NULL
        AND expires_at>clock_timestamp() AND idle_expires_at>clock_timestamp() FOR UPDATE
    """, (token_hash,)).fetchone()


def insert_session(conn, user_id, token_hash, csrf_hash, stage, purpose, expires, idle):
    conn.execute("""
        INSERT INTO app.auth_sessions(user_id,token_hash,csrf_hash,stage,purpose,expires_at,idle_expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user_id, token_hash, csrf_hash, stage, purpose, expires, idle))


def revoke_session(conn, session_id):
    conn.execute("UPDATE app.auth_sessions SET revoked_at=now() WHERE id=%s", (session_id,))


def revoke_user_sessions(conn, user_id, keep):
    conn.execute("""
        UPDATE app.auth_sessions SET revoked_at=now()
        WHERE user_id=%s AND revoked_at IS NULL AND id IS DISTINCT FROM %s
    """, (user_id, keep))


def touch_session(conn, session_id):
    conn.execute("""
        UPDATE app.auth_sessions SET last_seen_at=now(),
            idle_expires_at=least(expires_at, now()+interval '12 hours') WHERE id=%s
    """, (session_id,))


def set_verified_until(conn, session_id, until):
    conn.execute("UPDATE app.auth_sessions SET verified_until=%s WHERE id=%s", (until, session_id))


def set_pending_mfa(conn, session_id, ciphertext, key_version):
    conn.execute("""
        UPDATE app.auth_sessions SET pending_mfa_ciphertext=%s,pending_mfa_key_version=%s,
            pending_mfa_expires_at=least(expires_at,now()+interval '5 minutes') WHERE id=%s
    """, (ciphertext, key_version, session_id))


def purge_sessions(conn):
    conn.execute("""
        DELETE FROM app.auth_sessions WHERE id IN (
            SELECT id FROM app.auth_sessions WHERE expires_at<now()
                OR (revoked_at IS NOT NULL AND revoked_at<now()-interval '30 minutes')
            ORDER BY expires_at LIMIT 5000 FOR UPDATE SKIP LOCKED
        )
    """)


# --- verification tokens -----------------------------------------------------

def consume_open_tokens(conn, user_id, purpose):
    conn.execute("""
        UPDATE app.verification_tokens SET consumed_at=now()
        WHERE user_id=%s AND purpose=%s AND consumed_at IS NULL
    """, (user_id, purpose))


def consume_all_open_tokens(conn, user_id):
    conn.execute("""
        UPDATE app.verification_tokens SET consumed_at=now()
        WHERE user_id=%s AND consumed_at IS NULL
    """, (user_id,))


def insert_verification(conn, user_id, purpose, token_hash, expires):
    conn.execute("""
        INSERT INTO app.verification_tokens(user_id,purpose,token_hash,expires_at)
        VALUES (%s,%s,%s,%s)
    """, (user_id, purpose, token_hash, expires))


def verification_owner(conn, token_hash, purpose):
    return conn.execute(
        "SELECT user_id FROM app.verification_tokens WHERE token_hash=%s AND purpose=%s", (token_hash, purpose),
    ).fetchone()


def consume_verification(conn, token_hash):
    return conn.execute("""
        UPDATE app.verification_tokens SET consumed_at=now()
        WHERE token_hash=%s AND consumed_at IS NULL AND expires_at>clock_timestamp()
        RETURNING id
    """, (token_hash,)).fetchone()


def delete_tokens(conn, user_id):
    conn.execute("DELETE FROM app.verification_tokens WHERE user_id=%s", (user_id,))


# --- MFA ---------------------------------------------------------------------

def set_mfa_counter(conn, user_id, counter):
    conn.execute("UPDATE app.users SET mfa_last_counter=%s WHERE id=%s", (counter, user_id))


def set_mfa_secret(conn, user_id, ciphertext, key_version, counter):
    conn.execute("""
        UPDATE app.users SET mfa_secret_ciphertext=%s,mfa_key_version=%s,mfa_last_counter=%s,
                             revision=revision+1,updated_at=now() WHERE id=%s
    """, (ciphertext, key_version, counter, user_id))


def delete_recovery_codes(conn, user_id):
    conn.execute("DELETE FROM app.mfa_recovery_codes WHERE user_id=%s", (user_id,))


def insert_recovery_codes(conn, user_id, code_hashes):
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO app.mfa_recovery_codes(user_id,code_hash) VALUES (%s,%s)",
            [(user_id, code_hash) for code_hash in code_hashes],
        )


def consume_recovery_code(conn, user_id, code_hash):
    return conn.execute("""
        UPDATE app.mfa_recovery_codes SET consumed_at=now()
        WHERE user_id=%s AND code_hash=%s AND consumed_at IS NULL RETURNING user_id
    """, (user_id, code_hash)).fetchone()
