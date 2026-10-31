from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
import psycopg

from app.auth import require_complete_profile
from app.main import app


class CreditTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(app.dependency_overrides.clear)
        self.settings = SimpleNamespace(db_url=SecretStr("unused"), whatsapp_number="12025550123", credit_price_myr="1.00")
        self.stack.enter_context(patch("app.credits.get_settings", return_value=self.settings))
        self.connect = self.stack.enter_context(patch("app.credits.psycopg.connect"))
        self.execute = self.connect.return_value.__enter__.return_value.execute
        self.client = TestClient(app)
        self.body = {"request_id": str(uuid4()), "amount": 10, "reference": "TEST PAYMENT"}

    def authorize(self, role="User"):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1 if role == "Admin" else 2, "role": role, "username": "demo"}

    def test_zero_default_and_prefilled_whatsapp_reference(self):
        self.authorize()
        self.execute.return_value.fetchone.return_value = None
        response = self.client.get("/credits")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["balance"], 0)
        self.assertFalse(data["unlimited"])
        url = urlsplit(data["purchase_url"])
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "wa.me", "/12025550123"))
        message = parse_qs(url.query)["text"][0]
        self.assertIn("Username: demo", message)
        self.assertNotIn("User ID", message)

    def test_missing_contact_disables_purchase_and_admin_is_unlimited(self):
        self.authorize("Admin")
        self.settings.whatsapp_number = None
        self.execute.return_value.fetchone.return_value = {"balance": 0}
        self.assertEqual(self.client.get("/credits").json(), {"balance": 0, "unlimited": True, "price_myr": "1.00", "purchase_url": None})
        self.connect.assert_not_called()

    def test_assign_requires_admin_before_database_access(self):
        self.assertEqual(self.client.post("/admin/users/2/credits", json=self.body).status_code, 401)
        self.authorize()
        self.assertEqual(self.client.post("/admin/users/2/credits", json=self.body).status_code, 403)
        self.connect.assert_not_called()

    def test_positive_integer_amount_and_reference_are_required(self):
        self.authorize("Admin")
        for amount in (0, -1, 1.5, True, "10", 1000001):
            self.assertEqual(self.client.post("/admin/users/2/credits", json={**self.body, "amount": amount}).status_code, 422)
        for changes in ({"reference": " "}, {"request_id": "invalid"}, {"role": "Admin"}):
            self.assertEqual(self.client.post("/admin/users/2/credits", json={**self.body, **changes}).status_code, 422)
        self.connect.assert_not_called()

    def test_assignment_is_additive_and_audited_in_same_transaction(self):
        self.authorize("Admin")
        self.execute.return_value.fetchone.side_effect = [
            {"id": 2, "role": "User", "status": "Active"}, None, {"balance": 17},
        ]
        response = self.client.post("/admin/users/2/credits", json=self.body)
        self.assertEqual(response.json(), {"balance": 17, "assigned": 10, "duplicate": False})
        calls = self.execute.call_args_list
        self.assertIn("FOR UPDATE", calls[0].args[0])
        self.assertIn("credit_balance = credit_balance + %s", calls[2].args[0])
        self.assertEqual(calls[2].args[1], (10, 2, 10))
        self.assertIn("INSERT INTO credit_assignments", calls[3].args[0])
        self.assertEqual(calls[4].args[1][1], "user.credits_assigned")

    def test_duplicate_request_returns_current_balance_without_adding(self):
        self.authorize("Admin")
        self.execute.return_value.fetchone.side_effect = [
            {"id": 2, "role": "User", "status": "Active"},
            {"user_id": 2, "actor_user_id": 1, "amount": 10, "reference": "TEST PAYMENT"},
            {"balance": 17},
        ]
        response = self.client.post("/admin/users/2/credits", json=self.body)
        self.assertTrue(response.json()["duplicate"])
        self.assertFalse(any("credit_balance = credit_balance +" in call.args[0] for call in self.execute.call_args_list))

    def test_reused_request_with_different_payload_is_rejected(self):
        self.authorize("Admin")
        self.execute.return_value.fetchone.side_effect = [
            {"id": 2, "role": "User", "status": "Active"},
            {"user_id": 2, "actor_user_id": 1, "amount": 99, "reference": "OTHER"},
        ]
        self.assertEqual(self.client.post("/admin/users/2/credits", json=self.body).status_code, 409)

    def test_inactive_admin_missing_users_cannot_receive_assignments(self):
        self.authorize("Admin")
        for user in (None, {"id": 2, "role": "Admin", "status": "Active"},
                     {"id": 2, "role": "User", "status": "Suspended"},
                     {"id": 2, "role": "User", "status": "Deleted"}):
            self.execute.return_value.fetchone.return_value = user
            self.assertEqual(self.client.post("/admin/users/2/credits", json=self.body).status_code, 404 if user is None else 409)

    def test_database_errors_are_sanitized(self):
        self.authorize()
        self.connect.side_effect = psycopg.OperationalError("private credential")
        response = self.client.get("/credits")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)

    def test_stripe_routes_are_removed(self):
        for method, route in (("get", "/billing/subscription"), ("post", "/billing/checkout"),
                              ("post", "/billing/portal"), ("post", "/billing/webhook")):
            self.assertEqual(getattr(self.client, method)(route).status_code, 404)
