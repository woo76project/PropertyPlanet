import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from fastapi.testclient import TestClient

from app.auth import require_complete_profile
from app.main import app, settings
from app.search_rate_limit import AccountSearchLimiter


class SearchRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 100.0
        self.limiter = AccountSearchLimiter(clock=lambda: self.now)
        self.request = Request({"type": "http", "method": "GET", "path": "/properties/preview",
                                "app": SimpleNamespace()})
        self.user = {"id": 2, "role": "User"}
        clock = patch("app.search_rate_limit.InMemoryBucket.now", side_effect=lambda: int(self.now * 1000))
        clock.start()
        self.addCleanup(clock.stop)

    async def check(self, category="preview", user=None):
        await self.limiter.check(self.request, Response(), user or self.user, category)

    async def test_preview_twenty_and_paid_sixty_are_separate(self):
        for _ in range(20):
            await self.check()
        with self.assertRaises(HTTPException) as raised:
            await self.check()
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.headers["Retry-After"], "61")
        for _ in range(60):
            await self.check("credit")
        with self.assertRaises(HTTPException) as raised:
            await self.check("credit")
        self.assertEqual(raised.exception.status_code, 429)
        await self.check(user={"id": 3, "role": "User"})

    async def test_counters_reset_in_new_process_instance(self):
        for _ in range(20):
            await self.check()
        other = AccountSearchLimiter()
        await other.check(self.request, Response(), self.user, "preview")

    async def test_concurrent_calls_cannot_exceed_limit(self):
        async def attempt():
            try:
                await self.check()
                return True
            except HTTPException as error:
                self.assertEqual(error.status_code, 429)
                return False
        self.assertEqual(sum(await asyncio.gather(*(attempt() for _ in range(80)))), 20)

    async def test_rolling_window_recovers_without_resetting_newer_requests(self):
        for _ in range(10):
            await self.check()
        self.now = 130.0
        for _ in range(10):
            await self.check()
        self.now = 161.0
        for _ in range(10):
            await self.check()
        with self.assertRaises(HTTPException) as raised:
            await self.check()
        self.assertEqual(raised.exception.headers["Retry-After"], "30")

    async def test_admin_bypasses_limits_without_counters(self):
        for _ in range(100):
            await self.check(user={"id": 1, "role": "Admin"})
        self.assertFalse(self.limiter._accounts)

    async def test_memory_is_bounded_and_idle_accounts_expire(self):
        self.limiter = AccountSearchLimiter(clock=lambda: self.now, max_keys=1)
        await self.check()
        with self.assertRaises(HTTPException) as raised:
            await self.check(user={"id": 3, "role": "User"})
        self.assertEqual(raised.exception.status_code, 429)
        self.now = 162.0
        await self.check(user={"id": 3, "role": "User"})
        self.assertEqual(list(self.limiter._accounts), [(3, "preview")])


class SearchRateLimitRouteTests(unittest.TestCase):
    def test_routes_need_no_external_service_and_reject_before_property_queries(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
        self.addCleanup(app.dependency_overrides.clear)
        with patch("app.search_rate_limit.search_rate_limiter", AccountSearchLimiter()), patch("app.properties.psycopg.connect") as connect:
            connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
            client = TestClient(app)
            for _ in range(20):
                self.assertEqual(client.get("/properties/preview").status_code, 200)
            self.assertEqual(connect.call_count, 20)  # No preview caching remains.
            response = client.get("/properties/preview?before_id=10", headers={"Origin": settings.frontend_url})
            self.assertEqual(response.status_code, 429)
            self.assertIn("Retry-After", response.headers["access-control-expose-headers"])
            self.assertEqual(connect.call_count, 20)
            connect.reset_mock()
            # Empty clues fail before any credit work but still count toward
            # the paid endpoint limit, even with a new request UUID each time.
            for _ in range(60):
                self.assertEqual(client.post("/properties/search", json={"request_id": str(uuid4())}).status_code, 422)
            response = client.post("/properties/search", json={"request_id": str(uuid4()), "search": "Garden"})
            self.assertEqual(response.status_code, 429)
            self.assertIn("No search credits were deducted", response.json()["detail"])
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
