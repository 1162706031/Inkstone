import asyncio
import json
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from starlette.responses import StreamingResponse

from ai_platform.api.deps import Cursor, Limit, authenticated
from ai_platform.schemas import access_keys as k
from ai_platform.schemas import chat as s
from ai_platform.schemas.models import Page, RunView
from ai_platform.services import access_keys as keys
from ai_platform.services import chat as service
from ai_platform.services import deletion
from ai_platform.services.errors import ServiceError


class ChatRoute(APIRoute):
    """Reject JSON bodies over 64 KiB before FastAPI parses them."""

    def get_route_handler(self):
        handler = super().get_route_handler()
        if not self.body_field:
            return handler

        async def bounded(request):
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 65536:
                    raise HTTPException(413, "JSON body exceeds 64 KiB")
                body.extend(chunk)

            async def receive():
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            return await handler(Request(request.scope, receive=receive))

        return bounded


router = APIRouter(prefix="/api/v1", route_class=ChatRoute)
deletion_router = APIRouter(prefix="/api/v1")


@router.get("/agents")
def agents(request: Request, cursor: Cursor = None, limit: Limit = 20) -> Page[s.AgentView]:
    with authenticated(request) as (conn, _, user):
        return service.list_agents(conn, user, cursor, limit)


@router.post("/agents", status_code=201)
def create_agent(data: s.AgentCreate, request: Request) -> s.AgentView:
    with authenticated(request) as (conn, _, user):
        return service.create_agent(conn, user, data)


@router.get("/agents/{agent_id}")
def agent(agent_id: UUID, request: Request) -> s.AgentView:
    with authenticated(request) as (conn, _, user):
        return service.agent_row(conn, user, agent_id)


@router.patch("/agents/{agent_id}")
def edit_agent(agent_id: UUID, data: s.AgentPatch, request: Request) -> s.AgentView:
    with authenticated(request) as (conn, _, user):
        return service.edit_agent(conn, user, agent_id, data)


@router.post("/agents/{agent_id}/validate")
def validate_agent(agent_id: UUID, request: Request):
    with authenticated(request) as (conn, _, user):
        return service.validate_agent(conn, user, agent_id)


@router.delete("/agents/{agent_id}", status_code=204)
def archive_agent(agent_id: UUID, request: Request):
    with authenticated(request) as (conn, _, user):
        service.archive_agent(conn, user, agent_id)


@router.get("/agents/{agent_id}/versions")
def versions(agent_id: UUID, request: Request, cursor: Cursor = None, limit: Limit = 20) -> Page[s.Version]:
    with authenticated(request) as (conn, _, user):
        return service.list_versions(conn, user, agent_id, cursor, limit)


@router.post("/agents/{agent_id}/publish")
def publish(agent_id: UUID, data: s.Publish, request: Request) -> s.AgentView:
    with authenticated(request) as (conn, _, user):
        return service.publish(conn, user, agent_id, data.revision)


@router.post("/agents/{agent_id}/conversations", status_code=201)
def create_conversation(agent_id: UUID, data: s.NewConversation, request: Request) -> s.Conversation:
    with authenticated(request) as (conn, _, user):
        return service.create_conversation(conn, user, agent_id, data)


@router.get("/conversations")
def conversations(
    request: Request, agent_id: UUID | None = None, cursor: Cursor = None, limit: Limit = 20
) -> Page[s.Conversation]:
    with authenticated(request) as (conn, _, user):
        return service.list_conversations(conn, user, agent_id, cursor, limit)


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, request: Request) -> s.Conversation:
    with authenticated(request) as (conn, _, user):
        return service.get_conversation(conn, user, conversation_id)


@router.patch("/conversations/{conversation_id}")
def rename(conversation_id: UUID, data: s.Rename, request: Request) -> s.Conversation:
    with authenticated(request) as (conn, _, user):
        return service.rename_conversation(conn, user, conversation_id, data.title)


@router.get("/conversations/{conversation_id}/messages")
def messages(
    conversation_id: UUID, request: Request, cursor: Annotated[int, Query(ge=0)] = 0, limit: Limit = 20
) -> s.HistoryPage:
    with authenticated(request) as (conn, _, user):
        return service.history(conn, user, conversation_id, cursor, limit)


@router.post("/agents/{agent_id}/runs", status_code=202)
def accept_chat(
    agent_id: UUID,
    data: s.NewRun,
    request: Request,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> RunView:
    with authenticated(request) as (conn, _, user):
        return service.accept_chat(conn, user, agent_id, data, idempotency_key)


@router.get("/runs/{run_id}", operation_id="getRun")
def get_run(run_id: UUID, request: Request) -> s.RunDetail:
    with authenticated(request) as (conn, _, user):
        return service.get_run(conn, user, run_id)


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel(run_id: UUID, request: Request, response: Response) -> RunView:
    with authenticated(request) as (conn, _, user):
        run, accepted = service.cancel_run(conn, user, run_id)
        if not accepted:
            response.status_code = 200
        return run


def read_events(request, run_id, sequence):
    with authenticated(request) as (conn, _, user):
        return service.read_events(conn, user, run_id, sequence)


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
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


@router.get("/access-keys")
def access_keys(request: Request, cursor: Cursor = None, limit: Limit = 20) -> Page[k.KeyView]:
    with authenticated(request) as (conn, _, user):
        return keys.list_keys(conn, user, cursor, limit)


@router.post("/access-keys", status_code=201)
def create_access_key(data: k.KeyCreate, request: Request) -> k.IssuedKey:
    """The response carries the only copy of the token; it is stored as a digest and cannot be read again."""
    with authenticated(request) as (conn, _, user):
        return keys.create_key(conn, user, data)


@router.get("/access-keys/{key_id}")
def access_key(key_id: UUID, request: Request) -> k.KeyView:
    with authenticated(request) as (conn, _, user):
        return keys.get_key(conn, user, key_id)


@router.patch("/access-keys/{key_id}")
def edit_access_key(key_id: UUID, data: k.KeyPatch, request: Request) -> k.KeyView:
    with authenticated(request) as (conn, _, user):
        return keys.update_key(conn, user, key_id, data)


@router.post("/access-keys/{key_id}/rotate")
def rotate_access_key(key_id: UUID, data: k.KeyRotate, request: Request) -> k.IssuedKey:
    with authenticated(request) as (conn, _, user):
        return keys.rotate_key(conn, user, key_id, data)


@deletion_router.delete("/conversations/{conversation_id}", status_code=202)
def delete_conversation(conversation_id: UUID, request: Request):
    with authenticated(request) as (conn, _, user):
        owner, marker = deletion.request_deletion(conn, user, conversation_id)
    deletion.finish_deletion(owner, conversation_id, marker)
    return {"status": "deleting"}
