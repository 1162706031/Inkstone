from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from ai_platform.api.deps import Cursor, Limit, authenticated
from ai_platform.schemas import models as s
from ai_platform.services import models as service

router = APIRouter(prefix="/api/v1")


@router.get("/admin/model-connections", operation_id="listConnections")
def list_connections(request: Request, limit: Limit = 20, cursor: Cursor = None) -> s.Page[s.ConnectionView]:
    with authenticated(request, admin=True) as (conn, _, _):
        return service.list_connections(conn, cursor, limit)


@router.post("/admin/model-connections", operation_id="createConnection", status_code=201)
def create_connection(data: s.ConnectionCreate, request: Request) -> s.ConnectionView:
    with authenticated(request, admin=True) as (conn, _, user):
        return service.create_connection(conn, user, data, request.state.request_id)


@router.get("/admin/model-connections/{connection_id}", operation_id="getConnection")
def get_connection(connection_id: UUID, request: Request) -> s.ConnectionView:
    with authenticated(request, admin=True) as (conn, _, _):
        return service.get_connection(conn, connection_id)


@router.patch("/admin/model-connections/{connection_id}", operation_id="updateConnection")
def update_connection(connection_id: UUID, data: s.ConnectionPatch, request: Request) -> s.ConnectionView:
    with authenticated(request, admin=True) as (conn, _, user):
        return service.update_connection(conn, user, connection_id, data, request.state.request_id)


@router.delete("/admin/model-connections/{connection_id}", operation_id="deleteConnection", status_code=204)
def delete_connection(connection_id: UUID, request: Request) -> None:
    with authenticated(request, admin=True) as (conn, _, user):
        service.delete_connection(conn, user, connection_id, request.state.request_id)


@router.get("/admin/model-connections/{connection_id}/models", operation_id="listConnectionModels")
def list_models(connection_id: UUID, request: Request, limit: Limit = 20, cursor: Cursor = None) -> s.Page[s.ModelView]:
    with authenticated(request, admin=True) as (conn, _, _):
        return service.list_models(conn, connection_id, cursor, limit)


@router.post("/admin/model-connections/{connection_id}/models", operation_id="createModel", status_code=201)
def create_model(connection_id: UUID, data: s.ModelCreate, request: Request) -> s.ModelView:
    with authenticated(request, admin=True) as (conn, _, user):
        return service.create_model(conn, user, connection_id, data, request.state.request_id)


@router.get("/admin/model-connections/{connection_id}/discover", operation_id="discoverModels")
def discover_models(connection_id: UUID, request: Request) -> s.Discovery:
    """List models the provider reports via ``GET {base_url}/models``."""
    with authenticated(request, admin=True) as (conn, _, _):
        row, known = service.discovery_source(conn, connection_id)
    return service.discover_models(row, known)


@router.post("/admin/model-connections/{connection_id}/models/import", operation_id="importModels")
def import_models(connection_id: UUID, data: s.ModelImport, request: Request) -> s.ImportResult:
    """Register several upstream models at once; existing identifiers are skipped."""
    with authenticated(request, admin=True) as (conn, _, user):
        return service.import_models(conn, user, connection_id, data, request.state.request_id)


@router.patch("/admin/models/{model_id}", operation_id="updateModel")
def update_model(model_id: UUID, data: s.ModelPatch, request: Request) -> s.ModelView:
    with authenticated(request, admin=True) as (conn, _, user):
        return service.update_model(conn, user, model_id, data, request.state.request_id)


@router.get("/models", operation_id="listAvailableModels")
def available_models(request: Request, cursor: Cursor = None, limit: Limit = 20) -> s.Page[s.AvailableModelView]:
    with authenticated(request) as (conn, _, user):
        return service.available_models(conn, user, cursor, limit)


@router.post("/admin/model-connections/{connection_id}/test", operation_id="testConnection", status_code=202)
def test_connection(
    connection_id: UUID, data: s.ModelSelection, request: Request,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> s.RunView:
    with authenticated(request, admin=True) as (conn, _, user):
        return service.start_connection_test(conn, user, connection_id, data.model_definition_id,
                                             idempotency_key, request.state.request_id)


@router.get("/admin/users/{user_id}/quota", operation_id="getQuota")
def get_quota(user_id: UUID, request: Request) -> s.QuotaView:
    with authenticated(request, admin=True) as (conn, _, _):
        return service.get_quota(conn, user_id)


@router.patch("/admin/users/{user_id}/quota", operation_id="updateQuota")
def update_quota(user_id: UUID, data: s.QuotaPatch, request: Request) -> s.QuotaView:
    with authenticated(request, admin=True) as (conn, _, user):
        return service.update_quota(conn, user, user_id, data, request.state.request_id)


@router.get("/usage/summary", operation_id="getUsageSummary")
def summary(request: Request, month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None) -> s.UsageSummary:
    start, end = service.month_bounds(month)
    with authenticated(request) as (conn, _, user):
        return service.usage_summary(conn, user, start, end)


@router.get("/usage/attempts", operation_id="listUsageAttempts")
def attempts(
    request: Request, cursor: Cursor = None, limit: Limit = 20,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    status: Annotated[str | None, Query(max_length=40)] = None,
    model_definition_id: UUID | None = None,
) -> s.Page[s.AttemptView]:
    start, end = service.attempt_range(start, end)
    with authenticated(request) as (conn, _, user):
        return service.list_attempts(conn, user, start, end, status, model_definition_id, cursor, limit)
