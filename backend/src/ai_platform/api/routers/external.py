"""The ``/openapi/v1`` surface for external systems holding a member-level access key.

Every handler authenticates a Bearer token instead of a session cookie, and runs as the key's creator so
conversations, messages and usage land in that member's workspace exactly as they would from the workbench.
"""

import asyncio
import json
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from starlette.responses import StreamingResponse

from ai_platform.api.deps import Cursor, Limit, key_authenticated
from ai_platform.api.routers.chat import ChatRoute
from ai_platform.schemas import access_keys as k
from ai_platform.schemas import chat as s
from ai_platform.schemas.models import Page, RunView
from ai_platform.services import chat as service
from ai_platform.services.errors import ServiceError

router = APIRouter(prefix="/openapi/v1", route_class=ChatRoute, tags=["external"])


@router.post("/agents/{agent_id}/conversations", status_code=201, operation_id="createExternalConversation")
def create_conversation(agent_id: UUID, data: k.ExternalConversation, request: Request) -> s.Conversation:
    with key_authenticated(request, agent_id) as (conn, key, user):
        source = (key["id"], data.external_user_id)
        return service.create_conversation(conn, user, agent_id, data, source)


@router.get("/agents/{agent_id}/conversations", operation_id="listExternalConversations")
def conversations(
    agent_id: UUID,
    request: Request,
    external_user_id: Annotated[str, Query(min_length=1, max_length=200)],
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[s.Conversation]:
    with key_authenticated(request, agent_id) as (conn, key, user):
        return service.list_conversations(conn, user, agent_id, cursor, limit, (key["id"], external_user_id))


@router.get("/conversations/{conversation_id}/messages", operation_id="getExternalMessages")
def messages(
    conversation_id: UUID,
    request: Request,
    external_user_id: Annotated[str, Query(min_length=1, max_length=200)],
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Limit = 20,
) -> s.HistoryPage:
    with key_authenticated(request) as (conn, key, user):
        return service.history(conn, user, conversation_id, cursor, limit, (key["id"], external_user_id))


@router.post("/agents/{agent_id}/runs", status_code=202, operation_id="createExternalRun")
def accept_chat(
    agent_id: UUID,
    data: k.ExternalRun,
    request: Request,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> RunView:
    with key_authenticated(request, agent_id) as (conn, key, user):
        source = (key["id"], data.external_user_id)
        return service.accept_chat(conn, user, agent_id, data, idempotency_key, source)


@router.get("/runs/{run_id}", operation_id="getExternalRun")
def get_run(run_id: UUID, request: Request) -> s.RunDetail:
    with key_authenticated(request) as (conn, key, user):
        return service.get_run(conn, user, run_id, (key["id"], None))


@router.post("/runs/{run_id}/cancel", status_code=202, operation_id="cancelExternalRun")
def cancel(run_id: UUID, request: Request, response: Response) -> RunView:
    with key_authenticated(request) as (conn, key, user):
        run, accepted = service.cancel_run(conn, user, run_id, (key["id"], None))
        if not accepted:
            response.status_code = 200
        return run


def read_events(request, run_id, sequence):
    with key_authenticated(request) as (conn, key, user):
        return service.read_events(conn, user, run_id, sequence, (key["id"], None))


@router.get("/runs/{run_id}/events", response_class=StreamingResponse, operation_id="getExternalRunEvents")
async def events(run_id: UUID, request: Request, after: str | None = None):
    sequence = service.parse_event_cursor(request.headers.get("Last-Event-ID") or after, run_id)
    initial = await asyncio.to_thread(read_events, request, run_id, sequence)

    async def stream():
        current = sequence
        batch = initial
        heartbeat = time.monotonic() + 15
        while True:
            rows, done, last = batch
            for row in rows:
                current = row["sequence"]
                body = json.dumps(service.envelope(run_id, row))
                yield f"id: {run_id}:{current}\nevent: {row['type']}\ndata: {body}\n\n"
            if done and current >= last:
                return
            if time.monotonic() >= heartbeat:
                yield ": heartbeat\n\n"
                heartbeat = time.monotonic() + 15
            if len(rows) < 100:
                await asyncio.sleep(0.25)
            try:
                batch = await asyncio.to_thread(read_events, request, run_id, current)
            except ServiceError:
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
