from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from ai_platform.services.errors import Unavailable
from ai_platform.settings import settings


@contextmanager
def connection():
    dsn = settings().database_url
    if not dsn:
        raise Unavailable("Database unavailable")
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        yield conn
