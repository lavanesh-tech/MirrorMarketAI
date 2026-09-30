"""Background worker that drains the embedding job queue.

    python -m app.workers.embedding_worker          # run forever
    python -m app.workers.embedding_worker --once   # process everything pending, then exit

Safe to run many copies: jobs are claimed with FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.logging import configure_logging
from app.providers.embeddings import EmbeddingProvider, create_embedding_provider
from app.services.embeddings import EmbeddingService

logger = logging.getLogger("app.workers.embedding")


async def drain(database: Database, settings: Settings, provider: EmbeddingProvider) -> int:
    """Process pending jobs until none are left. Returns the number processed."""
    processed = 0
    while True:
        async with database.session_factory() as session:
            service = EmbeddingService(session, settings, provider)
            job = await service.claim_next()
            if job is None:
                return processed
            await service.run(job)
            processed += 1


async def run_worker(settings: Settings, *, once: bool) -> int:
    database = Database.from_settings(settings)
    provider = create_embedding_provider(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    total = 0
    logger.info("embedding worker started", extra={"model": provider.model, "once": once})
    try:
        while not stop.is_set():
            total += await drain(database, settings, provider)
            if once:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_seconds)
            except TimeoutError:
                continue
    finally:
        await provider.aclose()
        await database.dispose()
        logger.info("embedding worker stopped", extra={"processed": total})
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    asyncio.run(run_worker(settings, once=args.once))


if __name__ == "__main__":
    main()
