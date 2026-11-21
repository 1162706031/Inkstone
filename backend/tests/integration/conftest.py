import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from ai_platform.services.deletion import init_archive
from ai_platform.services.chat_graph import setup_checkpoints
from ai_platform.settings import settings
from test_identity import identity as identity


@pytest.fixture
def graph_db(identity):  # noqa: F811 - pytest injects the fixture registered above.
    with PostgresSaver.from_conn_string(settings().database_url) as saver:
        setup_checkpoints(saver)
        init_archive()
        yield identity
        for row in saver.conn.execute(
            "SELECT DISTINCT thread_id FROM app.runs WHERE thread_id IS NOT NULL"
        ).fetchall():
            saver.delete_thread(str(row["thread_id"]))
            saver.conn.execute("DELETE FROM graph.write_receipts WHERE thread_id=%s", (row["thread_id"],))
