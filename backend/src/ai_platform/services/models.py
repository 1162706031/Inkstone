"""Model connections, model definitions, connection tests, quotas and usage reporting."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import psycopg

from ai_platform.clients import provider
from ai_platform.repositories import metering as metering_repo
from ai_platform.repositories import models as repo
from ai_platform.repositories import runs
from ai_platform.schemas import models as s
from ai_platform.security import decrypt, digest, encrypt
from ai_platform.services import metering
from ai_platform.services.clock import now
from ai_platform.services.common import audit, require, workspace
from ai_platform.services.dispatch import aad, event
from ai_platform.services.errors import Conflict, InvalidInput, UpstreamFailed
from ai_platform.services.pagination import parse_cursor, to_page
from ai_platform.settings import settings

DISCOVERY_ERRORS = {
    "UPSTREAM_UNAUTHORIZED": "Provider rejected the API key",
    "ENDPOINT_FORBIDDEN": "Provider endpoint is not allowed",
    "ENDPOINT_UNAVAILABLE": "Provider endpoint is unreachable",
    "UPSTREAM_TIMEOUT": "Provider did not respond in time",
}
REQUEST_FIELDS = ("upstream_model", "context_limit_tokens", "max_output_tokens", "supports_temperature")


def secret_fields(key, connection_id, endpoint):
    sealed, version = encrypt(key, aad(connection_id, endpoint))
    return sealed[12:], sealed[:12], version


def connection_view(row):
    return s.ConnectionView.model_validate(row)


# --- connections -------------------------------------------------------------

def list_connections(conn, cursor, limit):
    return to_page(repo.list_connections(conn, parse_cursor(cursor), limit), limit, s.ConnectionView)


def create_connection(conn, user, data, request_id):
    try:
        endpoint = provider.normalize_endpoint(data.base_url)
    except provider.ProviderFailure as exc:
        raise InvalidInput("Invalid provider endpoint") from exc
    connection_id, secret_id = uuid4(), uuid4()
    repo.insert_secret(conn, secret_id, *secret_fields(data.api_key, connection_id, endpoint))
    row = repo.insert_connection(conn, connection_id, data.name, endpoint, data.protocol, secret_id)
    audit(conn, request_id, user, "connection.created", connection_id, "model_connection")
    return connection_view(row)


def get_connection(conn, connection_id):
    return connection_view(require(repo.get_connection(conn, connection_id)))


def update_connection(conn, user, connection_id, data, request_id):
    existing = require(repo.get_connection(conn, connection_id, lock=True))
    if existing["revision"] != data.revision:
        raise Conflict("Connection revision conflict")
    version = existing["credential_version"]
    if data.api_key is not None:
        version += 1
        repo.update_secret(conn, existing["credential_id"],
                           *secret_fields(data.api_key, connection_id, existing["base_url"]), version)
        repo.revoke_strict_budget(conn, connection_id)
    row = repo.update_connection(conn, connection_id, data.name, data.status, version)
    audit(conn, request_id, user, "connection.updated", connection_id, "model_connection")
    return connection_view(row)


def delete_connection(conn, user, connection_id, request_id):
    row = require(repo.get_connection(conn, connection_id, lock=True))
    try:
        repo.delete_connection(conn, connection_id)
    except psycopg.errors.ForeignKeyViolation as exc:
        raise Conflict("Connection is in use") from exc
    repo.delete_secret(conn, row["credential_id"])
    audit(conn, request_id, user, "connection.deleted", connection_id, "model_connection")


# --- model definitions -------------------------------------------------------

def list_models(conn, connection_id, cursor, limit):
    require(repo.get_connection(conn, connection_id))
    return to_page(repo.list_models(conn, connection_id, parse_cursor(cursor), limit), limit, s.ModelView)


def create_model(conn, user, connection_id, data, request_id):
    require(repo.get_connection(conn, connection_id, lock=True))
    try:
        row = repo.insert_model(conn, connection_id, data.upstream_model, data.display_name,
                                data.context_limit_tokens, data.max_output_tokens, data.supports_temperature,
                                data.usage_support)
    except psycopg.errors.UniqueViolation as exc:
        raise Conflict("Model already exists") from exc
    audit(conn, request_id, user, "model.created", row["id"], "model_definition")
    return s.ModelView.model_validate({**row, "last_test_status": "untested"})


def discovery_source(conn, connection_id):
    """Read what discovery needs inside the request transaction; the provider call happens afterwards."""
    row = require(repo.connection_secret(conn, connection_id))
    return row, repo.known_upstream_models(conn, connection_id)


async def _discover(endpoint, key):
    return await provider.list_models(await provider.preflight(endpoint), key)


def discover_models(row, known):
    key = decrypt(row["nonce"] + row["ciphertext"], row["key_version"], aad(row["id"], row["base_url"]))
    try:
        found = asyncio.run(_discover(row["base_url"], key))
    except provider.ProviderFailure as exc:
        raise UpstreamFailed(DISCOVERY_ERRORS.get(exc.code, f"Provider model listing failed ({exc.code})")) from exc
    return s.Discovery(items=[
        s.DiscoveredModel(upstream_model=m["id"], context_limit_tokens=m["context_length"],
                          registered=m["id"] in known) for m in found
    ])


def import_models(conn, user, connection_id, data, request_id):
    require(repo.get_connection(conn, connection_id, lock=True))
    created = 0
    for item in {m.upstream_model: m for m in data.models}.values():
        context = item.context_limit_tokens or 8192
        output = min(4096, max(1, context // 4))
        row = repo.insert_model_if_absent(conn, connection_id, item.upstream_model,
                                          item.display_name or item.upstream_model[:80], context, output)
        if row:
            created += 1
            audit(conn, request_id, user, "model.created", row["id"], "model_definition")
    return s.ImportResult(created=created, skipped=len(data.models) - created)


def update_model(conn, user, model_id, data, request_id):
    existing = require(repo.lock_model(conn, model_id))
    if existing["revision"] != data.revision:
        raise Conflict("Model revision conflict")
    fields = data.model_dump(exclude_unset=True, exclude={"revision"})
    updated = {**existing, **fields}
    if updated["max_output_tokens"] >= updated["context_limit_tokens"]:
        raise InvalidInput("Output must leave room for input")
    changed = any(updated[key] != existing[key] for key in REQUEST_FIELDS)
    try:
        repo.update_model(conn, model_id, *(updated[key] for key in REQUEST_FIELDS), updated["display_name"],
                          updated["status"], changed)
    except psycopg.errors.UniqueViolation as exc:
        raise Conflict("Model already exists") from exc
    audit(conn, request_id, user, "model.updated", model_id, "model_definition")
    return s.ModelView.model_validate(require(repo.get_model(conn, model_id)))


def available_models(conn, user, cursor, limit):
    workspace(conn, user["id"])
    return to_page(repo.list_available_models(conn, parse_cursor(cursor), limit), limit, s.AvailableModelView)


# --- connection tests --------------------------------------------------------

def start_connection_test(conn, user, connection_id, model_id, idempotency_key, request_id):
    owner = workspace(conn, user["id"])
    key = (owner, user["id"], connection_id, digest(idempotency_key))
    request_hash = digest(str(model_id))
    prior = runs.find_idempotency(conn, "connection_test", *key, lock=True)
    if prior and prior["expires_at"] > now():
        if prior["request_hash"] != request_hash:
            raise Conflict("Idempotency key is already bound to a different model")
        return s.RunView.model_validate(require(runs.get_run(conn, prior["run_id"])))
    if prior:
        runs.delete_idempotency(conn, prior["id"])
    model = require(repo.lock_test_target(conn, connection_id, model_id))
    if model["status"] != "active" or model["connection_status"] != "active":
        raise Conflict("Model is disabled")
    run = uuid4()
    runs.insert_test_run(conn, run, owner, user["id"])
    runs.insert_job(conn, run)
    repo.insert_test_request(conn, run, model_id, model["request_config_generation"], model["credential_version"])
    runs.insert_idempotency(conn, "connection_test", *key, request_hash, run)
    event(conn, run, "run.queued", {"status": "queued"})
    audit(conn, request_id, user, "connection.test_started", connection_id, "model_connection")
    return s.RunView.model_validate(require(runs.get_run(conn, run)))


# --- quotas and usage --------------------------------------------------------

def quota_view(row):
    config = settings()
    return s.QuotaView(**row, default_requests_per_minute=config.model_workspace_requests_per_minute,
                       default_max_concurrent=config.model_workspace_max_concurrent,
                       global_max_concurrent=config.model_global_max_concurrent)


def get_quota(conn, user_id):
    return quota_view(metering.policy(conn, workspace(conn, user_id)))


def update_quota(conn, actor, user_id, data, request_id):
    owner = workspace(conn, user_id)
    prior = metering.policy(conn, owner, lock=True)
    if prior["revision"] != data.revision:
        raise Conflict("Quota revision conflict")
    # Omitted overrides survive updates from older clients. An explicit null restores the deployment default.
    changes = data.model_dump(exclude_unset=True)
    row = metering_repo.update_policy(conn, owner, data.monthly_token_limit, data.mode,
                                      changes.get("requests_per_minute", prior["requests_per_minute"]),
                                      changes.get("max_concurrent", prior["max_concurrent"]))
    start, _ = metering.month_bounds()
    metering_repo.set_bucket_limit(conn, owner, start, data.monthly_token_limit)
    audit(conn, request_id, actor, "quota.updated", user_id, "user")
    return quota_view(row)


def month_bounds(month):
    try:
        return metering.month_bounds(month)
    except ValueError as exc:
        raise InvalidInput("Invalid month") from exc


def usage_summary(conn, user, start, end):
    owner = workspace(conn, user["id"])
    policy = metering.policy(conn, owner)
    totals = metering_repo.usage_totals(conn, owner, start) or {
        "limit_tokens": policy["monthly_token_limit"], "settled_tokens": 0,
        "reserved_tokens": 0, "uncertain": 0, "unknown": 0, "unpriced": 0}
    return s.UsageSummary(
        period_start=start, period_end=end, mode=policy["mode"], token_limit=totals["limit_tokens"],
        confirmed_tokens=totals["settled_tokens"], reserved_tokens=totals["reserved_tokens"]-totals["uncertain"],
        uncertain_reserved_tokens=totals["uncertain"], unknown_attempts=totals["unknown"],
        remaining_tokens=max(0, totals["limit_tokens"]-totals["settled_tokens"]-totals["reserved_tokens"]),
        estimated_costs=[], unpriced_attempts=totals["unpriced"],
    )


def attempt_range(start, end):
    end = end or now()
    start = start or end-timedelta(days=90)
    if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end-start <= timedelta(days=90):
        raise InvalidInput("Invalid usage date range")
    return start, end


def list_attempts(conn, user, start, end, status, model_definition_id, cursor, limit):
    owner = workspace(conn, user["id"])
    rows = metering_repo.list_attempts(conn, owner, start, end, status, model_definition_id,
                                       parse_cursor(cursor), limit)
    return to_page(rows, limit, s.AttemptView)
