"""Authentication, sessions, MFA and member lifecycle rules."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hmac import compare_digest
from uuid import UUID

import psycopg
import pyotp

from ai_platform.clients import redis_limits
from ai_platform.repositories import identity as repo
from ai_platform.repositories.common import active_workspace
from ai_platform.schemas import identity as s
from ai_platform.security import decrypt, digest, encrypt, password_hasher, password_matches, token
from ai_platform.services.clock import now
from ai_platform.services.errors import Conflict, Forbidden, InvalidInput, NotFound, RateLimited, \
    Unauthenticated, Unavailable
from ai_platform.settings import settings

SESSION_LIFETIME = {"anonymous": timedelta(minutes=30), "preauth": timedelta(minutes=5), "full": timedelta(days=7)}


@dataclass
class IssuedSession:
    """A new session token; the API layer stores it in the session cookie."""
    raw: str
    max_age: int


def audit(conn, action, target, *, actor=None, request_id="cli"):
    repo.insert_audit_event(conn, action, target, actor, request_id)


# --- operator commands -------------------------------------------------------

def bootstrap_admin(conn):
    repo.lock_bootstrap(conn)
    if repo.find_admin_or_reserved(conn):
        return False
    user = repo.insert_admin(conn, password_hasher.hash("admin"))
    repo.insert_workspace(conn, user["id"])
    audit(conn, "admin.bootstrap", user["id"])
    return True


def reset_admin_mfa(conn, username):
    user = repo.clear_admin_mfa(conn, username)
    if user is None:
        return False
    revoke(conn, user["id"])
    repo.delete_recovery_codes(conn, user["id"])
    audit(conn, "admin.mfa_reset", user["id"])
    return True


def purge_sessions(conn):
    repo.purge_sessions(conn)


# --- sessions ----------------------------------------------------------------

def csrf_token(raw: str) -> str:
    return digest("ai-platform-csrf:" + raw).hex()


def session(conn, raw):
    """Load and lock the live session for a raw cookie value, or return ``None``."""
    if not raw or len(raw) != 43:
        return None
    hashed = digest(raw)
    owner = repo.session_owner(conn, hashed)
    if not owner:
        return None
    user = None
    if owner["user_id"]:
        user = repo.lock_user(conn, owner["user_id"])
        if not user or user["status"] != "active":
            return None
    current = repo.live_session(conn, hashed)
    if current:
        current.update(user=user, csrf_token=csrf_token(raw))
    return current


def new_session(conn, stage, user_id=None, purpose=None):
    raw = token()
    issued = now()
    expires = issued + SESSION_LIFETIME[stage]
    idle = min(expires, issued + timedelta(hours=12))
    repo.insert_session(conn, user_id, digest(raw), digest(csrf_token(raw)), stage, purpose, expires, idle)
    return IssuedSession(raw, int((expires-issued).total_seconds()))


def revoke_one(conn, current):
    repo.revoke_session(conn, current["id"])


def revoke(conn, user_id, keep: UUID | None = None):
    repo.revoke_user_sessions(conn, user_id, keep)


def require_csrf(origin, value, current):
    value = value or ""
    if (
        origin != settings().app_origin or not current or len(value) != 64
        or not compare_digest(digest(value), current["csrf_hash"])
    ):
        raise Forbidden("CSRF validation failed")


def require_session(current, stage="full", *, purpose=None, admin=False):
    if not current or current["stage"] == "anonymous":
        raise Unauthenticated("Sign in required")
    if current["stage"] != stage or (purpose and current["purpose"] != purpose):
        raise Forbidden("Authentication step required")
    user = current["user"]
    if admin and user["role"] != "admin":
        raise Forbidden("Administrator required")
    return user


def refresh(conn, current):
    if current["stage"] == "full":
        repo.touch_session(conn, current["id"])


def require_recent(current):
    if not current["verified_until"] or current["verified_until"] <= now():
        raise Forbidden("Recent MFA required")


def anonymous_csrf(conn, current):
    """Return the CSRF token of the current session, issuing an anonymous session when there is none."""
    if current:
        return current["csrf_token"], None
    issued = new_session(conn, "anonymous")
    return csrf_token(issued.raw), issued


# --- verification tokens -----------------------------------------------------

def issue_verification(conn, user_id, purpose):
    repo.consume_open_tokens(conn, user_id, purpose)
    raw = token()
    expires = now() + (timedelta(days=1) if purpose == "activation" else timedelta(minutes=30))
    repo.insert_verification(conn, user_id, purpose, digest(raw), expires)
    return raw, expires


def consume_verification(conn, raw, purpose):
    hashed = digest(raw)
    record = repo.verification_owner(conn, hashed, purpose)
    if record:
        user = repo.lock_user(conn, record["user_id"])
        wanted = "pending" if purpose == "activation" else "active"
        if user["status"] == wanted and user["role"] == "member":
            if repo.consume_verification(conn, hashed):
                return user
    raise InvalidInput("Invalid or expired token")


# --- passwords, TOTP and throttling ------------------------------------------

def current_totp(user, code):
    secret = decrypt(user["mfa_secret_ciphertext"], user["mfa_key_version"], f"totp:{user['id']}")
    return totp_counter(secret, code, user["mfa_last_counter"])


def totp_counter(secret, code, last=None):
    totp = pyotp.TOTP(secret)
    step = totp.timecode(now())
    for counter in (step-1, step, step+1):
        if (last is None or counter > last) and compare_digest(totp.generate_otp(counter), code):
            return counter
    raise Unauthenticated("Invalid authentication code")


def rate_attempt(address, username):
    """Reserve one sign-in attempt; returns a callback that refunds it after success."""
    config = settings()
    dsn = config.redis_url
    if not dsn:
        raise Unavailable("Authentication rate limiter unavailable")
    keys = [f"{config.redis_prefix}:auth:" + digest(username).hex(),
            f"{config.redis_prefix}:auth-ip:" + digest(address).hex()]
    expiries = redis_limits.reserve_auth_attempt(dsn, keys, 5, 30)
    if not expiries:
        raise RateLimited("Try again later")

    def release():
        redis_limits.release_auth_attempt(dsn, keys, expiries)

    return release


def check_password(user, password):
    if not password_matches(user["password_hash"] if user else None, password):
        raise Unauthenticated("Invalid credentials")


def set_password(conn, user, password):
    repo.update_password(conn, user["id"], password_hasher.hash(password))
    revoke(conn, user["id"])


def check_mfa(conn, user, code):
    repo.set_mfa_counter(conn, user["id"], current_totp(user, code))


# --- sign-in flows -----------------------------------------------------------

def login(conn, current, username, password, address):
    if current["stage"] != "anonymous":
        raise Conflict("Sign out before starting a new session")
    release = rate_attempt(address, username)
    user = repo.lock_user_by_username(conn, username)
    check_password(user, password)
    if user["status"] != "active":
        raise Unauthenticated("Invalid credentials")
    release()
    revoke_one(conn, current)
    if user["role"] == "member":
        return s.LoginView(next="ready", user=s.UserView.model_validate(user)), new_session(conn, "full", user["id"])
    step = "initial_password_change" if user["must_change_password"] else (
        "mfa_verify" if user["mfa_secret_ciphertext"] else "mfa_setup"
    )
    return s.LoginView(next=step), new_session(conn, "preauth", user["id"], step)


def change_initial_password(conn, user, password, request_id):
    set_password(conn, user, password)
    audit(conn, "admin.initial_password_changed", user["id"], actor=user["id"], request_id=request_id)


def use_token(conn, current, raw, password, purpose, request_id):
    if current["stage"] != "anonymous":
        raise Conflict("Sign out before using this token")
    user = consume_verification(conn, raw, purpose)
    set_password(conn, user, password)
    revoke_one(conn, current)
    audit(conn, f"member.{purpose}", user["id"], actor=user["id"], request_id=request_id)


def reauthenticate(conn, current, user, password, code, address):
    release = rate_attempt(address, user["username"])
    check_password(user, password)
    check_mfa(conn, user, code)
    release()
    until = now() + timedelta(minutes=5)
    repo.set_verified_until(conn, current["id"], until)
    return s.FreshView(verified_until=until)


def enrollment_user(current):
    """Admins enroll MFA during pre-auth setup, or re-enroll from a recently verified full session."""
    if current and current["stage"] == "full":
        user = require_session(current, admin=True)
        require_recent(current)
        return user
    return require_session(current, "preauth", purpose="mfa_setup", admin=True)


def setup_mfa(conn, current, user):
    secret = pyotp.random_base32()
    encrypted, version = encrypt(secret, f"pending-totp:{current['id']}")
    repo.set_pending_mfa(conn, current["id"], encrypted, version)
    return s.SetupView(
        secret=secret, provisioning_uri=pyotp.TOTP(secret).provisioning_uri(user["username"], "AI Platform"),
    )


def confirm_mfa(conn, current, user, code, address, request_id):
    expires = current["pending_mfa_expires_at"]
    if not expires or expires <= now():
        raise Conflict("Start MFA setup first")
    release = rate_attempt(address, user["username"])
    secret = decrypt(current["pending_mfa_ciphertext"], current["pending_mfa_key_version"],
                     f"pending-totp:{current['id']}")
    counter = totp_counter(secret, code)
    release()
    encrypted, version = encrypt(secret, f"totp:{user['id']}")
    repo.set_mfa_secret(conn, user["id"], encrypted, version, counter)
    repo.delete_recovery_codes(conn, user["id"])
    codes = [token() for _ in range(10)]
    repo.insert_recovery_codes(conn, user["id"], [digest(code) for code in codes])
    revoke(conn, user["id"])
    issued = new_session(conn, "full", user["id"])
    audit(conn, "admin.mfa_enrolled", user["id"], actor=user["id"], request_id=request_id)
    return s.ConfirmView(user=s.UserView.model_validate(user), recovery_codes=codes), issued


def verify_mfa(conn, current, user, code, recovery_code, address, request_id):
    release = rate_attempt(address, user["username"])
    if code is not None:
        check_mfa(conn, user, code)
    else:
        if not repo.consume_recovery_code(conn, user["id"], digest(recovery_code)):
            raise Unauthenticated("Invalid authentication code")
        audit(conn, "admin.recovery_code_used", user["id"], actor=user["id"], request_id=request_id)
    release()
    revoke_one(conn, current)
    return s.UserResult(user=s.UserView.model_validate(user)), new_session(conn, "full", user["id"])


def me(conn, current, user):
    workspace = active_workspace(conn, user["id"])
    if not workspace:
        raise Forbidden("Workspace unavailable")
    return s.Me(user=s.UserView.model_validate(user), workspace_id=workspace["id"],
                csrf_token=current["csrf_token"])


def change_password(conn, current, user, old, new, address, request_id):
    if user["role"] == "admin":
        require_recent(current)
    release = rate_attempt(address, user["username"])
    check_password(user, old)
    release()
    set_password(conn, user, new)
    audit(conn, "account.password_changed", user["id"], actor=user["id"], request_id=request_id)


def revoke_other_sessions(conn, current, user, password, address, request_id):
    release = rate_attempt(address, user["username"])
    check_password(user, password)
    release()
    revoke(conn, user["id"], current["id"])
    audit(conn, "account.sessions_revoked", user["id"], actor=user["id"], request_id=request_id)


# --- member administration ---------------------------------------------------

def list_members(conn, limit, status, cursor):
    boundary = (None, None)
    if cursor:
        try:
            timestamp, user_id = cursor.split("|")
            boundary = (datetime.fromisoformat(timestamp), UUID(user_id))
            if boundary[0].tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise InvalidInput("Invalid cursor") from exc
    rows = repo.list_users(conn, status, boundary, limit)
    next_cursor = None
    if len(rows) > limit:
        rows.pop()
        next_cursor = f"{rows[-1]['created_at'].isoformat()}|{rows[-1]['id']}"
    return s.MemberPage(items=[s.AdminUserView.model_validate(row) for row in rows], next_cursor=next_cursor)


def create_member(conn, actor, username, display_name, request_id):
    try:
        user = repo.insert_member(conn, username, display_name)
    except psycopg.errors.UniqueViolation as exc:
        raise Conflict("Username already exists") from exc
    repo.insert_workspace(conn, user["id"])
    raw, expires = issue_verification(conn, user["id"], "activation")
    audit(conn, "member.created", user["id"], actor=actor["id"], request_id=request_id)
    return s.MemberCreated(user=s.AdminUserView.model_validate(user), activation_token=raw, expires_at=expires)


def target_user(conn, user_id):
    user = repo.lock_user(conn, user_id)
    if not user or user["status"] == "deleted":
        raise NotFound("Account unavailable")
    return user


def editable_member(conn, user_id, revision):
    user = target_user(conn, user_id)
    if user["role"] == "admin":
        raise Forbidden("Administrator lifecycle requires operator access")
    if user["revision"] != revision:
        raise Conflict("成员信息已更新，请核对最新状态后重试。")
    return user


def reissue_activation(conn, actor, user_id, request_id):
    user = target_user(conn, user_id)
    if user["role"] != "member" or user["status"] != "pending":
        raise Conflict("Account is not awaiting activation")
    raw, expires = issue_verification(conn, user_id, "activation")
    audit(conn, "member.activation_reissued", user_id, actor=actor["id"], request_id=request_id)
    return s.ActivationToken(activation_token=raw, expires_at=expires)


def issue_password_reset(conn, actor, user_id, request_id):
    user = target_user(conn, user_id)
    if user["role"] != "member" or user["status"] != "active":
        raise Conflict("An active member is required")
    revoke(conn, user_id)
    raw, expires = issue_verification(conn, user_id, "password_reset")
    audit(conn, "member.password_reset_issued", user_id, actor=actor["id"], request_id=request_id)
    return s.ResetToken(reset_token=raw, expires_at=expires)


def set_member_status(conn, actor, user_id, status, revision, request_id):
    user = editable_member(conn, user_id, revision)
    if status == "active" and not user["password_hash"]:
        if user["status"] != "disabled":
            raise Conflict("请先使用激活令牌设置密码。")
        status = "pending"
    row = repo.set_user_status(conn, user_id, status)
    if status == "disabled":
        revoke(conn, user_id)
    if status in ("disabled", "pending"):
        repo.consume_all_open_tokens(conn, user_id)
    audit(conn, f"member.{status}", user_id, actor=actor["id"], request_id=request_id)
    return s.AdminUserView.model_validate(row)


def delete_member(conn, actor, user_id, revision, request_id):
    editable_member(conn, user_id, revision)
    repo.mark_user_deleted(conn, user_id)
    repo.disable_workspace(conn, user_id)
    revoke(conn, user_id)
    repo.delete_tokens(conn, user_id)
    audit(conn, "member.deleted", user_id, actor=actor["id"], request_id=request_id)
