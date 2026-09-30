"""Helpers for reading Alembic migration metadata at runtime."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# backend/alembic.ini locally, /app/alembic.ini in the container.
ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def alembic_config(database_url: str | None = None) -> Config:
    """Build an Alembic Config.

    The URL is passed through `attributes` (not `set_main_option`) because
    configparser would treat a '%' in a password as interpolation syntax.
    """
    config = Config(str(ALEMBIC_INI_PATH))
    if database_url is not None:
        config.attributes["database_url"] = database_url
    return config


@lru_cache(maxsize=1)
def expected_head_revision() -> str | None:
    """The newest migration revision shipped with this build of the code."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    if len(heads) > 1:
        raise RuntimeError(f"multiple Alembic heads {heads}; merge them before deploying")
    return heads[0] if heads else None
