"""SQL for agents, agent versions, releases and model configuration snapshots."""

from psycopg.types.json import Jsonb

from ai_platform.repositories.common import fetch_page

AGENT_CATALOG = """
    SELECT a.*, creator.id AS creator_id, creator.display_name AS creator_name,
        creator.id=viewer.id AS is_creator, (viewer.admin OR creator.id=viewer.id) AS can_manage,
        a.status='active' AS can_edit, released.number AS published_version,
        v.revision AS published_revision, v.config->>'name' AS published_name,
        v.config->>'description' AS published_description,
        v.config->>'model_definition_id' AS published_model_definition_id
    FROM app.agents a JOIN app.workspaces aw ON aw.id=a.workspace_id
    JOIN app.users creator ON creator.id=aw.owner_user_id
    CROSS JOIN (SELECT %s::uuid AS id, %s::boolean AS admin) viewer
    LEFT JOIN LATERAL (SELECT number,version_id FROM app.agent_releases
        WHERE agent_id=a.id ORDER BY number DESC LIMIT 1) released ON true
    LEFT JOIN app.agent_versions v ON v.id=released.version_id
    WHERE (viewer.admin OR (aw.status='active' AND creator.status='active' AND
        (creator.id=viewer.id OR (a.status='active' AND a.visibility='public'))))
"""


def list_agents(conn, viewer, boundary, limit):
    return fetch_page(conn, AGENT_CATALOG + " AND a.status='active'", "a", viewer, boundary, limit)


def get_agent(conn, viewer, agent_id, lock=None):
    """``lock`` is ``None``, ``"update"`` or ``"share"``."""
    suffix = {None: "", "update": " FOR UPDATE OF a", "share": " FOR SHARE OF a"}[lock]
    return conn.execute(AGENT_CATALOG + " AND a.id=%s" + suffix, (*viewer, agent_id)).fetchone()


def insert_agent(conn, workspace, visibility, fields, values):
    return conn.execute(
        f"""INSERT INTO app.agents(workspace_id,visibility,{",".join(fields)})
        VALUES (%s,%s,{",".join(["%s"] * len(fields))}) RETURNING id""",
        (workspace, visibility, *values),
    ).fetchone()


def update_agent(conn, agent_id, changes):
    """``changes`` keys must be validated column names from the agent schema."""
    conn.execute(
        f"""UPDATE app.agents SET {",".join(k + "=%s" for k in changes)},
        revision=revision+1,updated_at=now() WHERE id=%s""",
        (*changes.values(), agent_id),
    )


def archive_agent(conn, agent_id):
    conn.execute("UPDATE app.agents SET status='archived',updated_at=now() WHERE id=%s", (agent_id,))


def touch_agent(conn, agent_id):
    conn.execute("UPDATE app.agents SET updated_at=now() WHERE id=%s", (agent_id,))


def list_versions(conn, workspace, agent_id, boundary, limit):
    return fetch_page(
        conn,
        """SELECT v.*,r.number AS published_version FROM app.agent_versions v
        LEFT JOIN app.agent_releases r ON r.version_id=v.id WHERE v.workspace_id=%s AND v.agent_id=%s""",
        "v", (workspace, agent_id), boundary, limit,
    )


def ensure_model_config_version(conn, key, snapshot):
    """Insert the snapshot for ``(connection, model, connection revision, model revision)``; returns its id."""
    conn.execute(
        """INSERT INTO app.model_config_versions
        (connection_id,model_definition_id,connection_revision,model_revision,snapshot)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (*key, Jsonb(snapshot)),
    )
    return conn.execute(
        """SELECT id FROM app.model_config_versions WHERE
        (connection_id,model_definition_id,connection_revision,model_revision)=(%s,%s,%s,%s)""",
        key,
    ).fetchone()


def ensure_agent_version(conn, workspace, agent_id, revision, config_version, config, config_hash):
    conn.execute(
        """INSERT INTO app.agent_versions(workspace_id,agent_id,revision,model_config_version_id,
        config,config_hash) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (workspace, agent_id, revision, config_version, Jsonb(config), config_hash),
    )
    return conn.execute(
        """SELECT id FROM app.agent_versions WHERE
        (agent_id,revision,model_config_version_id)=(%s,%s,%s)""",
        (agent_id, revision, config_version),
    ).fetchone()


def insert_release(conn, agent_id, number, version_id):
    conn.execute("INSERT INTO app.agent_releases(agent_id,number,version_id) VALUES (%s,%s,%s)",
                 (agent_id, number, version_id))


def released_version(conn, agent_id, number):
    return conn.execute("""SELECT v.id,v.config FROM app.agent_versions v
        JOIN app.agent_releases r ON r.version_id=v.id WHERE r.agent_id=%s AND r.number=%s""",
        (agent_id, number)).fetchone()
