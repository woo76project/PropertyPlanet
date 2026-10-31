"""Opt-in PostgreSQL test: temporary tables only; Clerk remains mocked.

python -m unittest discover -s tests -p integration_account_deletion.py -v
"""
from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from clerk_backend_api.models import SDKError, DeletedObject
from fastapi import HTTPException
from psycopg.rows import dict_row

from app.account import delete_user_account
from app.core.config import get_settings


class AccountDeletionPostgresTests(unittest.TestCase):
    def test_migration_profile_cleanup_foreign_keys_and_pending_recovery(self):
        settings = get_settings()
        # Session-local tables require a direct connection. A transaction pooler
        # can retain them and lend that server session to the running application.
        params = conninfo_to_dict(settings.db_url.get_secret_value())
        if params.get("host", "").endswith(".neon.tech"):
            params["host"] = params["host"].replace("-pooler.", ".", 1)
        elif "pooler" in params.get("host", ""):
            self.skipTest("Temporary-table tests require a direct database connection")
        with psycopg.connect(make_conninfo(**params), row_factory=dict_row, connect_timeout=15) as connection:
            # Temporary names shadow real tables for this connection only.
            schema = Path("sql/schema.sql").read_text().replace("CREATE TABLE", "CREATE TEMP TABLE")
            schema = schema.replace("property_duplicate_text", "pg_temp.property_duplicate_text")
            connection.execute(schema)
            connection.execute("CREATE TEMP TABLE deletion_history (user_id BIGINT REFERENCES users(id))")
            connection.execute(
                "INSERT INTO users (clerk_user_id,username,email,first_name,last_name,phone) "
                "VALUES ('user_delete_test','agent','test@example.com','Ali','Ahmad','0123')",
            )
            connection.execute("INSERT INTO deletion_history VALUES (1)")
            connection.execute("UPDATE users SET credit_balance=7 WHERE id=1")
            connection.commit()  # All test tables are private temporary tables.

            @contextmanager
            def test_connection(*args, **kwargs):
                try:
                    yield connection
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

            with patch("app.account.psycopg.connect", side_effect=test_connection), patch("app.account.Clerk") as clerk:
                remote_delete = clerk.return_value.__enter__.return_value.users.delete
                remote_delete.side_effect = httpx.ConnectError("offline")
                with self.assertRaises(HTTPException) as caught:
                    delete_user_account("user_delete_test", settings)
                self.assertEqual(caught.exception.status_code, 503)
                row = connection.execute("SELECT * FROM users WHERE id=1").fetchone()
                self.assertEqual(row["status"], "Deleting")
                self.assertEqual(row["credit_balance"], 0)
                self.assertEqual(row["email"], "test@example.com")
                connection.commit()

                # Simulate Clerk already deleted the identity before a network failure.
                remote_delete.side_effect = SDKError("already removed", httpx.Response(404, text="missing"))
                delete_user_account("user_delete_test", settings)
                row = connection.execute("SELECT * FROM users WHERE id=1").fetchone()
                self.assertEqual(row["status"], "Deleted")
                self.assertEqual(row["email"], "deleted-user_delete_test@accounts.invalid")
                for field in ("username", "first_name", "last_name", "phone"):
                    self.assertIsNone(row[field])
                self.assertEqual(connection.execute("SELECT user_id FROM deletion_history").fetchone()["user_id"], 1)
                connection.commit()

                # A Clerk-only identity can close its account before onboarding.
                remote_delete.side_effect = None
                remote_delete.return_value = DeletedObject(object="user", deleted=True, id="user_no_profile")
                delete_user_account("user_no_profile", settings)
                row = connection.execute("SELECT status FROM users WHERE clerk_user_id='user_no_profile'").fetchone()
                self.assertEqual(row["status"], "Deleted")
                connection.commit()


if __name__ == "__main__":
    unittest.main()
