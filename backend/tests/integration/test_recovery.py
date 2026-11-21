import os
from threading import RLock
from uuid import uuid4

import psycopg
import pytest
from langchain.agents import AgentState, create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


class ProbeState(AgentState):
    applied_input_seq: int


class LeaseLost(RuntimeError):
    pass


class FencedSaver(PostgresSaver):
    """Probe the stock SQL writes using the same connection as the lease lock."""

    def __init__(self, conn, run_id, thread_id, token):
        super().__init__(conn)
        # Serialize graph callbacks before opening a transaction on this connection.
        self.lock = RLock()
        self.run_id = run_id
        self.thread_id = thread_id
        self.token = token

    def write(self, config, kind, operation, *args):
        with self.lock, self.conn.transaction():
            lease = self.conn.execute(
                """SELECT run_id, thread_id, token FROM probe_jobs
                   WHERE thread_id = %s FOR UPDATE""",
                (self.thread_id,),
            ).fetchone()
            if (
                not lease
                or config["configurable"]["thread_id"] != self.thread_id
                or (lease["run_id"], lease["token"]) != (self.run_id, self.token)
                or not self.conn.execute(
                    "SELECT expires_at > clock_timestamp() AS active "
                    "FROM probe_jobs WHERE thread_id = %s",
                    (self.thread_id,),
                ).fetchone()["active"]
            ):
                raise LeaseLost(kind)
            result = operation(config, *args)
            self.conn.execute(
                "INSERT INTO probe_writes VALUES (%s, %s, %s, %s)",
                (self.run_id, self.thread_id, self.token, kind),
            )
            return result

    def put(self, config, checkpoint, metadata, new_versions):
        return self.write(config, "checkpoint", super().put, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        return self.write(config, "pending", super().put_writes, writes, task_id, task_path)


def graph(saver):
    return create_agent(
        FakeListChatModel(responses=["回复"], fake_error_on_chunk_number=None),
        tools=[], state_schema=ProbeState, checkpointer=saver,
    )


def incremental_input(snapshot, sequence):
    if sequence <= snapshot.values.get("applied_input_seq", 0):
        return None
    return {
        "applied_input_seq": sequence,
        "messages": [HumanMessage(content=f"输入 {sequence}", id=f"input-{sequence}")],
    }


def test_postgres_recovery():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for the isolated PostgreSQL probe")
    if conninfo_to_dict(dsn).get("dbname") != "ai_platform_test":
        pytest.fail("The recovery probe requires ai_platform_test")

    suffix = uuid4().hex
    jobs = sql.Identifier(f"t01_app_{suffix}")
    checkpoints = sql.Identifier(f"t01_graph_{suffix}")
    search_path = sql.SQL("SET search_path TO {}, {}").format(checkpoints, jobs)
    thread_id = "probe-thread"
    config = {"configurable": {"thread_id": thread_id}}
    connect = dict(autocommit=True, row_factory=dict_row, connect_timeout=5)
    with psycopg.connect(dsn, **connect) as admin:
        if admin.execute("SELECT current_database() AS name").fetchone()["name"] != "ai_platform_test":
            pytest.fail("Connected database must be ai_platform_test")
        print(f"PostgreSQL {admin.info.server_version}: ai_platform_test")
        with admin.transaction():
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(jobs))
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(checkpoints))
        try:
            with psycopg.connect(dsn, **connect) as first:
                first.execute(sql.SQL("SET search_path TO {}").format(jobs))
                first.execute(
                    """CREATE TABLE probe_jobs (
                        thread_id text PRIMARY KEY, run_id text NOT NULL,
                        token integer NOT NULL, expires_at timestamptz NOT NULL
                    )"""
                )
                first.execute(
                    """CREATE TABLE probe_writes (
                        run_id text NOT NULL, thread_id text NOT NULL,
                        token integer NOT NULL, kind text NOT NULL
                    )"""
                )
                first.execute(
                    "INSERT INTO probe_jobs VALUES (%s, 'run-1', 1, clock_timestamp() + interval '5 min')",
                    (thread_id,),
                )
                first.execute(search_path)
                PostgresSaver(first).setup()
                first_graph = graph(FencedSaver(first, "run-1", thread_id, 1))
                first_graph.invoke(incremental_input(first_graph.get_state(config), 1), config)
                confirmed = first_graph.get_state(config).config

            with psycopg.connect(dsn, **connect) as old, psycopg.connect(dsn, **connect) as new:
                for conn in (old, new):
                    conn.execute(search_path)
                old.execute(
                    "UPDATE probe_jobs SET run_id = 'run-2', token = 2 WHERE thread_id = %s",
                    (thread_id,),
                )
                stale = FencedSaver(old, "run-2", thread_id, 2)
                resumed = graph(stale)
                snapshot = resumed.get_state(confirmed)
                assert incremental_input(snapshot, 1) is None
                resumed.invoke(incremental_input(snapshot, 2), confirmed, interrupt_before=["model"])
                legal = resumed.get_state(config)
                assert legal.next == ("model",)
                saved = stale.get_tuple(legal.config)

                old.execute(
                    "UPDATE probe_jobs SET expires_at = clock_timestamp() WHERE thread_id = %s",
                    (thread_id,),
                )
                with new.transaction():
                    row = new.execute(
                        """SELECT token FROM probe_jobs WHERE thread_id = %s
                           AND run_id = 'run-2' AND expires_at <= clock_timestamp() FOR UPDATE""",
                        (thread_id,),
                    ).fetchone()
                    assert row == {"token": 2}
                    new.execute(
                        """UPDATE probe_jobs SET token = 3,
                           expires_at = clock_timestamp() + interval '5 min' WHERE thread_id = %s""",
                        (thread_id,),
                    )
                counts_sql = """SELECT
                    (SELECT count(*) FROM checkpoints) AS checkpoints,
                    (SELECT count(*) FROM checkpoint_writes) AS pending,
                    (SELECT count(*) FROM probe_writes) AS audit"""
                counts = new.execute(counts_sql).fetchone()
                with pytest.raises(LeaseLost, match="^checkpoint$"):
                    stale.put(legal.config, {**saved.checkpoint, "id": str(uuid4())}, saved.metadata, {})
                with pytest.raises(LeaseLost, match="^pending$"):
                    stale.put_writes(legal.config, [("probe", "stale")], str(uuid4()))
                assert new.execute(counts_sql).fetchone() == counts

                successor_saver = FencedSaver(new, "run-2", thread_id, 3)
                # Force the audit insert to fail after the stock saver issues its SQL.
                new.execute("ALTER TABLE probe_writes ADD CHECK (token <> 3) NOT VALID")
                with pytest.raises(psycopg.errors.CheckViolation):
                    successor_saver.put(
                        legal.config, {**saved.checkpoint, "id": str(uuid4())}, saved.metadata, {},
                    )
                with pytest.raises(psycopg.errors.CheckViolation):
                    successor_saver.put_writes(legal.config, [("probe", "rollback")], str(uuid4()))
                assert new.execute(counts_sql).fetchone() == counts
                new.execute("ALTER TABLE probe_writes DROP CONSTRAINT probe_writes_token_check")

                successor = graph(successor_saver)
                restored = successor.get_state(legal.config)
                assert restored.values["applied_input_seq"] == 2
                assert incremental_input(restored, 2) is None
                successor.invoke(None, legal.config)
                final = successor.get_state(config)
                assert final.values["applied_input_seq"] == 2
                assert final.next == ()
                messages = final.values["messages"]
                assert [message.id for message in messages if message.type == "human"] == [
                    "input-1", "input-2",
                ]
                assert [message.type for message in messages] == ["human", "ai", "human", "ai"]
                assert {r["kind"] for r in new.execute(
                    "SELECT kind FROM probe_writes WHERE run_id = 'run-2' AND token = 3"
                )} == {"checkpoint", "pending"}
                print(
                    "input_ids=input-1,input-2 applied_input_seq=2 "
                    "stale_checkpoint=denied stale_pending=denied failed_audit_writes=rolled_back"
                )
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {}, {} CASCADE").format(checkpoints, jobs))
