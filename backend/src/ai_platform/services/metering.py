"""Monthly token quotas, reservations, settlement and model admission."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ai_platform.clients import redis_limits
from ai_platform.repositories import metering as repo
from ai_platform.settings import settings

CHINA = ZoneInfo("Asia/Shanghai")


def month_bounds(month=None):
    local = datetime.now(CHINA) if month is None else datetime.strptime(month, "%Y-%m").replace(tzinfo=CHINA)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
    return start.astimezone(UTC), end.astimezone(UTC)


def policy(conn, workspace, *, lock=False):
    if lock:
        repo.ensure_policy(conn, workspace)
    result = repo.get_policy(conn, workspace, lock=lock)
    return result or {"workspace_id": workspace, "mode": "soft", "monthly_token_limit": 1000000, "revision": 1}


def reserve(conn, workspace, attempt, estimate, deadline, strict_eligible):
    config = policy(conn, workspace, lock=True)
    if config["mode"] == "strict" and not strict_eligible:
        return "STRICT_BUDGET_UNSUPPORTED"
    start, end = month_bounds()
    repo.ensure_bucket(conn, workspace, start, end, config["monthly_token_limit"])
    bucket = repo.reserve_bucket(conn, workspace, start, estimate)
    if not bucket:
        return "MONTHLY_QUOTA_EXCEEDED"
    repo.insert_reservation(conn, attempt, workspace, bucket["id"], estimate, deadline)
    repo.insert_ledger(conn, workspace, bucket["id"], attempt, "reserve", estimate, "dispatch")
    return None


def settle(conn, attempt, usage):
    row = repo.lock_reservation(conn, attempt)
    if not row or row["state"] != "reserved":
        return False
    workspace, bucket = row["workspace_id"], row["bucket_id"]
    source = "unknown" if usage is None else "reported"
    first, second = usage or (None, None)
    normalized = Jsonb({"input_tokens": first, "output_tokens": second}) if usage else None
    repo.insert_usage(conn, attempt, workspace, first, second, normalized, source)
    amount = 0 if usage is None else first + second
    if usage is not None:
        repo.settle_bucket(conn, bucket, amount, row["reserved_tokens"])
    repo.set_reservation_state(conn, attempt, "uncertain" if usage is None else "settled")
    repo.insert_ledger(conn, workspace, bucket, attempt, "settle", amount, source)
    return True


def reconcile(conn, attempt, usage, reason):
    row = repo.lock_reservation(conn, attempt)
    if not row or row["state"] != "uncertain":
        return False
    workspace, bucket = row["workspace_id"], row["bucket_id"]
    first, second = usage
    amount = first + second
    repo.settle_bucket(conn, bucket, amount, row["reserved_tokens"])
    repo.report_usage(conn, attempt, first, second)
    repo.set_reservation_state(conn, attempt, "settled")
    repo.insert_ledger(conn, workspace, bucket, attempt, "adjustment", amount, reason)
    return True


def admission(conn, workspace):
    # The authoritative attempt rows restore Redis's rate and concurrency view before each decision.
    repo.lock_admission(conn)
    return redis_admission(workspace, repo.admission_counts(conn, workspace))


def redis_admission(workspace, counts):
    config = settings()
    url = config.redis_url
    if not url:
        return False, "REDIS_UNAVAILABLE"
    prefix = config.redis_prefix
    keys = (f"{prefix}:model:rate:{workspace}", f"{prefix}:model:active:{workspace}", f"{prefix}:model:active:global")
    allowed = redis_limits.model_admission(url, keys, (counts["recent"], counts["local"], counts["global"]))
    return allowed, "MODEL_ADMISSION_LIMIT"
