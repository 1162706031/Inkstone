import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ai_platform.api.main import app
from ai_platform.services import chat_execution as execution
from ai_platform.services.deletion import init_archive, retry_archives
from ai_platform.db import connection
from ai_platform.settings import settings
from test_chat import setup_chat, write_fragment
from test_models_metering import fake_supplier


@pytest.mark.skipif(
    os.getenv("WORKBENCH_BROWSER") != "1", reason="Requires a built frontend and Playwright Chromium"
)
@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_live_browser(graph_db, monkeypatch, tmp_path):
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    dist = frontend / "dist"
    assert (dist / "index.html").is_file()
    stop = threading.Event()

    def stream(file, payload):
        prompt = payload["messages"][-1]["content"]
        if prompt == "重连测试":
            with connection() as conn:
                conn.execute("""UPDATE app.run_events SET created_at=now()-interval '8 days' WHERE type='run.queued'
                    AND run_id IN (SELECT id FROM app.runs WHERE kind='chat' AND status='running')""")
            write_fragment(file, "首段")
            time.sleep(3)
            write_fragment(file, "与尾段")
        elif prompt == "取消测试":
            write_fragment(file, "已保存部分")
            stop.wait(10)
        else:
            write_fragment(file, "OK")

    async def index(request):
        return FileResponse(dist / "index.html")

    routes = [Mount("/assets", app=StaticFiles(directory=dist / "assets"))]
    routes.extend(
        Route(path, index) for path in ("/", "/login", "/settings/security", "/agents", "/agents/{id}",
                                        "/chat", "/chat/{id}", "/usage")
    )
    routes.append(Mount("/", app=app))
    archive = tmp_path / "volume" / "markers.sqlite3"

    def poll():
        while not stop.wait(0.05):
            execution.run_once("browser-worker")
            with connection() as conn:
                pending = conn.execute(
                    "SELECT 1 FROM app.conversation_deletion_markers WHERE archived_at IS NULL"
                ).fetchone()
            if pending and not archive.parent.exists():
                # Restore the missing test volume after the API has returned the pending result.
                time.sleep(0.5)
                archive.parent.mkdir()
                init_archive()
                retry_archives()

    with (
        graph_db() as admin,
        graph_db() as member,
        fake_supplier(stream) as (endpoint, requests),
        socket.socket() as sock,
    ):
        setup_chat(admin, member, endpoint, monkeypatch)
        sock.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        monkeypatch.setenv("APP_ORIGIN", origin)
        monkeypatch.setenv("DELETION_ARCHIVE_PATH", str(archive))
        settings.cache_clear()
        server = uvicorn.Server(uvicorn.Config(Starlette(routes=routes), log_level="warning"))
        http = threading.Thread(target=server.run, kwargs={"sockets": [sock]})
        worker = threading.Thread(target=poll)
        http.start()
        worker.start()
        try:
            node = shutil.which("node")
            assert node
            result = subprocess.run(
                [node, "node_modules/@playwright/test/cli.js", "test", "--workers=1"],
                cwd=frontend,
                env={**os.environ, "WORKBENCH_TEST_ORIGIN": origin},
                timeout=130,
            )
            assert result.returncode == 0
            assert len(requests) == 6
            print("chromium live: connection_test=1 chat=4 context_summary=1 history_reconnect_delete=0")
        finally:
            stop.set()
            server.should_exit = True
            worker.join(15)
            http.join(15)
