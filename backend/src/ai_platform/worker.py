import asyncio
import logging
from uuid import uuid4

import psycopg

from ai_platform.db import connection
from ai_platform.repositories import runs
from ai_platform.services import chat_execution as chat
from ai_platform.services.chat_context import cache
from ai_platform.services.connection_tests import run_once
from ai_platform.services.deletion import retry_archives
from ai_platform.services.identity import purge_sessions
from ai_platform.settings import settings


async def poll(worker_id: str) -> None:
    while True:
        try:
            tested = await asyncio.to_thread(run_once, worker_id)
            chatted = await asyncio.to_thread(chat.run_once, worker_id)
            worked = tested or chatted
        except psycopg.Error:
            logging.error("Connection-test worker could not access its task store")
            worked = False
        if not worked:
            await asyncio.sleep(1)


def maintain() -> None:
    with connection() as conn:
        purge_sessions(conn)
        cache.collect(conn)
        runs.purge_old_events(conn)
        runs.expire_recoverable_results(conn)
    retry_archives()


async def maintenance() -> None:
    while True:
        try:
            await asyncio.to_thread(maintain)
        except psycopg.Error:
            logging.error("Could not clean expired sessions")
        await asyncio.sleep(60)


async def run() -> None:
    logging.info("AI Platform worker started")
    if not settings().database_url:
        logging.info("No task store configured")
        await asyncio.Event().wait()
    async with asyncio.TaskGroup() as workers:
        workers.create_task(maintenance())
        for _ in range(4):
            workers.create_task(poll(str(uuid4())))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.info("Worker stopped")


if __name__ == "__main__":
    main()
