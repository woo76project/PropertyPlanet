import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import require_clerk_user, require_complete_profile
from app.core.config import Settings
from app.main import app, settings


class ApiSetupTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_frontend_origin_normalization(self):
        for value, expected in (
            (" http://LOCALHOST:3000/ ", "http://localhost:3000"),
            ("https://example.com:443/", "https://example.com"),
            ("http://[::1]:3000/", "http://[::1]:3000"),
        ):
            configured = Settings(_env_file=None, db_url="unused", frontend_url=value)
            self.assertEqual(configured.frontend_url, expected)
        for value in ("*", "localhost:3000", "https://example.com/app", "http://user:secret@example.com", "http://localhost:bad", "https://example.com?x=1"):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, db_url="unused", frontend_url=value)

    def test_browser_can_preflight_bearer_json_and_upload_requests(self):
        client = TestClient(app)
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            response = client.options("/properties/import-jobs", headers={
                "Origin": settings.frontend_url,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], settings.frontend_url)
        # CORS permission does not bypass authentication.
        response = client.get("/auth/me", headers={"Origin": settings.frontend_url})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["access-control-allow-origin"], settings.frontend_url)

    def test_other_browser_origins_are_not_allowed(self):
        client = TestClient(app)
        response = client.options("/auth/me", headers={
            "Origin": "https://untrusted.invalid", "Access-Control-Request-Method": "GET",
        })
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_download_filename_is_exposed_to_frontend(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1, "role": "Admin"}
        response = TestClient(app).get("/properties/import-template", headers={"Origin": settings.frontend_url})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Content-Disposition", response.headers["access-control-expose-headers"])

    def test_unhandled_database_failure_becomes_safe_503_with_cors(self):
        app.dependency_overrides[require_clerk_user] = lambda: "user_test"
        with patch("app.auth.psycopg.connect", side_effect=psycopg.OperationalError("private password and database host")), self.assertLogs("app.main", level="ERROR") as logs:
            response = TestClient(app).get("/auth/me", headers={"Origin": settings.frontend_url})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "Database is temporarily unavailable"})
            self.assertEqual(response.headers["access-control-allow-origin"], settings.frontend_url)
            self.assertNotIn("private", " ".join(logs.output))

    def test_existing_not_found_and_conflict_errors_stay_specific(self):
        app.dependency_overrides[require_clerk_user] = lambda: "user_test"
        with patch("app.auth.psycopg.connect") as connect:
            connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
            self.assertEqual(TestClient(app).get("/auth/me").status_code, 404)
        with patch("app.auth.psycopg.connect", side_effect=psycopg.errors.UniqueViolation("private")):
            response = TestClient(app).patch("/auth/profile", json={
                "username": "test_user", "first_name": "A", "last_name": "B", "phone": "+60123456789",
            })
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {"detail": "Username is already taken"})


if __name__ == "__main__":
    unittest.main()
