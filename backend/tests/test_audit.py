import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_complete_profile
from app.audit import record_audit
from app.main import app
from app.properties import PropertyCreate, PropertyUpdate, create_property, delete_property, update_property
from app.property_files import save_properties


def settings():
    return SimpleNamespace(db_url=SecretStr("unused"))


class AuditTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def authorize_admin(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1, "role": "Admin"}

    def test_writer_uses_existing_connection_and_json_parameters(self):
        connection = MagicMock()
        record_audit(connection, 1, "property.updated", "property", 9, {"fields": ["owner_name"]})
        query, values = connection.execute.call_args.args
        self.assertIn("INSERT INTO audit_logs", query)
        self.assertEqual(values[:4], (1, "property.updated", "property", 9))
        self.assertEqual(values[4].obj, {"fields": ["owner_name"]})
        connection.commit.assert_not_called()

    def test_history_requires_admin(self):
        with patch("app.audit.psycopg.connect") as connect:
            client = TestClient(app)
            self.assertEqual(client.get("/admin/audit-logs").status_code, 401)
            app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
            self.assertEqual(client.get("/admin/audit-logs").status_code, 403)
            connect.assert_not_called()

    def test_history_filters_and_pagination(self):
        self.authorize_admin()
        with patch("app.audit.get_settings", return_value=settings()), patch("app.audit.psycopg.connect") as connect:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = [{"id": 9}, {"id": 8}, {"id": 7}]
            response = TestClient(app).get("/admin/audit-logs", params={
                "action": "property.deleted", "actor_user_id": 1, "before_id": 10, "limit": 2,
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"audit_logs": [{"id": 9}, {"id": 8}],
                "pagination": {"limit": 2, "has_more": True, "next_before_id": 8}})
            query, values = execute.call_args.args
            self.assertNotIn("property.deleted", query)
            self.assertEqual(values, ("property.deleted", 1, 10, 3))
            execute.return_value.fetchall.return_value = []
            empty = TestClient(app).get("/admin/audit-logs").json()
            self.assertEqual(empty["audit_logs"], [])
            self.assertIsNone(empty["pagination"]["next_before_id"])

    def test_history_category_filters_include_all_actions_for_that_entity(self):
        self.authorize_admin()
        with patch("app.audit.get_settings", return_value=settings()), patch("app.audit.psycopg.connect") as connect:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = []
            for category, entity in (("properties", "property"), ("users", "user")):
                response = TestClient(app).get("/admin/audit-logs", params={
                    "category": category, "actor_user_id": 1, "before_id": 10, "limit": 2,
                })
                self.assertEqual(response.status_code, 200)
                query, values = execute.call_args.args
                self.assertIn("a.entity_type = %s", query)
                self.assertNotIn("a.action = %s", query)
                self.assertEqual(values, (entity, 1, 10, 3))

    def test_history_username_search_is_trimmed_literal_and_parameterized(self):
        self.authorize_admin()
        with patch("app.audit.get_settings", return_value=settings()), patch("app.audit.psycopg.connect") as connect:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = []
            client = TestClient(app)
            response = client.get("/admin/audit-logs", params={"actor_username": "  Patrick_%'  ", "category": "users", "before_id": 10})
            self.assertEqual(response.status_code, 200)
            query, values = execute.call_args.args
            self.assertIn("strpos(lower(u.username), lower(%s)) > 0", query)
            self.assertNotIn("Patrick", query)
            self.assertEqual(values, ("user", "Patrick_%'", 10, 21))
            self.assertEqual(client.get("/admin/audit-logs", params={"actor_username": "x" * 201}).status_code, 422)
            client.get("/admin/audit-logs", params={"actor_username": "   "})
            self.assertNotIn("strpos", execute.call_args.args[0])

    def test_history_rejects_invalid_filters_and_hides_db_errors(self):
        self.authorize_admin()
        with patch("app.audit.get_settings", return_value=settings()), patch("app.audit.psycopg.connect") as connect:
            client = TestClient(app)
            for params in ({"category": "unknown"}, {"action": "unknown"}, {"actor_user_id": 0}, {"before_id": 0}, {"limit": 101}):
                self.assertEqual(client.get("/admin/audit-logs", params=params).status_code, 422)
            connect.assert_not_called()
            connect.side_effect = psycopg.OperationalError("private db details")
            response = client.get("/admin/audit-logs")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "Audit history is temporarily unavailable"})

    def test_property_changes_record_actor_id_and_action(self):
        data = PropertyCreate(owner_name="Ali", phone_01="0123")
        with patch("app.properties.get_settings", return_value=settings()), patch("app.properties.psycopg.connect") as connect:
            connection = connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchone.return_value = {"id": 9}
            create_property(data, {"id": 1})
            self.assertEqual(connection.execute.call_args.args[1][:4], (1, "property.created", "property", 9))
            update_property(PropertyUpdate(owner_name="New owner"), 9, {"id": 1})
            values = connection.execute.call_args.args[1]
            self.assertEqual(values[:4], (1, "property.updated", "property", 9))
            self.assertEqual(values[4].obj, {"fields": ["owner_name"]})
            delete_property(9, {"id": 1})
            self.assertEqual(connection.execute.call_args.args[1][:4], (1, "property.deleted", "property", 9))

    def test_missing_property_does_not_write_success_log(self):
        for operation in (lambda: delete_property(9, {"id": 1}),
                          lambda: update_property(PropertyUpdate(owner_name="New"), 9, {"id": 1})):
            with patch("app.properties.get_settings", return_value=settings()), patch("app.properties.psycopg.connect") as connect:
                connection = connect.return_value.__enter__.return_value
                connection.execute.return_value.fetchone.return_value = None
                with self.assertRaises(HTTPException) as caught:
                    operation()
                self.assertEqual(caught.exception.status_code, 404)
                self.assertEqual(connection.execute.call_count, 1)

    def test_property_audit_failure_exits_transaction_with_error(self):
        data = PropertyCreate(owner_name="Ali", phone_01="0123")
        operations = (
            lambda: create_property(data, {"id": 1}),
            lambda: update_property(PropertyUpdate(owner_name="New"), 9, {"id": 1}),
            lambda: delete_property(9, {"id": 1}),
        )
        for operation in operations:
            with patch("app.properties.get_settings", return_value=settings()), patch("app.properties.psycopg.connect") as connect, patch("app.properties.record_audit", side_effect=psycopg.OperationalError("audit failed")) as audit:
                connection = connect.return_value.__enter__.return_value
                connection.execute.return_value.fetchone.return_value = {"id": 9}
                with self.assertRaises(psycopg.OperationalError):
                    operation()
                self.assertIs(audit.call_args.args[0], connection)
                self.assertIsNotNone(connect.return_value.__exit__.call_args.args[0])

    def test_status_no_op_is_not_logged_and_audit_failure_aborts(self):
        self.authorize_admin()
        with patch("app.admin.get_settings", return_value=settings()), patch("app.admin.psycopg.connect") as connect:
            connection = connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchone.return_value = {"id": 2, "role": "User", "status": "Active"}
            response = TestClient(app).patch("/admin/users/2/status", json={"status": "Active"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(connection.execute.call_count, 1)
            connection.execute.return_value.fetchone.side_effect = [
                {"id": 2, "role": "User", "status": "Active"},
                {"id": 2, "role": "User", "status": "Suspended"},
            ]
            with patch("app.admin.record_audit", side_effect=psycopg.OperationalError("audit failed")) as audit:
                response = TestClient(app).patch("/admin/users/2/status", json={"status": "Suspended"})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(audit.call_args.args[1:5], (1, "user.suspended", "user", 2))
                self.assertIsNotNone(connect.return_value.__exit__.call_args.args[0])

    def test_import_logs_count_and_audit_failure_aborts(self):
        data = PropertyCreate(owner_name="Ali", phone_01="0123")
        with patch("app.property_files.get_settings", return_value=settings()), patch("app.property_files.psycopg.connect") as connect:
            connection = connect.return_value.__enter__.return_value
            connection.cursor.return_value.__enter__.return_value.rowcount = 1
            self.assertEqual(save_properties([data], 1), {"imported": 1, "duplicates_skipped": 0,
                                                       "duplicates_in_file": 0, "already_in_database": 0})
            values = connection.execute.call_args.args[1]
            self.assertEqual(values[:4], (1, "properties.imported", "property", None))
            self.assertEqual(values[4].obj, {"count": 1, "duplicates_skipped": 0})
            with patch("app.property_files.record_audit", side_effect=psycopg.OperationalError("audit failed")):
                with self.assertRaises(HTTPException) as caught:
                    save_properties([data], 1)
                self.assertEqual(caught.exception.status_code, 503)
                self.assertIsNotNone(connect.return_value.__exit__.call_args.args[0])

    def test_export_logs_only_after_workbook_prepared(self):
        self.authorize_admin()
        with patch("app.property_files.get_settings", return_value=settings()), patch("app.property_files.psycopg.connect") as connect:
            connection = connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchall.return_value = []
            response = TestClient(app).get("/properties/export", params={"search": "Ali private name"})
            self.assertEqual(response.status_code, 200)
            values = connection.execute.call_args.args[1]
            self.assertEqual(values[:4], (1, "properties.exported", "property", None))
            self.assertEqual(values[4].obj, {"count": 0, "search_used": True})
            with patch("app.property_files.excel_response", side_effect=HTTPException(status_code=422, detail="Invalid export data")), patch("app.property_files.record_audit") as audit:
                self.assertEqual(TestClient(app).get("/properties/export").status_code, 422)
                audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
