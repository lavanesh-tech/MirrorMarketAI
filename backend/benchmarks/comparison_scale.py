"""Latency of the comparison engine (scoring + sensitivity) on synthetic matrices.

Run: uv run python -m benchmarks.comparison_scale
Synthetic, seeded data; pure CPU (no database). Reports median and p95 over runs.
"""

from __future__ import annotations

import json
import platform
import random
import statistics
import time
from decimal import Decimal
from pathlib import Path

from app.domain.comparison import Candidate, Cell, CellStatus, columns_for, compare
from app.domain.requirements import Criterion, Operator, Priority
from benchmarks.citations import _commit

SIZES = ((5, 5), (20, 10), (50, 20))
THRESHOLD = 50
MUST_CRITERIA = 2
RUNS = 30


def _matrix(
    products: int, criteria: int, rng: random.Random
) -> tuple[list[Criterion], list[Candidate]]:
    crits = [
        Criterion(
            key=f"spec_{i}",
            operator=Operator.GTE if i % 2 else Operator.LTE,
            value_number=Decimal(THRESHOLD),
            priority=Priority.MUST if i < MUST_CRITERIA else Priority.SHOULD,
        )
        for i in range(criteria)
    ]
    cands = []
    for p in range(products):
        cells = {}
        for c in crits:
            value = rng.randint(0, 100)
            met = value >= THRESHOLD if c.operator is Operator.GTE else value <= THRESHOLD
            cells[c.key] = Cell(CellStatus.MET if met else CellStatus.UNMET, Decimal(value))
        cands.append(Candidate(f"p{p}", f"Product {p:03d}", cells))
    return crits, cands


def run() -> dict[str, object]:
    rng = random.Random(42)  # noqa: S311 - reproducible synthetic data, not security
    results = []
    for products, criteria in SIZES:
        crits, cands = _matrix(products, criteria, rng)
        cols = columns_for(crits, {}, {}, include_price=False, budget_is_hard=True)
        timings = []
        for _ in range(RUNS):
            start = time.perf_counter()
            compare(cols, cands)
            timings.append((time.perf_counter() - start) * 1000)
        timings.sort()
        results.append(
            {
                "products": products,
                "criteria": criteria,
                "median_ms": round(statistics.median(timings), 2),
                "p95_ms": round(timings[int(0.95 * (len(timings) - 1))], 2),
            }
        )
    return {
        "benchmark": "comparison_engine_latency",
        "dataset": "synthetic seeded matrices; includes 2x-per-criterion sensitivity re-ranking",
        "runs_per_size": RUNS,
        "results": results,
    }


def main() -> None:
    result = run() | {
        "commit": _commit(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }
    out = Path(__file__).parent / "results" / "comparison_scale.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
