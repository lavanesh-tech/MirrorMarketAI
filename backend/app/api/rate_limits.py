"""Rate-limit dependencies.

- Auth endpoints are limited per client IP (credential stuffing, sign-up spam).
  Login is additionally limited per email, so one account cannot be brute-forced
  from many IPs.
- Agent endpoints are limited per user, on unsafe methods only: they are the
  expensive calls (retrieval + optional LLM tokens). Reading run history is free.

The client IP is the socket peer. Behind a proxy, uvicorn's `--forwarded-allow-ips`
must be set so `request.client` is the real client (configured in Phase 29).
"""

from __future__ import annotations

from fastapi import Request, Response

from app.api.deps import CurrentUser, RateLimiterDep, SettingsDep
from app.core.errors import RateLimitedError

PERIOD_SECONDS = 60
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def enforce(
    limiter: RateLimiterDep,
    response: Response | None,
    name: str,
    identity: str,
    limit: int,
) -> None:
    decision = await limiter.hit(name, identity, limit, PERIOD_SECONDS)
    if not decision.allowed:
        raise RateLimitedError(decision.retry_after_seconds, limit)
    if response is not None:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)


async def auth_rate_limit(
    request: Request, response: Response, limiter: RateLimiterDep, settings: SettingsDep
) -> None:
    if settings.rate_limit_enabled:
        name = f"auth:{request.url.path.rsplit('/', 1)[-1]}"
        await enforce(
            limiter, response, name, _client_ip(request), settings.rate_limit_auth_per_minute
        )


async def agent_rate_limit(
    request: Request,
    response: Response,
    user: CurrentUser,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> None:
    if settings.rate_limit_enabled and request.method not in _SAFE_METHODS:
        await enforce(
            limiter, response, "agents", str(user.id), settings.rate_limit_agents_per_minute
        )
