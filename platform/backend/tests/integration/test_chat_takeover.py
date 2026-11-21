import subprocess
import sys
from uuid import UUID

import pytest

from ai_platform.services import chat_execution as chat
from ai_platform.services.chat_context import cost, message
from ai_platform.db import connection
from test_chat import send, setup_chat
from test_identity import post
from test_models_metering import fake_supplier


def crash_after(purpose):
    # Terminate the worker process after the durable call result, before returning to LangGraph.
    code = """
import os,sys
from ai_platform.services import chat_execution as chat
from ai_platform.services import metering
from ai_platform.settings import settings
if not settings().redis_url:
    metering.redis_admission=lambda *_:(True,"")
original=chat.invoke
async def crash(job,purpose,*args):
    result=await original(job,purpose,*args)
    if purpose==sys.argv[1]:
        os._exit(73)
    return result
chat.invoke=crash
chat.run_once("crashing-worker")
"""
    result = subprocess.run([sys.executable, "-c", code, purpose], timeout=30)
    assert result.returncode == 73


def expire(run):
    with connection() as conn:
        conn.execute("UPDATE app.execution_jobs SET lease_until=now() WHERE run_id=%s", (run,))


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_takeover_and_deadlines(graph_db, monkeypatch):
    with graph_db() as admin, graph_db() as member, fake_supplier() as (endpoint, requests):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        agent = post(
            member,
            "/agents",
            {
                "name": "short history",
                "model_definition_id": model,
                "history_max_messages": 2,
                "context_budget_tokens": 240,
                "max_output_tokens": 40,
            },
        ).json()["id"]
        assert post(member, f"/agents/{agent}/publish", {"revision": 1}).is_success
        cid = post(member, f"/agents/{agent}/conversations", {"agent_revision": 1}).json()["id"]
        for text in ("first", "second"):
            assert send(member, agent, cid, text).is_success and chat.run_once("normal")
        run = send(member, agent, cid, "third").json()["id"]
        crash_after("context_summary")
        assert len(requests) == 4
        expire(run)
        crash_after("chat")
        assert len(requests) == 5
        expire(run)
        assert chat.run_once("successor")
        assert member.get(f"/api/v1/runs/{run}").json()["status"] == "completed"
        assert len(requests) == 5
        with connection() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM app.conversation_context_summaries WHERE run_id=%s", (run,)
                ).fetchone()["n"]
                == 1
            )
        slow = send(member, agent, cid, "last " + "a" * 105).json()["id"]
        with connection() as conn:
            first = chat.claim(conn, "lost-before-call")
            assert first["run_id"] == UUID(slow)
            started = conn.execute(
                """UPDATE app.runs SET started_at=now()-interval '70 seconds' WHERE id=%s
                RETURNING started_at""",
                (slow,),
            ).fetchone()["started_at"]
        expire(slow)
        assert chat.run_once("successor")
        final = member.get(f"/api/v1/runs/{slow}").json()
        assert final["status"] == "completed" and final["context_compression"] == "truncated"
        with connection() as conn:
            attempt = conn.execute(
                """SELECT r.started_at,a.deadline_at,c.purpose FROM app.runs r
                JOIN app.model_calls c ON c.run_id=r.id JOIN app.model_call_attempts a ON a.model_call_id=c.id
                WHERE r.id=%s""",
                (slow,),
            ).fetchall()
        assert len(attempt) == 1 and attempt[0]["purpose"] == "chat" and attempt[0]["started_at"] == started
        assert (attempt[0]["deadline_at"] - started).total_seconds() == 180
        sent = requests[-1][2]
        # The separate rolling summary does not count toward the raw history cap.
        assert sent["messages"][1:-1] == [
            message("assistant", "OK"),
            message("user", "third"),
            message("assistant", "OK"),
        ]
        assert 16 + sent["max_tokens"] + sum(map(cost, sent["messages"])) <= 240
        overflow = send(member, agent, cid, "x" * 230).json()["id"]
        assert chat.run_once("successor")
        assert member.get(f"/api/v1/runs/{overflow}").json()["error"] == "CONTEXT_LIMIT_EXCEEDED"
        assert len(requests) == 6
        print(
            "worker_exits=2 summary_dispatches=1 recovered_reply_dispatches=1 repeated=0 "
            "late_summary_dispatches=0 deadline=original_start+180s requests=6"
        )


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_inflight_crash_preserves_committed_text(graph_db, monkeypatch):
    with graph_db() as admin, graph_db() as member, fake_supplier() as (endpoint, requests):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        agent = post(member, "/agents", {"name": "crash", "model_definition_id": model}).json()["id"]
        assert post(member, f"/agents/{agent}/publish", {"revision": 1}).is_success
        cid = post(member, f"/agents/{agent}/conversations", {"agent_revision": 1}).json()["id"]
        run = send(member, agent, cid, "partial").json()["id"]
        code = """
import os
from ai_platform.services import chat_execution as chat
from ai_platform.services import metering
from ai_platform.settings import settings
if not settings().redis_url:
    metering.redis_admission=lambda *_:(True,"")
original=chat.WriteBuffer.receive
async def crash(self,text):
    await original(self,text)
    os._exit(74)
chat.WriteBuffer.receive=crash
chat.run_once("lost-worker")
"""
        assert subprocess.run([sys.executable, "-c", code], timeout=30).returncode == 74
        expire(run)
        assert not chat.run_once("replacement")
        with connection() as conn:
            attempt = conn.execute(
                """SELECT a.state,a.deadline_at,j.available_at FROM app.model_call_attempts a
                JOIN app.model_calls c ON c.id=a.model_call_id JOIN app.execution_jobs j ON j.run_id=c.run_id
                WHERE c.run_id=%s""",
                (run,),
            ).fetchone()
            assert attempt["state"] == "dispatching" and attempt["available_at"] == attempt["deadline_at"]
            conn.execute(
                """UPDATE app.model_call_attempts SET deadline_at=now() FROM app.model_calls c
                WHERE model_call_attempts.model_call_id=c.id AND c.run_id=%s""",
                (run,),
            )
            conn.execute("UPDATE app.execution_jobs SET available_at=now() WHERE run_id=%s", (run,))
        assert not chat.run_once("replacement")
        result = member.get(f"/api/v1/runs/{run}").json()
        assert result["status"] == "interrupted" and result["output_message"]["content"][0]["text"] == "OK"
        usage = member.get("/api/v1/usage/summary").json()
        assert usage["unknown_attempts"] == 1 and usage["uncertain_reserved_tokens"] > 0
        assert len(requests) == 2
        print("inflight_exit=74 committed_text=OK status=interrupted unknown_attempts=1 replay_requests=0")
