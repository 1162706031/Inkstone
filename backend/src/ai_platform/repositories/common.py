"""SQL shared by several domains: workspaces, audit events and keyset pagination."""


def active_workspace(conn, user_id):
    return conn.execute(
        "SELECT id FROM app.workspaces WHERE owner_user_id=%s AND status='active'", (user_id,)
    ).fetchone()


def insert_audit(conn, actor_id, workspace_id, action, target, target_type, request_id):
    conn.execute("""
        INSERT INTO app.audit_events(actor_user_id,workspace_id,action,target_type,target_id,result,request_id)
        VALUES (%s,%s,%s,%s,%s,'success',%s)
    """, (actor_id, workspace_id, action, target_type, target, request_id))


def fetch_page(conn, statement, order, params, boundary, limit, time_column="created_at"):
    """Run ``statement`` (ending in a WHERE clause) with a keyset boundary; returns up to ``limit + 1`` rows."""
    return conn.execute(statement + f"""
        AND (%s::timestamptz IS NULL OR ({order}.{time_column},{order}.id)>(%s,%s))
        ORDER BY {order}.{time_column},{order}.id LIMIT %s
    """, (*params, boundary[0], *boundary, limit + 1)).fetchall()
