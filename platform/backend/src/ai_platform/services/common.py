"""Helpers shared by the domain services."""

from ai_platform.repositories import common as repo
from ai_platform.services.errors import NotFound


def require(row, message="Resource unavailable"):
    if row is None:
        raise NotFound(message)
    return row


def workspace(conn, user_id):
    return require(repo.active_workspace(conn, user_id))["id"]


def audit(conn, request_id, actor, action, target, target_type):
    repo.insert_audit(conn, actor["id"], workspace(conn, actor["id"]), action, target, target_type, request_id)
