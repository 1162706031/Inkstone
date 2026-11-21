from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


from ai_platform.services import chat_execution as chat
from ai_platform.db import connection
from ai_platform.services import connection_tests as execution, metering
from ai_platform.settings import settings
from test_identity import ORIGIN, PASSWORD, post, sign_in
from test_models_metering import accept, enroll_admin, fake_supplier


def send(client, agent, conv, text, key=None):
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    return client.post(
        f"/api/v1/agents/{agent}/runs",
        json={"conversation_id": conv, "input": {"content": [{"type": "text", "text": text}]}},
        headers={"origin": ORIGIN, "x-csrf-token": csrf, "Idempotency-Key": key or str(uuid4())},
    )


def setup_chat(admin, member, endpoint, monkeypatch):
    monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", endpoint)
    settings.cache_clear()
    if not settings().redis_url:
        monkeypatch.setattr(metering, "redis_admission", lambda *_: (True, ""))
    enroll_admin(admin)
    invited = post(admin, "/admin/users", {"username": "member", "display_name": "Member"}).json()
    assert post(
        member, "/auth/activate", {"token": invited["activation_token"], "password": PASSWORD}
    ).is_success
    assert sign_in(member, "member", PASSWORD).is_success
    link = post(
        admin, "/admin/model-connections", {"name": "fixture", "base_url": endpoint, "api_key": "fake"}
    ).json()["id"]
    model = post(
        admin,
        f"/admin/model-connections/{link}/models",
        {
            "upstream_model": "okay",
            "display_name": "文本模型",
            "context_limit_tokens": 2048,
            "max_output_tokens": 128,
            "supports_temperature": False,
            "usage_support": "unknown",
        },
    ).json()
    assert accept(admin, link, model["id"]).is_success
    assert execution.run_once("test")
    return link, model["id"]


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_chat_snapshot_recovery_isolation(graph_db, monkeypatch):
    with (
        graph_db() as admin,
        graph_db() as member,
        graph_db() as peer,
        fake_supplier() as (endpoint, requests),
    ):
        link, model = setup_chat(admin, member, endpoint, monkeypatch)
        invitation = post(admin, "/admin/users", {"username": "peer", "display_name": "Peer"}).json()
        assert post(
            peer, "/auth/activate", {"token": invitation["activation_token"], "password": PASSWORD}
        ).is_success
        assert sign_in(peer, "peer", PASSWORD).is_success
        agent = post(
            member, "/agents", {"name": "assistant", "model_definition_id": model, "history_max_messages": 2}
        ).json()["id"]
        assert post(member, f"/agents/{agent}/publish", {"revision": 1}).is_success
        path = f"/agents/{agent}/conversations"
        conv = post(member, path, {"agent_revision": 1}).json()
        cid = conv["id"]
        key = str(uuid4())
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: send(member, agent, cid, "first", key), range(2)))
        assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
        run = responses[0].json()["id"]
        assert responses[1].json()["id"] == run
        assert send(member, agent, cid, "changed", key).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert send(member, agent, cid, "busy").json()["error"]["code"] == "CONVERSATION_BUSY"
        for resource in (
            f"/conversations/{cid}",
            f"/conversations/{cid}/messages",
            f"/runs/{run}",
            f"/runs/{run}/events",
        ):
            for outsider in (admin, peer):
                assert outsider.get("/api/v1" + resource).status_code == 404, resource
        for suffix in ("", "/versions"):
            assert peer.get(f"/api/v1/agents/{agent}{suffix}").status_code == 404
            assert admin.get(f"/api/v1/agents/{agent}{suffix}").is_success
        assert peer.get("/api/v1/agents").json()["items"] == []
        assert peer.get("/api/v1/conversations").json()["items"] == []
        assert post(peer, f"/runs/{run}/cancel").status_code == 404
        assert send(peer, agent, cid, "cross").status_code == 404
        assert post(admin, f"/runs/{run}/cancel").status_code == 404
        assert send(admin, agent, cid, "cross").status_code == 404
        assert chat.run_once("worker")
        assert member.get(f"/api/v1/runs/{run}").json()["status"] == "completed"
        assert post(
            admin, f"/admin/models/{model}", {"revision": 1, "upstream_model": "new"}, method="PATCH"
        ).is_success
        assert post(member, path, {"agent_revision": 1}).status_code == 404
        assert accept(admin, link, model).is_success and execution.run_once("test")
        next_conv = post(member, path, {"agent_revision": 1}).json()
        assert next_conv["agent_version_id"] == conv["agent_version_id"]
        for prompt in ("second", "third"):
            run = send(member, agent, cid, prompt).json()["id"]
            assert chat.run_once("worker")
            assert member.get(f"/api/v1/runs/{run}").json()["status"] == "completed"
        assert requests[-1][2]["model"] == "okay"
        usage = member.get("/api/v1/usage/attempts").json()["items"]
        assert sorted(row["purpose"] for row in usage) == ["chat"] * 3 + ["context_summary"]
        messages = member.get(f"/api/v1/conversations/{cid}/messages").json()
        assert [r["content"][0]["text"] for r in messages["items"]] == [
            "first",
            "OK",
            "second",
            "OK",
            "third",
            "OK",
        ]
        before = len(requests)
        # Lose final business projection, resume from a committed graph checkpoint.
        with connection() as conn:
            conn.execute("UPDATE app.runs SET status='running',finished_at=NULL WHERE id=%s", (run,))
            conn.execute("UPDATE app.execution_jobs SET status='ready' WHERE run_id=%s", (run,))
        assert chat.run_once("successor")
        assert member.get(f"/api/v1/runs/{run}").json()["status"] == "completed"
        assert len(requests) == before == 6
        assert not chat.run_once("worker")
        cancelled = send(member, agent, cid, "stop").json()["id"]
        assert post(member, f"/runs/{cancelled}/cancel").json()["status"] == "cancelled"
        assert post(member, f"/runs/{cancelled}/cancel").status_code == 200
        assert len(requests) == 6
        print("chat=3 summary=1 connection_tests=2 recovery=0 history=0 cross_workspace=404")


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_publication_and_visibility(graph_db, monkeypatch):
    with graph_db() as admin, graph_db() as member, graph_db() as peer, fake_supplier() as (endpoint, calls):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        invited = post(admin, "/admin/users", {"username": "peer", "display_name": "Peer"}).json()
        assert post(peer, "/auth/activate", {"token": invited["activation_token"], "password": PASSWORD}).is_success
        assert sign_in(peer, "peer", PASSWORD).is_success
        created = post(member, "/agents", {"name": "first", "model_definition_id": model}).json()
        path = f"/agents/{created['id']}"
        assert created["visibility"] == "private" and created["is_creator"]
        assert created["creator_name"] == "Member" and created["published_version"] is None
        for actor in (member, admin):
            assert post(actor, path + "/conversations", {"agent_revision": 1}).status_code == 409
        for suffix, body in (("/publish", {"revision": 1}), ("/conversations", {"agent_revision": 1})):
            assert post(peer, path + suffix, body).status_code == 404
        assert peer.get("/api/v1" + path).status_code == 404
        assert post(peer, path, {"revision": 1, "name": "hidden"}, method="PATCH").status_code == 404
        public = post(admin, path, {"revision": 1, "visibility": "public"}, method="PATCH").json()
        assert public["can_manage"] and not public["is_creator"]
        assert public["creator_id"] == created["creator_id"]
        first = post(peer, path + "/publish", {"revision": 2}).json()
        assert first["published_version"] == 1 and not first["can_manage"]
        old = post(peer, path + "/conversations", {"agent_revision": 2}).json()
        edited = post(peer, path, {"revision": 2, "name": "next", "system_prompt": "new prompt"}, method="PATCH").json()
        assert edited["published_name"] == "first" and edited["published_revision"] == 2 and edited["revision"] == 3
        assert post(admin, path + "/publish", {"revision": 2}).status_code == 409
        assert post(peer, path, {"revision": 3, "visibility": "private"}, method="PATCH").status_code == 403
        assert post(peer, path, method="DELETE").status_code == 403
        before = post(member, path + "/conversations", {"agent_revision": 2}).json()
        assert before["agent_version_id"] == old["agent_version_id"]
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda actor: post(actor, path + "/publish", {"revision": 3}), (peer, admin)))
        assert [r.json()["published_version"] for r in results] == [2, 2]
        assert post(member, path + "/conversations", {"agent_revision": 2}).status_code == 409
        latest = post(peer, path + "/conversations", {"agent_revision": 3}).json()
        assert latest["release_version"] == 2 and latest["agent_name"] == "next"
        assert latest["agent_version_id"] != old["agent_version_id"]
        assert peer.get("/api/v1/conversations/" + old["id"]).json()["release_version"] == 1
        accepted = send(peer, created["id"], latest["id"], "queued").json()["id"]
        assert post(member, path, {"revision": 3, "visibility": "private"}, method="PATCH").is_success
        assert send(peer, created["id"], old["id"], "revoked").status_code == 404
        assert chat.run_once("revoked-worker") and len(calls) == 1
        assert peer.get("/api/v1/runs/" + accepted).json()["status"] == "failed"
        assert post(admin, path + "/conversations", {"agent_revision": 3}).is_success
        assert post(admin, path, method="DELETE").status_code == 204


def write_fragment(file, text):
    import json

    file.write(b"data: " + json.dumps({"choices": [{"delta": {"content": text}}]}).encode() + b"\n\n")
    file.flush()


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_writeback_archive(graph_db, monkeypatch, tmp_path):
    import sqlite3
    import threading
    import time
    from ai_platform.services.chat_context import cache
    from ai_platform.services.deletion import init_archive, retry_archives

    gates = [threading.Event() for _ in range(4)]

    def stream(file, payload):
        if payload["messages"][-1]["content"] != "batch":
            write_fragment(file, "OK")
            return
        for value, gate in zip(("first", "timer", "界" * 2730 + "ab", "tail"), gates):
            write_fragment(file, value)
            assert gate.wait(12)

    with graph_db() as admin, graph_db() as member, fake_supplier(stream) as (endpoint, requests):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        aid = post(member, "/agents", {"name": "test", "model_definition_id": model}).json()["id"]
        assert post(member, f"/agents/{aid}/publish", {"revision": 1}).is_success
        conv = post(member, f"/agents/{aid}/conversations", {"agent_revision": 1}).json()["id"]
        run = send(member, aid, conv, "batch").json()["id"]

        def snapshot():
            return member.get(f"/api/v1/runs/{run}").json()

        def wait_text(text, timeout=3):
            start = time.monotonic()
            while time.monotonic() - start < timeout:
                row = snapshot()
                if row["output_message"]["content"][0]["text"] == text:
                    return time.monotonic() - start
                time.sleep(0.01)
            pytest.fail("Missing committed prefix")

        with ThreadPoolExecutor(1) as workers:
            future = workers.submit(chat.run_once, "batch")
            try:
                wait_text("first")
                assert post(member, f"/conversations/{conv}", method="DELETE").status_code == 409
                gates[0].set()
                time.sleep(0.2)
                assert snapshot()["output_message"]["content"][0]["text"] == "first"
                elapsed = wait_text("firsttimer")
                gates[1].set()
                large = "firsttimer" + "界" * 2730 + "ab"
                size_delay = wait_text(large, 0.95)
                gates[2].set()
                time.sleep(0.1)
                assert snapshot()["status"] == "running"
                start = time.monotonic()
                gates[3].set()
                assert future.result(4)
                final_delay = time.monotonic() - start
                assert (
                    final_delay < 1 and snapshot()["output_message"]["content"][0]["text"] == large + "tail"
                )
                print(
                    f"timer_after_200ms={elapsed:.3f}s size_flush={size_delay:.3f}s terminal_flush={final_delay:.3f}s"
                )
            finally:
                for gate in gates:
                    gate.set()
        # Finished history can be replayed and expired history can be resynchronized.
        events = member.get(f"/api/v1/runs/{run}/events").text
        assert events.count("event: message.delta") == 4 and "event: message.snapshot" in events
        with connection() as conn:
            conn.execute(
                "UPDATE app.run_events SET created_at=now()-interval '8 days' WHERE run_id=%s", (run,)
            )
            conn.execute(
                "UPDATE app.runs SET created_at=now()-interval '31 minutes',finished_at=now()-interval '29 minutes' WHERE id=%s",
                (run,),
            )
            cache.collect(conn)
            assert any(str(key[1]) == conv for key in cache.entries)
            conn.execute(
                "UPDATE app.runs SET created_at=now()-interval '31 minutes',finished_at=now()-interval '30 minutes' WHERE id=%s",
                (run,),
            )
            cache.collect(conn)
        assert all(str(key[1]) != conv for key in cache.entries)
        assert member.get(f"/api/v1/runs/{run}/events").status_code == 410
        history = member.get(f"/api/v1/conversations/{conv}/messages").json()
        assert history["items"][-1]["content"][0]["text"] == large + "tail"
        assert member.get(f"/api/v1/runs/{run}/events?after={run}:{history['run_cursors'][run]}").text == ""
        # Resume from an evicted cache with a fresh graph reader.
        resumed = send(member, aid, conv, "after idle").json()["id"]
        assert (
            chat.run_once("fresh") and member.get(f"/api/v1/runs/{resumed}").json()["status"] == "completed"
        )
        archive = tmp_path / "mount" / "deletions.sqlite3"
        monkeypatch.setenv("DELETION_ARCHIVE_PATH", str(archive))
        settings.cache_clear()
        response = post(member, f"/conversations/{conv}", method="DELETE")
        assert response.status_code == 503 and response.json()["error"]["retryable"]
        assert admin.get(f"/api/v1/conversations/{conv}").status_code == 404
        assert post(admin, f"/conversations/{conv}", method="DELETE").status_code == 404
        assert member.get(f"/api/v1/runs/{run}").status_code == 404
        assert member.get(f"/api/v1/conversations/{conv}/messages").status_code == 404
        assert send(member, aid, conv, "hidden").status_code == 404
        import os
        from pathlib import Path

        if retained := os.getenv("TEST_DELETION_ARCHIVE_PATH"):
            archive = Path(retained)
            assert archive.is_absolute()
            monkeypatch.setenv("DELETION_ARCHIVE_PATH", str(archive))
            settings.cache_clear()
        archive.parent.mkdir(parents=True, exist_ok=True)
        init_archive()
        retry_archives()
        retry_archives()
        assert post(member, f"/conversations/{conv}", method="DELETE").status_code == 202
        with sqlite3.connect(archive) as store:
            assert store.execute(
                "SELECT count(*) FROM deletions WHERE conversation_id=?", (conv,)
            ).fetchone() == (1,)
        assert len(member.get("/api/v1/usage/attempts").json()["items"]) == 2
        assert len(requests) == 3
        print(
            "first=immediate threshold_bytes=8192 terminal=completed idle=evicted archive=503,202 requests=3"
        )
