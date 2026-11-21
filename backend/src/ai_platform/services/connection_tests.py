"""Worker loop that probes a model connection with a short chat completion."""

import asyncio
from datetime import timedelta

from redis.exceptions import RedisError

from ai_platform.clients import provider
from ai_platform.db import connection
from ai_platform.repositories import models as models_repo
from ai_platform.repositories import runs
from ai_platform.security import decrypt
from ai_platform.services import metering
from ai_platform.services.clock import now
from ai_platform.services.dispatch import aad, event, locked_job, reserve_call

TEST_PROMPT = [{"role": "user", "content": "Reply with OK."}]


def finish(conn, run, error, *, dispatched=False, interrupted=False, cancelled=False):
    status = "cancelled" if cancelled else ("interrupted" if interrupted else ("failed" if error else "completed"))
    runs.finish_run(conn, run, status, error)
    runs.finish_job(conn, run)
    if dispatched and not interrupted and not cancelled:
        models_repo.record_test_result(conn, run, "failed" if error else "passed")
    event(conn, run, f"run.{status}", {"status": status, "error_code": error})


def claim(conn, worker):
    job = runs.claim_test_job(conn)
    if not job:
        return None
    run = job["run_id"]
    prior = runs.prior_test_attempt(conn, run)
    if prior:
        if prior["deadline_at"] > now():
            runs.defer_job(conn, run, prior["deadline_at"])
            return None
        metering.settle(conn, prior["id"], None)
        runs.interrupt_attempt(conn, prior["id"])
        runs.set_call_status(conn, prior["model_call_id"], "interrupted")
        finish(conn, run, "RESULT_UNKNOWN", interrupted=True)
        return None
    if job["status"] == "cancel_requested":
        finish(conn, run, None, cancelled=True)
        return None
    if not job["started_at"] and job["created_at"] + timedelta(minutes=5) <= now():
        finish(conn, run, "QUEUE_TIMEOUT")
        return None
    job["fencing_token"] += 1
    runs.lease_test_job(conn, run, worker, job["fencing_token"])
    job["started_at"] = runs.start_test_run(conn, run, job["fencing_token"])["started_at"]
    event(conn, run, "run.running", {"status": "running"})
    return job


def current_model(conn, job):
    actor = runs.lock_actor_status(conn, job["actor_user_id"])
    model = runs.lock_test_model(conn, job["model_definition_id"])
    if actor["status"] != "active" or not model or model["status"] != "active" or model["connection_status"] != "active":
        return None
    if any(model[field] != job[field] for field in ("request_config_generation", "credential_version")):
        return None
    return model


def begin_dispatch(conn, job, worker, endpoint):
    runs.share_actor(conn, job["actor_user_id"])
    if not locked_job(conn, job, worker):
        return None
    run = job["run_id"]
    if runs.run_status(conn, run) == "cancel_requested":
        finish(conn, run, None, cancelled=True)
        return None
    model = current_model(conn, job)
    if not model or model["base_url"] != endpoint:
        finish(conn, run, "MODEL_CONFIGURATION_CHANGED")
        return None

    deadline = min(now() + timedelta(seconds=45), job["started_at"] + timedelta(seconds=90))
    if deadline <= now():
        finish(conn, run, "RUN_TIMEOUT")
        return None
    output = min(32, model["max_output_tokens"])
    estimate = len(TEST_PROMPT[0]["content"].encode()) + 16 + output
    if estimate > model["context_limit_tokens"]:
        finish(conn, run, "CONTEXT_LIMIT")
        return None

    key = decrypt(model["nonce"] + model["ciphertext"], model["key_version"], aad(model["connection_id"], endpoint))
    ids, error = reserve_call(conn, job, model, "connection_test", deadline, estimate)
    if error:
        finish(conn, run, error)
        return None
    runs.extend_lease(conn, run)
    return *ids, deadline, model["upstream_model"], output, key


def heartbeat(job, worker):
    with connection() as conn:
        if not locked_job(conn, job, worker):
            return False
        runs.extend_lease(conn, job["run_id"], touch=True)
        return True


async def call_with_heartbeat(job, worker, target, key, model, output, deadline):
    request = asyncio.create_task(provider.request_text(
        target, key, model, TEST_PROMPT, output, timeout=max(0, (deadline-now()).total_seconds()),
        probe=True,
    ))
    next_heartbeat = asyncio.get_running_loop().time() + 10
    try:
        while not request.done():
            ready, _ = await asyncio.wait((request,), timeout=.25)
            if ready:
                break
            with connection() as conn:
                if not locked_job(conn, job, worker):
                    return None
                if runs.run_status(conn, job["run_id"]) == "cancel_requested":
                    request.cancel()
                    break
            if asyncio.get_running_loop().time() >= next_heartbeat:
                if not await asyncio.to_thread(heartbeat, job, worker):
                    return None
                next_heartbeat = asyncio.get_running_loop().time() + 10
        return await request
    finally:
        if not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)


def run_once(worker):
    with connection() as conn:
        job = claim(conn, worker)
        if not job:
            return False
        endpoint = models_repo.connection_endpoint(conn, job["model_definition_id"])
    try:
        target = asyncio.run(provider.preflight(endpoint))
        with connection() as conn:
            dispatch = begin_dispatch(conn, job, worker, endpoint)
    except (provider.ProviderFailure, RedisError, OSError, RuntimeError) as exc:
        with connection() as conn:
            if locked_job(conn, job, worker):
                finish(conn, job["run_id"], getattr(exc, "code", "DEPENDENCY_UNAVAILABLE"))
        return True
    if not dispatch:
        return True
    call, attempt, deadline, model, output, key = dispatch
    result = asyncio.run(call_with_heartbeat(job, worker, target, key, model, output, deadline))
    if result is None:
        return True
    _, usage, error = result
    with connection() as conn:
        if locked_job(conn, job, worker):
            metering.settle(conn, attempt, usage)
            state = "failed" if error else "completed"
            runs.finish_test_attempt(conn, attempt, state, error)
            runs.set_call_status(conn, call, state, touch=True)
            cancelled = runs.run_status(conn, job["run_id"]) == "cancel_requested"
            finish(conn, job["run_id"], error, dispatched=True, cancelled=cancelled)
    return True
