"""SQL for model connections, model definitions and connection-test requests."""

from ai_platform.repositories.common import fetch_page

CONNECTIONS = "SELECT c.* FROM app.model_connections c"
MODELS = """
    SELECT m.*,coalesce(t.status,'untested') AS last_test_status,
           CASE WHEN t.status='passed' THEN 'verified' ELSE 'unverified' END AS capability_status,
           lr.id AS test_run_id,lr.status AS test_run_status,lr.created_at AS test_requested_at,
           CASE WHEN lr.status IN ('failed','interrupted') THEN lr.error_code END AS test_error
    FROM app.model_definitions m JOIN app.model_connections c ON c.id=m.connection_id
    LEFT JOIN LATERAL (
        SELECT status FROM app.model_connection_test_results
        WHERE model_definition_id=m.id AND request_config_generation=m.request_config_generation
          AND credential_version=c.credential_version ORDER BY accepted_order DESC LIMIT 1
    ) t ON true
    LEFT JOIN LATERAL (
        SELECT r.id,r.status,r.error_code,r.created_at FROM app.model_test_requests q JOIN app.runs r ON r.id=q.run_id
        WHERE q.model_definition_id=m.id ORDER BY q.accepted_order DESC LIMIT 1
    ) lr ON true
"""


# --- connections -------------------------------------------------------------

def list_connections(conn, boundary, limit):
    return fetch_page(conn, CONNECTIONS + " WHERE true", "c", (), boundary, limit)


def get_connection(conn, connection_id, *, lock=False):
    return conn.execute(CONNECTIONS + " WHERE c.id=%s" + (" FOR UPDATE" if lock else ""),
                        (connection_id,)).fetchone()


def insert_secret(conn, secret_id, ciphertext, nonce, key_version):
    conn.execute("""
        INSERT INTO app.credential_secrets(id,ciphertext,nonce,key_version) VALUES (%s,%s,%s,%s)
    """, (secret_id, ciphertext, nonce, key_version))


def update_secret(conn, secret_id, ciphertext, nonce, key_version, version):
    conn.execute("""
        UPDATE app.credential_secrets SET ciphertext=%s,nonce=%s,key_version=%s,version=%s,updated_at=now()
        WHERE id=%s
    """, (ciphertext, nonce, key_version, version, secret_id))


def delete_secret(conn, secret_id):
    conn.execute("DELETE FROM app.credential_secrets WHERE id=%s", (secret_id,))


def insert_connection(conn, connection_id, name, base_url, protocol, secret_id):
    return conn.execute("""
        INSERT INTO app.model_connections(id,name,base_url,protocol,credential_id)
        VALUES (%s,%s,%s,%s,%s) RETURNING *
    """, (connection_id, name, base_url, protocol, secret_id)).fetchone()


def update_connection(conn, connection_id, name, status, credential_version):
    return conn.execute("""
        UPDATE app.model_connections SET name=coalesce(%s,name),status=coalesce(%s,status),
            credential_version=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *
    """, (name, status, credential_version, connection_id)).fetchone()


def revoke_strict_budget(conn, connection_id):
    conn.execute("""
        UPDATE app.model_definitions SET strict_budget_eligible=false
        WHERE connection_id=%s AND strict_budget_eligible
    """, (connection_id,))


def delete_connection(conn, connection_id):
    """Raises ``psycopg.errors.ForeignKeyViolation`` while runs still reference the models."""
    conn.execute("DELETE FROM app.model_definitions WHERE connection_id=%s", (connection_id,))
    conn.execute("DELETE FROM app.model_connections WHERE id=%s", (connection_id,))


def connection_secret(conn, connection_id):
    return conn.execute("""
        SELECT c.id,c.base_url,k.ciphertext,k.nonce,k.key_version FROM app.model_connections c
        JOIN app.credential_secrets k ON k.id=c.credential_id WHERE c.id=%s
    """, (connection_id,)).fetchone()


def connection_endpoint(conn, model_definition_id):
    return conn.execute("""SELECT c.base_url FROM app.model_connections c JOIN app.model_definitions m
        ON m.connection_id=c.id WHERE m.id=%s""", (model_definition_id,)).fetchone()["base_url"]


def connection_link(conn, connection_id):
    return conn.execute("SELECT revision,base_url FROM app.model_connections WHERE id=%s",
                        (connection_id,)).fetchone()


# --- model definitions -------------------------------------------------------

def list_models(conn, connection_id, boundary, limit):
    return fetch_page(conn, MODELS + " WHERE m.connection_id=%s", "m", (connection_id,), boundary, limit)


def list_available_models(conn, boundary, limit):
    return fetch_page(conn, MODELS + " WHERE c.status='active' AND m.status='active' AND t.status='passed'",
                      "m", (), boundary, limit)


def get_model(conn, model_id):
    return conn.execute(MODELS + " WHERE m.id=%s", (model_id,)).fetchone()


def get_verified_model(conn, model_id):
    return conn.execute(MODELS + """ WHERE m.id=%s AND m.status='active' AND c.status='active'
        AND t.status='passed'""", (model_id,)).fetchone()


def share_model(conn, model_id):
    return conn.execute("""SELECT m.id FROM app.model_definitions m JOIN app.model_connections c
        ON c.id=m.connection_id WHERE m.id=%s FOR SHARE OF c,m""", (model_id,)).fetchone()


def lock_model(conn, model_id):
    return conn.execute("""
        SELECT m.* FROM app.model_definitions m JOIN app.model_connections c ON c.id=m.connection_id
        WHERE m.id=%s FOR UPDATE OF c,m
    """, (model_id,)).fetchone()


def known_upstream_models(conn, connection_id):
    return {r["upstream_model"] for r in conn.execute(
        "SELECT upstream_model FROM app.model_definitions WHERE connection_id=%s", (connection_id,))}


def insert_model(conn, connection_id, upstream_model, display_name, context_limit, max_output,
                 supports_temperature, usage_support):
    """Raises ``psycopg.errors.UniqueViolation`` when the upstream model is already registered."""
    return conn.execute("""
        INSERT INTO app.model_definitions
            (connection_id,upstream_model,display_name,context_limit_tokens,
             max_output_tokens,supports_temperature,usage_support)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (connection_id, upstream_model, display_name, context_limit, max_output,
          supports_temperature, usage_support)).fetchone()


def insert_model_if_absent(conn, connection_id, upstream_model, display_name, context_limit, max_output):
    return conn.execute("""
        INSERT INTO app.model_definitions
            (connection_id,upstream_model,display_name,context_limit_tokens,
             max_output_tokens,supports_temperature,usage_support)
        VALUES (%s,%s,%s,%s,%s,true,'unknown')
        ON CONFLICT (connection_id,upstream_model) DO NOTHING RETURNING id
    """, (connection_id, upstream_model, display_name, context_limit, max_output)).fetchone()


def update_model(conn, model_id, upstream_model, context_limit, max_output, supports_temperature,
                 display_name, status, changed):
    """Raises ``psycopg.errors.UniqueViolation`` when the new upstream model collides."""
    conn.execute("""
        UPDATE app.model_definitions SET upstream_model=%s,context_limit_tokens=%s,max_output_tokens=%s,
            supports_temperature=%s,display_name=%s,status=%s,revision=revision+1,
            request_config_generation=request_config_generation+%s,
            strict_budget_eligible=strict_budget_eligible AND NOT %s,updated_at=now() WHERE id=%s
    """, (upstream_model, context_limit, max_output, supports_temperature, display_name, status,
          int(changed), changed, model_id))


# --- connection tests --------------------------------------------------------

def lock_test_target(conn, connection_id, model_id):
    return conn.execute("""
        SELECT c.credential_version,c.status AS connection_status,m.status,m.request_config_generation
        FROM app.model_definitions m JOIN app.model_connections c ON c.id=m.connection_id
        WHERE c.id=%s AND m.id=%s FOR UPDATE OF c,m
    """, (connection_id, model_id)).fetchone()


def insert_test_request(conn, run, model_id, generation, credential_version):
    conn.execute("""
        INSERT INTO app.model_test_requests(run_id,model_definition_id,request_config_generation,credential_version)
        VALUES (%s,%s,%s,%s)
    """, (run, model_id, generation, credential_version))


def record_test_result(conn, run, status):
    conn.execute("""
        INSERT INTO app.model_connection_test_results
            (run_id,model_definition_id,request_config_generation,credential_version,accepted_order,status)
        SELECT run_id,model_definition_id,request_config_generation,credential_version,accepted_order,%s
        FROM app.model_test_requests WHERE run_id=%s ON CONFLICT DO NOTHING
    """, (status, run))
