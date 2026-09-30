"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings


def get_app_settings(request: Request) -> Settings:
    """Return the Settings instance the running app was created with.

    Reading from `app.state` (instead of calling `get_settings()` directly) means
    a test can build an app with custom Settings and every endpoint sees them.
    """
    settings: Settings = request.app.state.settings
    return settings
