"""Distributed rate limiting with GCRA (generic cell rate algorithm) in one Lua script.

GCRA stores a single number per key, the "theoretical arrival time" (TAT): when
the bucket will be empty again. A request is allowed when
`TAT + interval - burst * interval <= now`, and it pushes TAT forward by one
interval. That gives `limit` requests per `period` with smooth refill, exact
`Retry-After`, O(1) memory per key and one atomic round trip.

The clock is Redis's own (`TIME`), so API replicas with skewed clocks agree.
Failures fail OPEN: a Redis outage must not take login or agents down with it.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_GCRA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local interval = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local tat = tonumber(redis.call('GET', KEYS[1])) or now
if tat < now then tat = now end
local new_tat = tat + interval
local allow_at = new_tat - burst * interval
if allow_at > now then
  return {0, allow_at - now, 0}
end
redis.call('SET', KEYS[1], new_tat, 'PX', new_tat - now)
return {1, 0, math.floor((now - allow_at) / interval)}
"""


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int  # 0 when allowed


class RateLimiter:
    def __init__(self, redis: Redis | None, prefix: str) -> None:
        self._redis = redis
        self._prefix = prefix
        self._script = redis.register_script(_GCRA) if redis is not None else None

    def _key(self, name: str, identity: str) -> str:
        # Identities can be emails or IPs: hash them so keys hold no personal data.
        digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
        return f"{self._prefix}rl:{name}:{digest}"

    async def hit(self, name: str, identity: str, limit: int, period_seconds: int) -> Decision:
        if self._script is None:
            return Decision(allowed=True, limit=limit, remaining=limit, retry_after_seconds=0)
        interval_ms = math.ceil(period_seconds * 1000 / limit)
        try:
            allowed, wait_ms, remaining = await self._script(
                keys=[self._key(name, identity)], args=[interval_ms, limit]
            )
        except RedisError:
            logger.warning("rate limiter unavailable; allowing request", extra={"limit": name})
            return Decision(allowed=True, limit=limit, remaining=limit, retry_after_seconds=0)
        return Decision(
            allowed=bool(allowed),
            limit=limit,
            remaining=int(remaining),
            retry_after_seconds=math.ceil(int(wait_ms) / 1000),
        )
