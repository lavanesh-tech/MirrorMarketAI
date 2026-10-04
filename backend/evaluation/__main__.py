"""Run the end-to-end evaluation and write the results.

    uv run python -m evaluation                    # offline engines (rules + hashing)
    uv run python -m evaluation --engine openai    # OpenAI extraction, agents and answers
    uv run python -m evaluation --embedder openai  # OpenAI embeddings
    uv run python -m evaluation --report-only      # rebuild docs/EVALUATION.md from results

It creates a throwaway database on the PostgreSQL server from DATABASE_URL (or
EVAL_DATABASE_URL), migrates it, runs the evaluation against the real app in-process,
and drops the database again. Results go to `evaluation/results/<engine>-<embedder>.json`;
`docs/EVALUATION.md` is rebuilt from every result file present.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from alembic import command
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.migrations import alembic_config
from evaluation.report import render
from evaluation.runner import evaluate

RESULTS = Path(__file__).parent / "results"
BASELINES = Path(__file__).parent / "baseline"  # first-run results, kept for comparison
REPORT = Path(__file__).resolve().parents[2] / "docs" / "EVALUATION.md"


def evaluation_settings(database_url: str, engine: str, embedder: str) -> Settings:
    """The app's normal settings (so the OpenAI key comes from .env), pinned for a fair run."""
    return Settings(
        database_url=database_url,  # type: ignore[arg-type]
        redis_url=None,  # no cache and no rate limits between the system and the score
        rate_limit_enabled=False,
        kafka_enabled=False,
        cors_allowed_origins=[],
        log_level="WARNING",
        requirements_extractor=engine,  # type: ignore[arg-type]
        agent_engine=engine,  # type: ignore[arg-type]
        embedding_provider=embedder,  # type: ignore[arg-type]
    )


async def _admin(server_url: str, sql: str) -> None:
    dsn = make_url(server_url).set(drivername="postgresql", database="postgres")
    connection = await asyncpg.connect(dsn.render_as_string(hide_password=False))
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


def run(engine: str, embedder: str) -> dict[str, Any]:
    server_url = os.environ.get("EVAL_DATABASE_URL") or Settings().database_url.get_secret_value()
    name = f"mm_eval_{uuid.uuid4().hex[:12]}"
    database_url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
    asyncio.run(_admin(server_url, f'CREATE DATABASE "{name}"'))
    try:
        config = alembic_config(database_url)
        config.attributes["skip_logging_config"] = True
        command.upgrade(config, "head")
        settings = evaluation_settings(database_url, engine, embedder)
        return asyncio.run(evaluate(settings, log=lambda step: print(f"  {step}", flush=True)))
    finally:
        asyncio.run(_admin(server_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", choices=["rules", "openai"], default="rules")
    parser.add_argument("--embedder", choices=["hashing", "openai"], default="hashing")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    if not args.report_only:
        print(f"Evaluating: engine={args.engine}, embedder={args.embedder}")
        report = run(args.engine, args.embedder)
        report["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%d")
        RESULTS.mkdir(exist_ok=True)
        path = RESULTS / f"{args.engine}-{args.embedder}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")

    results = {p.stem: json.loads(p.read_text()) for p in sorted(RESULTS.glob("*.json"))}
    if not results:
        print("no results to report", file=sys.stderr)
        return 1
    baselines = {p.stem: json.loads(p.read_text()) for p in sorted(BASELINES.glob("*.json"))}
    REPORT.write_text(render(results, baselines))
    print(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
