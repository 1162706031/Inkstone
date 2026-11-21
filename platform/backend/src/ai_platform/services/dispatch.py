"""Model-call dispatch steps shared by connection tests and chat runs."""

from uuid import uuid4

from psycopg import Rollback

from ai_platform.repositories import runs
from ai_platform.services import metering


def event(conn, run, kind, payload):
    return runs.append_event(conn, run, kind, payload)


def locked_job(conn, job, worker):
    return runs.locked_job(conn, job["run_id"], worker, job["fencing_token"])


def aad(connection_id, endpoint):
    return f"provider-key:{connection_id}:{endpoint}"


def reserve_call(conn, job, model, purpose, deadline, estimate):
    """Record a dispatching call and reserve its quota; returns ``((call, attempt), error)``."""
    allowed, reason = metering.admission(conn, job["workspace_id"])
    if not allowed:
        return None, reason
    call, attempt = uuid4(), uuid4()
    with conn.transaction() as transaction:
        runs.insert_call(conn, call, job["workspace_id"], job["run_id"], model["id"], purpose)
        runs.insert_attempt(conn, attempt, call, job["workspace_id"], model["credential_version"], deadline)
        error = metering.reserve(conn, job["workspace_id"], attempt, estimate, deadline, model["strict_budget_eligible"])
        if error:
            raise Rollback(transaction)
    return (call, attempt), error
