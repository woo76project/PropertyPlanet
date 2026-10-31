from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import psycopg
from clerk_backend_api.models import SDKError
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_clerk_user
from app.main import app


class ProfileOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(db_url=SecretStr("unused"), clerk_secret_key=SecretStr("unused"))
        self.patches = [patch("app.auth.get_settings", return_value=self.settings),
                        patch("app.auth.psycopg.connect"), patch("app.auth.Clerk")]
        _, self.connect, self.clerk = [p.start() for p in self.patches]
        self.connection = self.connect.return_value.__enter__.return_value
        self.execute = self.connection.execute
        self.remote = self.clerk.return_value.__enter__.return_value.users
        self.remote.get.return_value = SimpleNamespace(username=None, banned=False, locked=False)
        self.remote.update.return_value = SimpleNamespace(username="agent_new")
        self.active = {"id": 2, "clerk_user_id": "user_test", "status": "Active", "role": "User", "username": None}
        self.saved = {**self.active, "username": "agent_new", "first_name": "Ali", "last_name": "Ahmad", "phone": "+60123456789"}
        self.execute.return_value.fetchone.side_effect = [self.active, None, self.saved]
        self.body = {"username": " AGENT_NEW ", "first_name": " Ali ", "last_name": " Ahmad ", "phone": " +60 12-345 6789 "}
        app.dependency_overrides[require_clerk_user] = lambda: "user_test"
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        for p in reversed(self.patches):
            p.stop()

    def save(self):
        return self.client.patch("/auth/profile", json=self.body)

    def test_saves_canonical_username_to_clerk_before_neon_and_trims_details(self):
        def update(**kwargs):
            self.assertEqual(self.execute.call_count, 2)  # Only lock and conflict check so far.
            return SimpleNamespace(username="agent_new")
        self.remote.update.side_effect = update
        response = self.save()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "agent_new")
        self.remote.update.assert_called_once_with(user_id="user_test", username="agent_new", timeout_ms=15000, retries=None)
        self.assertIn("FOR UPDATE", self.execute.call_args_list[0].args[0])
        self.assertEqual(self.execute.call_args_list[1].args[1], ("agent_new", 2))
        self.assertEqual(self.execute.call_args.args[1], ("agent_new", "Ali", "Ahmad", "+60123456789", "user_test"))

    def test_retry_with_matching_clerk_username_does_not_change_it_again(self):
        self.remote.get.return_value.username = "agent_new"
        self.assertEqual(self.save().status_code, 200)
        self.remote.update.assert_not_called()

    def test_neon_conflict_is_rejected_before_clerk_changes(self):
        self.execute.return_value.fetchone.side_effect = [self.active, {"id": 9}]
        self.assertEqual(self.save().status_code, 409)
        self.clerk.assert_not_called()
        self.assertEqual(self.execute.call_count, 2)

    def test_inactive_and_missing_profiles_never_contact_clerk(self):
        for row, status in ((None, 404), ({**self.active, "status": "Suspended"}, 403),
                            ({**self.active, "status": "Deleting"}, 403), ({**self.active, "status": "Deleted"}, 403)):
            self.execute.return_value.fetchone.side_effect = [row]
            self.assertEqual(self.save().status_code, status)
        self.clerk.assert_not_called()

    def test_clerk_duplicate_and_validation_errors_are_safe_and_do_not_save_neon(self):
        for code, duplicate, expected in ((422, True, 409), (422, False, 422), (500, False, 503)):
            with self.subTest(code=code, duplicate=duplicate):
                self.execute.reset_mock()
                self.execute.return_value.fetchone.side_effect = [self.active, None]
                error = SDKError("private provider data", httpx.Response(code, text="private"))
                error.data = SimpleNamespace(errors=[SimpleNamespace(code="form_identifier_exists")] if duplicate else [])
                self.remote.update.side_effect = error
                response = self.save()
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("private", response.text)
                self.assertEqual(self.execute.call_count, 2)
                self.assertIsNotNone(self.connect.return_value.__exit__.call_args.args[0])

    def test_clerk_network_failure_is_retryable_without_neon_update(self):
        self.remote.update.side_effect = httpx.ConnectError("private")
        self.assertEqual(self.save().status_code, 503)
        self.assertEqual(self.execute.call_count, 2)

    def test_unconfirmed_username_never_updates_neon(self):
        self.remote.update.return_value.username = "something_else"
        self.assertEqual(self.save().status_code, 503)
        self.assertEqual(self.execute.call_count, 2)

    def test_neon_failure_after_clerk_change_warns_that_retry_is_required(self):
        def execute(query, values):
            if "UPDATE users" in query:
                raise psycopg.OperationalError("private database credentials")
            return self.execute.return_value
        self.execute.side_effect = execute
        response = self.save()
        self.assertEqual(response.status_code, 503)
        self.assertIn("username may already have changed", response.json()["detail"])
        self.assertNotIn("private", response.text)
        self.remote.update.assert_called_once()

    def test_reloading_profile_repairs_username_copy_and_null_requires_onboarding(self):
        for username in ("canonical_name", None):
            self.remote.get.return_value.username = username
            self.execute.reset_mock()
            self.execute.return_value.fetchone.side_effect = [
                {**self.active, "username": "old_name"}, {**self.saved, "username": username},
            ]
            response = self.client.post("/auth/profile")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["username"], username)
            self.assertEqual(self.execute.call_args.args[1], (username, 2))
            self.assertIn("FOR UPDATE", self.execute.call_args_list[0].args[0])
            self.assertFalse(any("INSERT" in call.args[0] for call in self.execute.call_args_list))

    def test_invalid_fields_and_password_role_status_injection_rejected_before_services(self):
        for extra in ({"username": "abc"}, {"username": "a" * 51}, {"username": "bad name"},
                      {"first_name": " "}, {"phone": " "}, {"password": "not_accepted"},
                      {"role": "Admin"}, {"status": "Active"}):
            self.assertEqual(self.client.patch("/auth/profile", json={**self.body, **extra}).status_code, 422)
        self.connect.assert_not_called()
        self.clerk.assert_not_called()

    def test_blocked_clerk_identity_cannot_complete_or_reload_profile(self):
        self.remote.get.return_value.banned = True
        self.assertEqual(self.save().status_code, 403)
        self.execute.return_value.fetchone.side_effect = [self.active]
        self.assertEqual(self.client.post("/auth/profile").status_code, 403)
        self.remote.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
