from types import SimpleNamespace
import unittest
from unittest.mock import patch
import time

import jwt
from clerk_backend_api import AuthenticateRequestOptions
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_clerk_user
from app.main import app


def user(**changes):
    return {"id": 2, "clerk_user_id": "user_test", "username": "agent", "email": "agent@example.com",
            "first_name": "Ali", "last_name": "Ahmad", "phone": "0123",
            "role": "User", "status": "Active", **changes}


class AuthTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_real_sdk_rejects_bad_signature_expiry_and_frontend_origin(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        now = int(time.time())
        claims = {"sub": "user_test", "azp": "http://localhost:3000", "iat": now - 10,
                  "nbf": now - 10, "exp": now + 60, "sid": "sess_test"}
        settings = SimpleNamespace(clerk_secret_key=SecretStr("sk_test_dummy"), frontend_url="http://localhost:3000",
                                   db_url=SecretStr("unused"))
        def options(**kwargs):
            # Exercise real SDK cryptographic validation using a local key; no Clerk network calls.
            return AuthenticateRequestOptions(jwt_key=public_key, authorized_parties=kwargs["authorized_parties"],
                                              accepts_token=kwargs["accepts_token"])
        with patch("app.auth.get_settings", return_value=settings), patch("app.auth.AuthenticateRequestOptions", side_effect=options), patch("app.auth.psycopg.connect") as connect:
            client = TestClient(app)
            connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = user()
            token = jwt.encode(claims, private_key, algorithm="RS256")
            self.assertEqual(client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code, 200)
            invalid = (
                jwt.encode(claims, other_key, algorithm="RS256"),
                jwt.encode({**claims, "exp": now - 60}, private_key, algorithm="RS256"),
                jwt.encode({**claims, "azp": "https://untrusted.invalid"}, private_key, algorithm="RS256"),
            )
            for token in invalid:
                self.assertEqual(client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code, 401)
            self.assertEqual(connect.call_count, 1)

    def test_clerk_verification_options_and_invalid_identity(self):
        settings = SimpleNamespace(clerk_secret_key=SecretStr("sk_test_dummy"), frontend_url="http://localhost:3000")
        client = TestClient(app)
        with patch("app.auth.get_settings", return_value=settings), patch("app.auth.authenticate_request") as verify, patch("app.auth.psycopg.connect") as connect:
            for payload in ({"sub": "bad_id"}, {"sub": 123}, {}, None):
                verify.return_value = SimpleNamespace(is_signed_in=True, payload=payload)
                self.assertEqual(client.get("/auth/me", headers={"Authorization": "Bearer fake_token"}).status_code, 401)
            verify.return_value = SimpleNamespace(is_signed_in=False, payload={"sub": "user_test"})
            self.assertEqual(client.get("/auth/me", headers={"Authorization": "Bearer fake_token"}).status_code, 401)
            request, options = verify.call_args.args
            self.assertEqual(options.authorized_parties, ["http://localhost:3000"])
            self.assertEqual(options.accepts_token, ["session_token"])
            connect.assert_not_called()

    def test_suspended_account_cannot_access_product_admin_or_billing(self):
        app.dependency_overrides[require_clerk_user] = lambda: "user_test"
        with patch("app.auth.psycopg.connect") as connect:
            connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = user(status="Suspended")
            client = TestClient(app)
            for method, path, kwargs in (
                ("get", "/auth/me", {}), ("post", "/auth/profile", {}),
                ("get", "/properties", {}), ("get", "/properties/1", {}),
                ("get", "/credits", {}), ("post", "/properties/search", {"json": {}}),
                ("get", "/admin/users", {}),
                ("get", "/admin/audit-logs", {}),
                ("post", "/properties/import-jobs", {"json": {"filename": "a.csv", "file_size": 1}}),
            ):
                self.assertEqual(client.request(method, path, **kwargs).status_code, 403, path)

    def test_incomplete_profiles_can_read_identity_but_not_property_data(self):
        app.dependency_overrides[require_clerk_user] = lambda: "user_test"
        with patch("app.auth.psycopg.connect") as connect:
            client = TestClient(app)
            result = connect.return_value.__enter__.return_value.execute.return_value
            for field in ("username", "first_name", "last_name", "phone"):
                result.fetchone.return_value = user(**{field: " "})
                self.assertEqual(client.get("/auth/me").status_code, 200)
                response = client.get("/properties")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"], "Complete your profile first")

    def test_existing_profile_checks_clerk_username_without_inserting(self):
        app.dependency_overrides[require_clerk_user] = lambda: "user_test"
        with patch("app.auth.psycopg.connect") as connect, patch("app.auth.clerk_profile", return_value=SimpleNamespace(username="agent")) as clerk:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchone.return_value = user()
            response = TestClient(app).post("/auth/profile", json={"role": "Admin"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["role"], "User")
            execute.assert_called_once()
            clerk.assert_called_once()

    def test_new_profile_uses_verified_clerk_identity_and_forces_user_role(self):
        app.dependency_overrides[require_clerk_user] = lambda: "user_test"
        settings = SimpleNamespace(db_url=SecretStr("unused"), clerk_secret_key=SecretStr("sk_test_dummy"))
        identity = SimpleNamespace(
            banned=False, locked=False, primary_email_address_id="email_1", username=None,
            first_name="Ali", last_name="Ahmad", email_addresses=[SimpleNamespace(
                id="email_1", email_address="ALI@EXAMPLE.COM", verification=SimpleNamespace(status="verified"),
            )],
        )
        with patch("app.auth.get_settings", return_value=settings), patch("app.auth.psycopg.connect") as connect, patch("app.auth.Clerk") as clerk:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchone.side_effect = [None, user(username=None, phone=None)]
            clerk.return_value.__enter__.return_value.users.get.return_value = identity
            response = TestClient(app).post("/auth/profile", json={"role": "Admin", "email": "attacker@example.com"})
            self.assertEqual(response.status_code, 200)
            insert = execute.call_args_list[1]
            self.assertIn("'User', 'Active'", insert.args[0])
            self.assertEqual(insert.args[1][2], "ali@example.com")
            self.assertNotIn("attacker@example.com", insert.args[1])

    def test_profile_update_rejects_role_and_status_changes(self):
        app.dependency_overrides[require_clerk_user] = lambda: "user_test"
        valid = {"username": "agent", "first_name": "A", "last_name": "B", "phone": "+60123456789"}
        with patch("app.auth.psycopg.connect") as connect:
            for changes in ({"role": "Admin"}, {"status": "Active"}, {"username": "a"}):
                response = TestClient(app).patch("/auth/profile", json={**valid, **changes})
                self.assertEqual(response.status_code, 422)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
