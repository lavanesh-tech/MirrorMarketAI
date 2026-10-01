"""Price-history query latency against a real PostgreSQL, with and without the history index.

Run (needs the stack up, e.g. `make up`):
  DATABASE_URL=postgresql+asyncpg://mirrormarket:mirrormarket@localhost:5433/mirrormarket \
    uv run python -m benchmarks.price_history_db

Seeds a throwaway user + product with synthetic snapshots, measures the bucketed history
query (median/p95), repeats it inside a transaction where the composite index is dropped
(rolled back afterwards), then deletes the seeded rows.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import random
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.prices import PriceService
from benchmarks.citations import _commit

ROWS = 50_000
OTHER_PRODUCTS = 20  # noise rows so the index has something to skip
RUNS = 20


async def _seed(session: AsyncSession, rng: random.Random) -> tuple[uuid.UUID, uuid.UUID]:
    user_id, target = uuid.uuid4(), uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO users (id, email, password_hash, display_name) VALUES (:u, :e, 'x', 'b')"
        ),
        {"u": user_id, "e": f"bench-{user_id.hex[:8]}@example.com"},
    )
    products = [target, *(uuid.uuid4() for _ in range(OTHER_PRODUCTS))]
    for pid in products:
        await session.execute(
            text(
                "INSERT INTO products (id, brand, name, category, canonical_key, created_by_id) "
                "VALUES (:p, 'Bench', :n, 'laptop', :k, :u)"
            ),
            {"p": pid, "n": pid.hex, "k": f"bench::{pid.hex}", "u": user_id},
        )
    start = datetime.now(UTC) - timedelta(days=365)
    rows = [
        {
            "id": uuid.uuid4(),
            "p": products[i % len(products)],
            "r": f"Shop {i % 5}",
            "a": Decimal(rng.randint(900, 1600)),
            "t": start + timedelta(seconds=i * 630),
            "u": user_id,
        }
        for i in range(ROWS)
    ]
    await session.execute(
        text(
            "INSERT INTO price_snapshots (id, product_id, retailer, amount, currency, observed_at,"
            " source, created_by_id) VALUES (:id, :p, :r, :a, 'USD', :t, 'IMPORT', :u)"
        ),
        rows,
    )
    await session.execute(text("ANALYZE price_snapshots"))
    return user_id, target


async def _measure(session: AsyncSession, product_id: uuid.UUID) -> dict[str, float]:
    service = PriceService(session)
    timings = []
    for _ in range(RUNS):
        started = time.perf_counter()
        await service.history(product_id, currency="USD", bucket="week", since=None, until=None)
        timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    return {
        "median_ms": round(statistics.median(timings), 2),
        "p95_ms": round(timings[int(0.95 * (len(timings) - 1))], 2),
    }


async def run(database_url: str) -> dict[str, object]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rng = random.Random(7)  # noqa: S311 - reproducible synthetic data
    async with factory() as session:
        user_id, target = await _seed(session, rng)
        await session.commit()
    try:
        async with factory() as session:
            with_index = await _measure(session, target)
        async with factory() as session, session.begin():
            await session.execute(text("DROP INDEX ix_price_snapshots_history"))
            without_index = await _measure(session, target)
            await session.rollback()
    finally:
        async with factory() as session:
            await session.execute(
                text("DELETE FROM products WHERE created_by_id = :u"), {"u": user_id}
            )
            await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
            await session.commit()
        await engine.dispose()
    return {
        "benchmark": "price_history_query",
        "dataset": f"synthetic: {ROWS} snapshots over {OTHER_PRODUCTS + 1} products, 365 days",
        "target_rows": ROWS // (OTHER_PRODUCTS + 1) + 1,
        "runs": RUNS,
        "with_index": with_index,
        "without_index": without_index,
    }


def main() -> None:
    url = os.environ["DATABASE_URL"]
    result = asyncio.run(run(url)) | {
        "commit": _commit(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }
    out = Path(__file__).parent / "results" / "price_history_db.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
