"""Per-account fastapi-limiter limits in memory; use one backend process."""

import asyncio
from collections import OrderedDict
import math
from time import monotonic
from types import SimpleNamespace

from fastapi import Depends, HTTPException, Request, Response
from fastapi_limiter.depends import RateLimiter
from pyrate_limiter import Duration, InMemoryBucket, Limiter, Rate, RateItem, SingleBucketFactory

from app.auth import require_complete_profile


def reject_search(wait: int):
    raise HTTPException(
        429,
        f"Too many searches. Please wait {wait} seconds and try again. No search credits were deducted for this request.",
        headers={"Retry-After": str(wait)},
    )


class AccountSearchLimiter:
    def __init__(self, clock=monotonic, max_keys=10_000):
        self._clock = clock
        self._max_keys = max_keys
        self._accounts = OrderedDict()
        self._lock = asyncio.Lock()

    async def check(self, request: Request, response: Response, user: dict, category: str):
        if user["role"] == "Admin":
            return
        limit = {"preview": 20, "credit": 60}[category]
        key = (user["id"], category)
        # Serialize lookup, expiry and acquisition so concurrent calls cannot
        # replace an active counter or race its expiry. No network I/O inside.
        async with self._lock:
            now = self._clock()
            while self._accounts:
                oldest = next(iter(self._accounts))
                if self._accounts[oldest][2] > now - 61:
                    break
                del self._accounts[oldest]
            entry = self._accounts.get(key)
            if entry is None:
                if len(self._accounts) >= self._max_keys:
                    reject_search(60)
                bucket = InMemoryBucket([Rate(limit, Duration.MINUTE)])
                limiter = Limiter(SingleBucketFactory(bucket, schedule_leak=False))

                async def identifier(_request):
                    return str(user["id"])

                async def exceeded(_request, _response):
                    delay = bucket.waiting(RateItem(str(user["id"]), bucket.now()))
                    reject_search(max(1, math.ceil(delay / 1000)))

                dependency = RateLimiter(limiter=limiter, identifier=identifier,
                                         callback=exceeded, blocking=False)
            else:
                bucket, dependency, _ = entry
            self._accounts[key] = (bucket, dependency, now)
            self._accounts.move_to_end(key)
            # Prune timestamps on access rather than creating background tasks
            # per user. Idle accounts are discarded above, with a bounded map.
            bucket.leak(bucket.now())
            # Released fastapi-limiter 0.2.0 inspects app.routes, while FastAPI
            # 0.142 has lazy router entries without .path. Account/category
            # buckets already isolate endpoints, so skip that incompatible scan.
            limiter_request = Request({**request.scope, "app": SimpleNamespace(routes=[])})
            await dependency(limiter_request, response)


search_rate_limiter = AccountSearchLimiter()


async def check_search_limit(request: Request, response: Response, user: dict, category: str):
    await search_rate_limiter.check(request, response, user, category)


async def require_preview_search(request: Request, response: Response, user: dict = Depends(require_complete_profile)):
    await check_search_limit(request, response, user, "preview")
    return user


async def require_credit_search(request: Request, response: Response, user: dict = Depends(require_complete_profile)):
    await check_search_limit(request, response, user, "credit")
    return user
