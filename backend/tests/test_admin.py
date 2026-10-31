import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_complete_profile
from app.main import app


class AdminUsersTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def authorize_admin(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1, "role": "Admin"}

    def test_requires_sign_in_and_admin_role(self):
        with patch("app.admin.psycopg.connect") as connect: #replace with a mock so we don't actually connect to the database
            client = TestClient(app)
            self.assertEqual(client.get("/admin/users").status_code, 401)
            app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"} #give fake data
            self.assertEqual(client.get("/admin/users").status_code, 403)
            connect.assert_not_called()

    def test_filters_are_parameterized_and_cursor_excludes_extra_row(self):
        self.authorize_admin()
        with patch("app.admin.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.admin.psycopg.connect") as connect:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = [{"id": 9}, {"id": 8}, {"id": 7}]
            response = TestClient(app).get("/admin/users", params={
                "search": " a!%_' ", "status": "Suspended", "before_id": 10, "limit": 2,
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "users": [{"id": 9}, {"id": 8}],
                "pagination": {"limit": 2, "has_more": True, "next_before_id": 8},
            })
            query, values = execute.call_args.args
            self.assertNotIn("a!%_'", query)
            self.assertIn("status = %s AND id < %s", query)
            self.assertEqual(values, tuple(["%a!!!%!_'%"] * 5 + ["Suspended", 10, 3]))

    def test_empty_page_and_invalid_parameters(self):
        self.authorize_admin()
        with patch("app.admin.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.admin.psycopg.connect") as connect:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = []
            client = TestClient(app)
            response = client.get("/admin/users", params={"search": "   "})
            self.assertEqual(response.json(), {
                "users": [], "pagination": {"limit": 20, "has_more": False, "next_before_id": None},
            })
            query, values = execute.call_args.args
            self.assertNotIn("FROM users WHERE", query)
            self.assertEqual(values, (21,))
            for params in ({"limit": 0}, {"limit": 101}, {"before_id": 0}, {"status": "Deleted"}, {"search": "a" * 201}):
                self.assertEqual(client.get("/admin/users", params=params).status_code, 422)
            self.assertEqual(execute.call_count, 1)

    def test_database_failure_returns_generic_error(self):
        self.authorize_admin()
        with patch("app.admin.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.admin.psycopg.connect", side_effect=psycopg.OperationalError("private connection details")):
            response = TestClient(app).get("/admin/users")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "User list is temporarily unavailable"})

    def test_status_update_requires_admin(self):
        with patch("app.admin.psycopg.connect") as connect:
            client = TestClient(app)
            self.assertEqual(client.patch("/admin/users/2/status", json={"status": "Suspended"}).status_code, 401)
            app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
            self.assertEqual(client.patch("/admin/users/3/status", json={"status": "Active"}).status_code, 403)
            connect.assert_not_called()





    def test_suspend_and_reactivate_user(self):
        self.authorize_admin()
        for status in ("Suspended", "Active"):
            with self.subTest(status=status), patch("app.admin.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.admin.psycopg.connect") as connect:
                execute = connect.return_value.__enter__.return_value.execute
                execute.return_value.fetchone.side_effect = [
                    {"id": 2, "role": "User", "status": "Active" if status == "Suspended" else "Suspended"},
                    {"id": 2, "role": "User", "status": status},
                ]
                response = TestClient(app).patch("/admin/users/2/status", json={"status": status})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], status)
                self.assertIn("FOR UPDATE", execute.call_args_list[0].args[0])
                self.assertEqual(execute.call_args_list[0].args[1], (2,))
                query, values = execute.call_args_list[1].args
                self.assertIn("role = 'User'", query)
                self.assertEqual(values, (status, 2))

    def test_missing_user_and_admin_target_are_not_updated(self):
        self.authorize_admin()
        for target, code in ((None, 404), ({"id": 1, "role": "Admin"}, 403)):
            with self.subTest(code=code), patch("app.admin.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.admin.psycopg.connect") as connect:
                execute = connect.return_value.__enter__.return_value.execute
                execute.return_value.fetchone.return_value = target
                response = TestClient(app).patch("/admin/users/1/status", json={"status": "Suspended"})
                self.assertEqual(response.status_code, code)
                self.assertEqual(execute.call_count, 1)
                self.assertIsNotNone(connect.return_value.__exit__.call_args.args[0])

    def test_status_update_rejects_invalid_body_and_id(self):
        self.authorize_admin()
        with patch("app.admin.psycopg.connect") as connect:
            client = TestClient(app)
            for body in ({}, {"status": "Deleted"}, {"status": None}, {"status": "Active", "role": "Admin"}):
                self.assertEqual(client.patch("/admin/users/2/status", json=body).status_code, 422)
            self.assertEqual(client.patch("/admin/users/0/status", json={"status": "Active"}).status_code, 422)
            connect.assert_not_called()

    def test_status_update_database_error_is_generic(self):
        self.authorize_admin()
        with patch("app.admin.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.admin.psycopg.connect", side_effect=psycopg.OperationalError("private connection details")):
            response = TestClient(app).patch("/admin/users/2/status", json={"status": "Suspended"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "User status update is temporarily unavailable"})


if __name__ == "__main__":
    unittest.main()
