from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_complete_profile
from app.main import app


class CreditAdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.addCleanup(app.dependency_overrides.clear)
        self.stack.enter_context(patch("app.credits.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))))
        self.connect = self.stack.enter_context(patch("app.credits.psycopg.connect"))
        self.execute = self.connect.return_value.__enter__.return_value.execute
        self.user = {"id": 2, "role": "User", "status": "Active", "credit_balance": 11}
        self.execute.return_value.fetchone.side_effect = [self.user, None]
        self.client = TestClient(app)
        self.body = {"request_id": str(uuid4()), "direction": "subtract", "amount": 1,
                     "reason": " Correction ", "expected_balance": 11}

    def authorize(self, role="Admin"):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1, "role": role}

    def post(self, **changes):
        return self.client.post("/admin/users/2/credits/adjustments", json={**self.body, **changes})

    def test_requires_admin_before_database_access(self):
        self.assertEqual(self.post().status_code, 401)
        self.authorize("User")
        self.assertEqual(self.post().status_code, 403)
        self.connect.assert_not_called()

    def test_subtracts_delta_and_records_before_after_reason_and_actor(self):
        self.authorize()
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"balance": 10, "adjusted": -1, "duplicate": False})
        calls = self.execute.call_args_list
        self.assertIn("FOR UPDATE", calls[0].args[0])
        self.assertEqual(calls[2].args[1], (-1, 2))
        self.assertEqual(calls[3].args[1], (UUID(self.body["request_id"]), 2, 1, -1, "Correction", 11, 10))
        self.assertEqual(calls[4].args[1][1], "user.credits_adjusted")

    def test_addition_and_subtraction_to_zero(self):
        self.authorize()
        for direction, amount, after in (("add", 2, 13), ("subtract", 11, 0)):
            self.execute.return_value.fetchone.side_effect = [self.user, None]
            response = self.post(direction=direction, amount=amount)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["balance"], after)

    def test_invalid_inputs_never_contact_database(self):
        self.authorize()
        for change in ({"amount": 0}, {"amount": -1}, {"amount": True}, {"amount": "1"}, {"amount": 1.5},
                       {"amount": 1000001}, {"direction": "set"}, {"reason": " "}, {"reason": "a" * 201},
                       {"expected_balance": -1}, {"expected_balance": True}, {"expected_balance": 1000000001},
                       {"request_id": "bad"}, {"balance": 10}):
            self.assertEqual(self.post(**change).status_code, 422, change)
        self.connect.assert_not_called()

    def test_missing_inactive_and_admin_targets_are_protected(self):
        self.authorize()
        for row in (None, {**self.user, "role": "Admin"}, *({**self.user, "status": s} for s in ("Suspended", "Deleting", "Deleted"))):
            self.execute.reset_mock()
            self.execute.return_value.fetchone.side_effect = [row]
            self.assertEqual(self.post().status_code, 404 if row is None else 409)
            self.assertEqual(self.execute.call_count, 1)

    def test_changed_balance_negative_result_and_overflow_do_not_update(self):
        self.authorize()
        for user, change in (({**self.user, "credit_balance": 10}, {}), (self.user, {"amount": 12}),
                             ({**self.user, "credit_balance": 1000000000}, {"direction": "add", "expected_balance": 1000000000})):
            self.execute.reset_mock()
            self.execute.return_value.fetchone.side_effect = [user, None]
            self.assertEqual(self.post(**change).status_code, 409)
            self.assertEqual(self.execute.call_count, 2)

    def test_matching_retry_is_free_even_if_balance_has_since_changed(self):
        self.authorize()
        prior = {"user_id": 2, "actor_user_id": 1, "delta": -1, "reason": "Correction", "balance_before": 11}
        self.execute.return_value.fetchone.side_effect = [{**self.user, "credit_balance": 8}, prior]
        response = self.post()
        self.assertEqual(response.json(), {"balance": 8, "adjusted": -1, "duplicate": True})
        self.assertEqual(self.execute.call_count, 2)

    def test_reused_request_with_different_payload_is_rejected(self):
        self.authorize()
        prior = {"user_id": 2, "actor_user_id": 1, "delta": -1, "reason": "Correction", "balance_before": 11}
        for change in ({"user_id": 3}, {"actor_user_id": 3}, {"delta": 1}, {"reason": "Other"}, {"balance_before": 10}):
            self.execute.return_value.fetchone.side_effect = [self.user, {**prior, **change}]
            self.assertEqual(self.post().status_code, 409)

    def test_failed_audit_rolls_back_update_and_receipt(self):
        self.authorize()
        with patch("app.credits.record_audit", side_effect=psycopg.OperationalError("private")):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        self.assertIsNotNone(self.connect.return_value.__exit__.call_args.args[0])
