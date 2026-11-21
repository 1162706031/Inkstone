"""Keyset cursors of the form ``<iso timestamp>|<uuid>``."""

from datetime import datetime
from uuid import UUID

from ai_platform.schemas.models import Page
from ai_platform.services.errors import InvalidInput


def parse_cursor(cursor):
    if not cursor:
        return None, None
    try:
        timestamp, row_id = cursor.split("|")
        boundary = (datetime.fromisoformat(timestamp), UUID(row_id))
        if boundary[0].tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise InvalidInput("Invalid cursor") from exc
    return boundary


def to_page(rows, limit, schema, time_column="created_at"):
    """Build a page from ``limit + 1`` rows; the extra row signals that another page exists."""
    next_cursor = None
    if len(rows) > limit:
        rows.pop()
        next_cursor = f"{rows[-1][time_column].isoformat()}|{rows[-1]['id']}"
    return Page(items=[schema.model_validate(row) for row in rows], next_cursor=next_cursor)
