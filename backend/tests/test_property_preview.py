from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_clerk_user, require_complete_profile
from app.main import app


class PropertyPreviewTests(unittest.TestCase):
    def setUp(self):
        # Rate-limit behavior is tested separately with isolated memory counters.
        guard = patch("app.search_rate_limit.check_search_limit")
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_preview_requires_sign_in(self):
        with patch("app.properties.psycopg.connect") as connect:
            self.assertEqual(TestClient(app).get("/properties/preview").status_code, 401)
            connect.assert_not_called()

    def test_free_preview_excludes_private_fields_and_searches_only_public_fields(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
        with patch("app.properties.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.properties.psycopg.connect") as connect:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = [
                {"id": 9, "address": "KL",
                 "owner_name": "Private", "phone_01": "secret", "ic_number": "secret", "remark": "secret"},
                {"id": 8, "address": None},
            ]
            response = TestClient(app).get("/properties/preview", params={
                "search": " abc!%_ ", "before_id": 10, "limit": 1,
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "properties": [{"id": 9, "address": "KL"}],
                "pagination": {"limit": 1, "has_more": True, "next_before_id": 9},
            })
            query, values = execute.call_args.args
            for field in ("owner_name", "phone_01", "phone_02", "ic_number", "remark", "created_by", "updated_by"):
                self.assertNotIn(field, query)
                self.assertNotIn(field, response.text)
            self.assertEqual(values, ("%abc!!!%!_%", 10, 2))

    def test_preview_filters_are_optional_and_inputs_validated(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
        with patch("app.properties.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.properties.psycopg.connect") as connect:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = []
            client = TestClient(app)
            self.assertEqual(client.get("/properties/preview").json()["pagination"], {
                "limit": 20, "has_more": False, "next_before_id": None,
            })
            self.assertEqual(execute.call_args.args[1], (21,))
            for params in ({"limit": 0}, {"limit": 101}, {"before_id": 0}):
                self.assertEqual(client.get("/properties/preview", params=params).status_code, 422)

    def test_free_user_still_cannot_read_full_property_or_write(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
        with patch("app.properties.psycopg.connect") as connect:
            connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
            client = TestClient(app)
            for url in ("/properties", "/properties/1"):
                self.assertEqual(client.get(url).status_code, 403)
            self.assertEqual(client.delete("/properties/1").status_code, 403)

    def test_preview_rejects_incomplete_suspended_and_deleted_profiles(self):
        app.dependency_overrides[require_clerk_user] = lambda: "user_test"
        with patch("app.auth.psycopg.connect") as connect:
            result = connect.return_value.__enter__.return_value.execute.return_value
            for status in ("Suspended", "Deleting", "Deleted", "Active"):
                result.fetchone.return_value = {"id": 2, "status": status, "username": None}
                self.assertEqual(TestClient(app).get("/properties/preview").status_code, 403)


if __name__ == "__main__":
    unittest.main()
