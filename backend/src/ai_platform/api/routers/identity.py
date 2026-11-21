from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from ai_platform.api.deps import anonymous, authenticated, clear_cookie, client_address, current_session, \
    enrollment, set_session_cookie
from ai_platform.db import connection
from ai_platform.schemas import identity as s
from ai_platform.services import identity as auth

router = APIRouter(prefix="/api/v1")


def request_id(request):
    return request.state.request_id


@router.get("/auth/csrf", operation_id="getCsrf")
def csrf(request: Request, response: Response) -> s.CSRFView:
    with connection() as conn:
        raw, issued = auth.anonymous_csrf(conn, current_session(conn, request))
        if issued:
            set_session_cookie(response, issued)
        return s.CSRFView(csrf_token=raw)


@router.post("/auth/login", operation_id="login", response_model_exclude_none=True)
def login(payload: s.Login, request: Request, response: Response) -> s.LoginView:
    with anonymous(request) as (conn, current):
        view, issued = auth.login(conn, current, payload.username, payload.password, client_address(request))
        set_session_cookie(response, issued)
        return view


@router.post("/auth/change-initial-password", status_code=204, operation_id="changeInitialPassword")
def change_initial(payload: s.NewPassword, request: Request, response: Response) -> None:
    with authenticated(request, admin=True, stage="preauth", purpose="initial_password_change") as (conn, _, user):
        auth.change_initial_password(conn, user, payload.new_password, request_id(request))
        clear_cookie(response)


def use_token(request, response, raw, password, purpose):
    with anonymous(request) as (conn, current):
        auth.use_token(conn, current, raw, password, purpose, request_id(request))
        clear_cookie(response)


@router.post("/auth/activate", operation_id="activateMember")
def activate(payload: s.Activation, request: Request, response: Response) -> s.StatusResult:
    use_token(request, response, payload.token, payload.password, "activation")
    return s.StatusResult(status="activated")


@router.post("/auth/reset-password", operation_id="resetPassword")
def reset_password(payload: s.ResetPassword, request: Request, response: Response) -> s.StatusResult:
    use_token(request, response, payload.token, payload.new_password, "password_reset")
    return s.StatusResult(status="password_changed")


@router.post("/auth/reauthenticate", operation_id="reauthenticate")
def reauthenticate(payload: s.RecentAuth, request: Request) -> s.FreshView:
    with authenticated(request, admin=True) as (conn, current, user):
        return auth.reauthenticate(conn, current, user, payload.password, payload.code, client_address(request))


@router.post("/auth/mfa/setup", operation_id="setupMfa")
def setup_mfa(request: Request) -> s.SetupView:
    with enrollment(request) as (conn, current, user):
        return auth.setup_mfa(conn, current, user)


@router.post("/auth/mfa/confirm", operation_id="confirmMfa")
def confirm_mfa(payload: s.CodeInput, request: Request, response: Response) -> s.ConfirmView:
    with enrollment(request) as (conn, current, user):
        view, issued = auth.confirm_mfa(conn, current, user, payload.code, client_address(request), request_id(request))
        set_session_cookie(response, issued)
        return view


@router.post("/auth/mfa/verify", operation_id="verifyMfa")
def verify_mfa(payload: s.Verify, request: Request, response: Response) -> s.UserResult:
    with authenticated(request, admin=True, stage="preauth", purpose="mfa_verify") as (conn, current, user):
        view, issued = auth.verify_mfa(conn, current, user, payload.code, payload.recovery_code,
                                       client_address(request), request_id(request))
        set_session_cookie(response, issued)
        return view


@router.get("/auth/me", operation_id="getMe")
def me(request: Request) -> s.Me:
    with authenticated(request) as (conn, current, user):
        return auth.me(conn, current, user)


@router.post("/auth/logout", status_code=204, operation_id="logout")
def logout(request: Request, response: Response) -> None:
    with anonymous(request) as (conn, current):
        auth.revoke_one(conn, current)
        clear_cookie(response)


@router.post("/auth/change-password", status_code=204, operation_id="changePassword")
def change_password(payload: s.ChangePassword, request: Request, response: Response) -> None:
    with authenticated(request) as (conn, current, user):
        auth.change_password(conn, current, user, payload.current_password, payload.new_password,
                             client_address(request), request_id(request))
        clear_cookie(response)


@router.post("/auth/revoke-other-sessions", status_code=204, operation_id="revokeOtherSessions")
def revoke_other(payload: s.PasswordCheck, request: Request) -> None:
    with authenticated(request) as (conn, current, user):
        auth.revoke_other_sessions(conn, current, user, payload.password, client_address(request), request_id(request))


@router.get("/admin/users", operation_id="listUsers")
def list_users(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20,
               status: s.Status | None = None, cursor: Annotated[str | None, Query(max_length=96)] = None) -> s.MemberPage:
    with authenticated(request, admin=True) as (conn, _, _):
        return auth.list_members(conn, limit, status, cursor)


@router.post("/admin/users", status_code=201, operation_id="createMember")
def create_member(payload: s.MemberCreate, request: Request) -> s.MemberCreated:
    with authenticated(request, admin=True) as (conn, _, actor):
        return auth.create_member(conn, actor, payload.username, payload.display_name, request_id(request))


@router.post("/admin/users/{user_id}/activation-token", status_code=201, operation_id="reissueActivation")
def reissue(user_id: UUID, request: Request) -> s.ActivationToken:
    with authenticated(request, admin=True) as (conn, _, actor):
        return auth.reissue_activation(conn, actor, user_id, request_id(request))


@router.post("/admin/users/{user_id}/password-reset", status_code=201, operation_id="issuePasswordReset")
def issue_reset(user_id: UUID, request: Request) -> s.ResetToken:
    with authenticated(request, admin=True) as (conn, _, actor):
        return auth.issue_password_reset(conn, actor, user_id, request_id(request))


@router.patch("/admin/users/{user_id}", operation_id="setMemberStatus")
def update_member(user_id: UUID, payload: s.MemberStatus, request: Request) -> s.AdminUserView:
    with authenticated(request, admin=True) as (conn, _, actor):
        return auth.set_member_status(conn, actor, user_id, payload.status, payload.revision, request_id(request))


@router.delete("/admin/users/{user_id}", status_code=204, operation_id="deleteMember")
def delete_member(user_id: UUID, revision: Annotated[int, Query(ge=1)], request: Request) -> None:
    with authenticated(request, admin=True) as (conn, _, actor):
        auth.delete_member(conn, actor, user_id, revision, request_id(request))
