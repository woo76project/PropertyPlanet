from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app.auth import require_clerk_user
from app.main import app


class AccountDeletionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(app.dependency_overrides.clear)
        self.stack.enter_context(patch("app.account.get_settings", return_value=SimpleNamespace(
            db_url=SecretStr("unused"), clerk_secret_key=SecretStr("test"))))
        self.connect = self.stack.enter_context(patch("app.account.psycopg.connect"))
        self.execute = self.connect.return_value.__enter__.return_value.execute
        self.clerk = self.stack.enter_context(patch("app.account.Clerk"))
        self.clerk.return_value.__enter__.return_value.users.delete.return_value = SimpleNamespace(deleted=True)
        self.execute.return_value.fetchone.return_value = {"id": 2, "role": "User", "status": "Active"}
        app.dependency_overrides[require_clerk_user] = lambda: "user_self"
        self.client = TestClient(app)

    def delete(self, body=None):
        return self.client.request("DELETE", "/auth/account", json=body or {"confirmation": "DELETE"})

    def test_authentication_and_explicit_confirmation(self):
        app.dependency_overrides.clear()
        self.assertEqual(self.delete().status_code, 401)
        app.dependency_overrides[require_clerk_user] = lambda: "user_self"
        for body in ({"confirmation": "NO"}, {"confirmation": "DELETE", "user_id": 3}):
            self.assertEqual(self.delete(body).status_code, 422)
        self.connect.assert_not_called()

    def test_deletion_zeros_credits_and_removes_only_own_profile(self):
        self.assertEqual(self.delete().status_code, 204)
        calls = self.execute.call_args_list
        self.assertIn("FOR UPDATE", calls[1].args[0])
        self.assertEqual(calls[1].args[1], ("user_self",))
        self.assertIn("credit_balance = 0", calls[2].args[0])
        self.assertEqual(calls[2].args[1], (2,))
        self.assertIn("status = 'Deleting'", calls[2].args[0])
        self.assertIn("status = 'Deleted'", calls[3].args[0])
        self.assertFalse(any("DELETE FROM properties" in call.args[0] for call in calls))
        self.clerk.return_value.__enter__.return_value.users.delete.assert_called_once_with(user_id="user_self", timeout_ms=15000, retries=None)

    def test_suspended_and_pending_deletions_can_finish(self):
        for status in ("Suspended", "Deleting"):
            self.execute.return_value.fetchone.return_value = {"id": 2, "role": "User", "status": status}
            self.assertEqual(self.delete().status_code, 204)

    def test_admin_is_protected_and_deleted_request_is_idempotent(self):
        self.execute.return_value.fetchone.return_value = {"id": 1, "role": "Admin", "status": "Active"}
        self.assertEqual(self.delete().status_code, 403)
        self.clerk.assert_not_called()
        self.execute.return_value.fetchone.return_value = {"id": 2, "role": "User", "status": "Deleted"}
        self.assertEqual(self.delete().status_code, 204)
        self.clerk.assert_not_called()

    def test_credit_failure_blocks_external_deletion(self):
        self.execute.side_effect = lambda query, *args: (_ for _ in ()).throw(psycopg.OperationalError("private")) if "credit_balance = 0" in query else self.execute.return_value
        response = self.delete()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        self.clerk.assert_not_called()

    def test_unconfirmed_external_deletion_stays_pending(self):
        self.clerk.return_value.__enter__.return_value.users.delete.return_value = SimpleNamespace(deleted=False)
        self.assertEqual(self.delete().status_code, 503)
        self.assertFalse(any("status = 'Deleted'" in call.args[0] for call in self.execute.call_args_list))

    def test_account_eligibility_does_not_mutate_credits(self):
        self.assertEqual(self.client.get("/auth/account").json(), {"can_delete": True, "status": "Active"})
        self.assertEqual(self.execute.call_count, 1)
        self.assertTrue(self.execute.call_args.args[0].startswith("SELECT"))
