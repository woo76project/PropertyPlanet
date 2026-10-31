"""Opt-in PostgreSQL checks using session-local temporary tables only.

Run from backend: python -m unittest discover -s tests -p integration_search_credits.py -v
"""
from contextlib import contextmanager, ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from fastapi.testclient import TestClient

from app.auth import require_complete_profile
from app.core.config import get_settings
from app.main import app


class CreditIntegrationTests(unittest.TestCase):
    def setUp(self):
        guard = patch("app.search_rate_limit.check_search_limit")
        guard.start()
        self.addCleanup(guard.stop)
        params = conninfo_to_dict(get_settings().db_url.get_secret_value())
        if "-pooler" in params.get("host", ""):
            params["host"] = params["host"].replace("-pooler", "")
        self.connection = psycopg.connect(**params, row_factory=dict_row, autocommit=True, connect_timeout=15)
        self.addCleanup(self.connection.close)
        self.connection.execute("SET statement_timeout = '15s'")
        # All unqualified application table names below resolve to pg_temp.
        schema = Path("sql/schema.sql").read_text().replace("CREATE TABLE", "CREATE TEMP TABLE")
        schema = schema.replace("property_duplicate_text", "pg_temp.property_duplicate_text")
        self.connection.execute(schema)
        for clerk_id, role in (("user_admin", "Admin"), ("user_test", "User")):
            self.connection.execute("INSERT INTO users (clerk_user_id, email, role) VALUES (%s, %s, %s)", (clerk_id, clerk_id + "@example.invalid", role))
        for number in range(3):
            self.connection.execute("INSERT INTO properties (address, owner_name, phone_01, created_by) VALUES (%s, 'Tan', 'TEST', 1)", (f"Garden {number}",))
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.addCleanup(app.dependency_overrides.clear)
        self.stack.enter_context(patch("app.credits.psycopg.connect", side_effect=self.application_connection))
        self.client = TestClient(app)

    @contextmanager
    def application_connection(self, *args, **kwargs):
        with self.connection.transaction():
            yield self.connection

    def authorize(self, role):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1 if role == "Admin" else 2, "role": role, "username": "test"}

    def balance(self):
        row = self.connection.execute("SELECT credit_balance AS balance FROM users WHERE id = 2").fetchone()
        return row["balance"] if row else 0

    def grant(self, amount):
        self.authorize("Admin")
        body = {"request_id": str(uuid4()), "amount": amount, "reference": "TEST PAYMENT"}
        response = self.client.post("/admin/users/2/credits", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return body

    def test_grant_retry_search_retry_paging_zero_balance_and_scope(self):
        body = self.grant(2)
        response = self.client.post("/admin/users/2/credits", json=body)
        self.assertTrue(response.json()["duplicate"])
        self.assertEqual(self.balance(), 2)
        self.authorize("User")
        query = {"request_id": str(uuid4()), "search": "Garden", "limit": 1}
        response = self.client.post("/properties/search", json=query)
        self.assertEqual(response.status_code, 200, response.text)
        first = response.json()
        self.assertTrue(first["charged"])
        self.assertEqual(self.balance(), 1)
        self.assertTrue(first["pagination"]["has_more"])
        response = self.client.post("/properties/search", json={**query, "before_id": first["pagination"]["next_before_id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["charged"])
        self.assertEqual(self.balance(), 1)
        self.assertEqual(self.client.post("/properties/search", json={**query, "search": "Other"}).status_code, 409)
        empty_query = {"request_id": str(uuid4()), "search": "No matching property"}
        for _ in range(2):
            empty = self.client.post("/properties/search", json=empty_query)
            self.assertEqual(empty.status_code, 200, empty.text)
            self.assertEqual(empty.json()["properties"], [])
            self.assertFalse(empty.json()["charged"])
            self.assertEqual(empty.json()["balance"], 1)
            self.assertEqual(self.balance(), 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) AS total FROM credit_searches WHERE request_id = %s",
                                                (empty_query["request_id"],)).fetchone()["total"], 0)
        paid = self.client.post("/properties/search", json={**query, "request_id": str(uuid4())})
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertTrue(paid.json()["charged"])
        self.assertEqual(self.balance(), 0)
        self.assertEqual(self.client.post("/properties/search", json={**query, "request_id": str(uuid4())}).status_code, 402)
        self.assertEqual(self.client.post("/properties/search", json=query).status_code, 200)
        self.assertEqual(self.balance(), 0)

    def test_failed_audit_rolls_back_assignment_and_balance(self):
        self.grant(5)
        body = {"request_id": str(uuid4()), "amount": 10, "reference": "TEST FAIL"}
        with patch("app.credits.record_audit", side_effect=psycopg.OperationalError("test rollback")):
            self.assertEqual(self.client.post("/admin/users/2/credits", json=body).status_code, 503)
        self.assertEqual(self.balance(), 5)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) AS total FROM credit_assignments WHERE request_id = %s", (body["request_id"],)).fetchone()["total"], 0)

    def test_adjustment_retry_stale_balance_and_audit_rollback(self):
        self.grant(11)
        change = {"request_id": str(uuid4()), "direction": "subtract", "amount": 1, "reason": "Correct extra credit", "expected_balance": 11}
        response = self.client.post("/admin/users/2/credits/adjustments", json=change)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.balance(), 10)
        self.assertTrue(self.client.post("/admin/users/2/credits/adjustments", json=change).json()["duplicate"])
        self.assertEqual(self.balance(), 10)
        self.assertEqual(self.client.post("/admin/users/2/credits/adjustments", json={**change, "request_id": str(uuid4())}).status_code, 409)
        self.assertEqual(self.client.post("/admin/users/2/credits/adjustments", json={**change, "request_id": str(uuid4()), "amount": 11, "expected_balance": 10}).status_code, 409)
        failed = {**change, "request_id": str(uuid4()), "expected_balance": 10}
        with patch("app.credits.record_audit", side_effect=psycopg.OperationalError("test rollback")):
            self.assertEqual(self.client.post("/admin/users/2/credits/adjustments", json=failed).status_code, 503)
        self.assertEqual(self.balance(), 10)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) AS total FROM credit_adjustments").fetchone()["total"], 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) AS total FROM credit_adjustments WHERE request_id=%s", (failed["request_id"],)).fetchone()["total"], 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) AS total FROM audit_logs WHERE action='user.credits_adjusted'").fetchone()["total"], 1)

    def test_suspension_blocks_previous_search_without_erasing_balance(self):
        self.grant(3)
        self.authorize("User")
        query = {"request_id": str(uuid4()), "search": "Garden"}
        self.assertEqual(self.client.post("/properties/search", json=query).status_code, 200)
        self.authorize("Admin")
        self.assertEqual(self.client.patch("/admin/users/2/status", json={"status": "Suspended"}).status_code, 200)
        self.authorize("User")
        self.assertEqual(self.client.post("/properties/search", json=query).status_code, 403)
        self.assertEqual(self.balance(), 2)
