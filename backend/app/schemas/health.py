"""Response models for operational endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Liveness response.

    Liveness answers "is this process up and able to serve HTTP?" only. It
    deliberately does NOT check PostgreSQL or Redis: if a dependency blips, an
    orchestrator restarting every API container would make things worse.
    Dependency checks belong to the readiness endpoint.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = Field(description="Always 'ok' when the process can respond.")
    service: str = Field(examples=["mirrormarket-api"])
    version: str = Field(examples=["0.1.0"])
    environment: str = Field(examples=["local"])


CheckFailure = Literal["unreachable", "timeout", "not_migrated", "schema_mismatch"]


class ReadinessCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "fail"]
    latency_ms: float | None = None
    # A fixed vocabulary only: raw exception text could leak hostnames or credentials.
    reason: CheckFailure | None = None


class ReadinessResponse(BaseModel):
    """Readiness response: can this instance serve real traffic right now?"""

    model_config = ConfigDict(frozen=True)

    status: Literal["ready", "not_ready"]
    checks: dict[str, ReadinessCheck]
