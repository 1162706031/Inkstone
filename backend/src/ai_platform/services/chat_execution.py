"""Worker side of chat runs: claim, stream the model reply, project it into messages and settle usage."""

import asyncio
import time
from datetime import timedelta

import psycopg
from redis.exceptions import RedisError

from ai_platform.clients import provider
from ai_platform.db import connection
from ai_platform.repositories import conversations as repo
from ai_platform.repositories import runs
from ai_platform.security import decrypt
from ai_platform.services import metering
from ai_platform.services.chat_context import ChatError, Context, cache, cost
from ai_platform.services.clock import now
from ai_platform.services.dispatch import aad, event, locked_job, reserve_call

class LeaseLost(Exception):
    pass


def guard(conn, job):
    if not locked_job(conn, job, job["worker"]):
        raise LeaseLost()
    return runs.run_status(conn, job["run_id"])


def emit_text(conn, job, text):
    # The absolute prefix and locked projection make a lost commit acknowledgement safe to retry.
    block = repo.lock_message_content(conn, job["output_message_id"])["content"][0]
    previous = block["text"]
    if previous == text:
        return
    if not text.startswith(previous):
        raise ChatError("PROJECTION_CONFLICT")
    refs = {"message_id": str(job["output_message_id"]), "block_id": block["block_id"]}
    if not previous:
        event(conn, job["run_id"], "message.started", refs)
    seq = event(conn, job["run_id"], "message.delta", {**refs, "operation": "append", "text": text[len(previous):]})
    repo.project_message(conn, job["output_message_id"], [{**block, "text": text}], seq)


def terminal(conn, job, status, error=None, text=None):
    if text is not None:
        emit_text(conn, job, text)
    run = job["run_id"]
    row = repo.set_message_status(conn, job["output_message_id"], status)
    seq = event(conn, run, "message.snapshot",
                {"message_id": str(job["output_message_id"]), "content": row["content"], "status": status})
    repo.set_projected_seq(conn, job["output_message_id"], seq)
    repo.complete_input(conn, job["input_message_id"])
    runs.finish_run(conn, run, status, error)
    repo.finish_chat_job(conn, run)
    event(conn, run, "run." + status, {"status": status, "error_code": error})
    repo.keep_recoverable_results(conn, run)
    if status != "completed":
        repo.rotate_thread(conn, job["conversation_id"], job["thread_id"])


def claim(conn, worker):
    job = repo.claim_chat_job(conn)
    if not job:
        return None
    run = job["run_id"]
    pending = repo.pending_attempt(conn, run)
    if pending:
        if pending["deadline_at"] > now():
            runs.defer_job(conn, run, pending["deadline_at"])
            return None
        settle(conn, job, pending["call"], pending["id"], None, "interrupted", "RESULT_UNKNOWN", None)
        terminal(conn, job, "interrupted", "RESULT_UNKNOWN")
        return None
    if job["status"] == "cancel_requested":
        terminal(conn, job, "cancelled")
        return None
    if not job["started_at"] and job["created_at"] + timedelta(minutes=5) <= now():
        terminal(conn, job, "failed", "QUEUE_TIMEOUT")
        return None
    job["worker"] = worker
    job["fencing_token"] += 1
    repo.lease_chat_job(conn, run, worker, job["fencing_token"])
    started = repo.start_chat_run(conn, run, job["fencing_token"])
    if not job["started_at"]:
        event(conn, run, "run.started", {"status": "running"})
    job["started_at"] = started["started_at"]
    job["deadline"] = job["started_at"] + timedelta(seconds=180)
    return job


def settle(conn, job, call, attempt, usage, state, error, result):
    metering.settle(conn, attempt, usage)
    repo.finish_chat_attempt(conn, attempt, state, error, result)
    runs.set_call_status(conn, call, state)
    event(conn, job["run_id"], "usage.updated",
          {"attempt_id": str(attempt), "usage_source": "reported" if usage else "unknown"})


def prior_result(conn, job, purpose):
    return repo.prior_result(conn, job["run_id"], purpose)


class WriteBuffer:
    def __init__(self, job, visible):
        self.job = job
        self.visible = visible
        self.text = ""
        self.pending = 0
        self.due = None
        self.first = True

    async def receive(self, fragment):
        self.text += fragment
        if not self.visible:
            return
        self.pending += len(fragment.encode())
        if self.due is None:
            self.due = time.monotonic() + 1
        if self.first or self.pending >= 8192:
            await self.flush()

    async def flush(self):
        while self.pending:
            try:
                with connection() as conn:
                    guard(conn, self.job)
                    emit_text(conn, self.job, self.text)
            except psycopg.OperationalError:
                if self.job["deadline"] <= now():
                    raise
                await asyncio.sleep(0.1)
            else:
                self.pending = 0
                self.due = None
                self.first = False


async def invoke(job, purpose, messages, output, metadata):
    endpoint = job["snapshot"]["base_url"]
    target = await provider.preflight(endpoint)
    with connection() as conn:
        runs.share_actor(conn, job["actor_user_id"])
        if guard(conn, job) == "cancel_requested":
            raise ChatError("CANCELLED")
        live = repo.live_chat_model(conn, job["actor_user_id"], job["model_definition_id"], job["conversation_id"],
                                    job["workspace_id"], endpoint)
        if not live:
            raise ChatError("MODEL_UNAVAILABLE")
        if live["request_config_generation"] != job["snapshot"]["request_config_generation"]:
            live["strict_budget_eligible"] = False
        key = decrypt(live["nonce"] + live["ciphertext"], live["key_version"], aad(live["connection_id"], endpoint))
        limit = 60 if purpose == "context_summary" else 120
        deadline = min(job["deadline"], now() + timedelta(seconds=limit))
        if deadline <= now():
            raise ChatError("RUN_TIMEOUT")
        ids, error = reserve_call(conn, job, live, purpose, deadline, 16 + output + sum(map(cost, messages)))
        if error:
            raise ChatError(error)
    buffer = WriteBuffer(job, purpose == "chat")
    request = asyncio.create_task(
        provider.request_text(
            target, key, job["snapshot"]["upstream_model"], messages, output,
            temperature=job["config"]["temperature"],
            timeout=max(0, (deadline - now()).total_seconds()),
            on_text=buffer.receive,
        )
    )
    cancelled = False
    heartbeat_at = time.monotonic() + 10
    try:
        while not request.done():
            delay = min(0.25, max(0, buffer.due - time.monotonic())) if buffer.due else 0.25
            await asyncio.wait([request], timeout=delay)
            if buffer.due and buffer.due <= time.monotonic():
                await buffer.flush()
            if not request.done():
                with connection() as conn:
                    cancelled = guard(conn, job) == "cancel_requested"
                    if time.monotonic() >= heartbeat_at:
                        runs.extend_lease(conn, job["run_id"])
                        heartbeat_at = time.monotonic() + 10
                if cancelled:
                    request.cancel()
                    break
        try:
            _, usage, error = await request
        except asyncio.CancelledError:
            usage, error = None, "CANCELLED"
    finally:
        if not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    uncertain = error in ("UPSTREAM_INTERRUPTED", "UPSTREAM_TIMEOUT", "UPSTREAM_PROTOCOL")
    with connection() as conn:
        cancelled = guard(conn, job) == "cancel_requested" or cancelled
        result = {**metadata, "text": buffer.text}
        state = "interrupted" if uncertain else ("failed" if error else "completed")
        settle(conn, job, *ids, usage, state, error, result)
        if purpose == "chat":
            job["tail"] = buffer.text
    if cancelled:
        raise ChatError("CANCELLED")
    if uncertain:
        raise ChatError("RESULT_UNKNOWN")
    if error:
        raise ChatError(error)
    return result


def restored(prior):
    if prior["state"] == "completed" and prior["recoverable_result"] is not None:
        return prior["recoverable_result"]
    if prior["state"] == "failed":
        raise ChatError(prior["error_code"])
    raise ChatError("RESULT_UNKNOWN")


def reply(job):
    with connection() as conn:
        guard(conn, job)
        prior = prior_result(conn, job, "chat")
        if prior:
            return restored(prior)
        ctx = Context.load(conn, job)
        summary_call = prior_result(conn, job, "context_summary")
    own_summary = ctx.summary and ctx.summary["run_id"] == job["run_id"]
    if not own_summary:
        request = ctx.summary_request(job["deadline"] - now())
        summary = None
        if summary_call and summary_call["state"] != "failed":
            summary = restored(summary_call)
        elif request and not summary_call:
            batch, output, through = request
            try:
                summary = asyncio.run(invoke(job, "context_summary", batch, output, {"covered_through_seq": through}))
            except ChatError as exc:
                if exc.code not in ("UPSTREAM_REJECTED", "MONTHLY_QUOTA_EXCEEDED", "MODEL_ADMISSION_LIMIT"):
                    raise
        if summary:
            with connection() as conn:
                if guard(conn, job) == "cancel_requested":
                    raise ChatError("CANCELLED")
                repo.insert_summary(conn, job["workspace_id"], job["conversation_id"], job["run_id"],
                                    summary["covered_through_seq"], ctx.summary["id"] if ctx.summary else None,
                                    summary["text"])
                ctx = Context.load(conn, job)
            own_summary = True
    messages, mode, plan = ctx.reply(own_summary)
    with connection() as conn:
        guard(conn, job)
        repo.set_compression(conn, job["run_id"], mode)
    return asyncio.run(invoke(job, "chat", messages, ctx.output, plan))


def run_once(worker):
    from ai_platform.services.chat_graph import execute_graph

    with connection() as conn:
        job = claim(conn, worker)
    if not job:
        return False
    try:
        text = execute_graph(job)
        status, error = "completed", None
    except LeaseLost:
        return True
    except (ChatError, provider.ProviderFailure, RedisError, OSError, RuntimeError) as exc:
        error = getattr(exc, "code", "DEPENDENCY_UNAVAILABLE")
        status = {"RESULT_UNKNOWN": "interrupted", "CANCELLED": "cancelled"}.get(error, "failed")
        text = job.get("tail")
    with connection() as conn:
        try:
            if guard(conn, job) == "cancel_requested":
                status, error = "cancelled", None
        except LeaseLost:
            return True
        terminal(conn, job, status, error, text)
    if status == "completed":
        cache.committed(job, text)
    return True
