"""Validate the complete V3 schema using temporary tables, then roll back.

Run from backend: python -m unittest discover -s tests -p integration_schema.py -v
"""
from pathlib import Path
from contextlib import contextmanager
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg import sql
from psycopg.rows import dict_row

from app.core.config import get_settings


class CompleteSchemaTests(unittest.TestCase):
    def test_streamed_excel_job_commits_checkpoints_counts_errors_and_finishes(self):
        from app.import_worker import process_job
        from test_import_excel import shared_workbook
        from test_property_files import VALID_ROW
        params = conninfo_to_dict(get_settings().db_url.get_secret_value())
        if "-pooler" in params.get("host", ""):
            params["host"] = params["host"].replace("-pooler", "")
        with psycopg.connect(**params, row_factory=dict_row, connect_timeout=15) as connection:
            try:
                connection.execute("SET LOCAL statement_timeout = '30s'")
                source = Path("sql/schema.sql").read_text().replace("CREATE TABLE", "CREATE TEMP TABLE").replace("property_duplicate_text", "pg_temp.property_duplicate_text")
                connection.execute(source)
                connection.execute("INSERT INTO users (clerk_user_id,email,role) VALUES ('import_test','import@example.invalid','Admin')")
                job = {"id": uuid4(), "created_by": 1, "worker_token": uuid4(),
                       "filename": "test.xlsx", "last_row": 1, "storage_bucket": None}
                connection.execute("INSERT INTO property_import_jobs (id,created_by,filename,file_size,status,worker_token,lease_until) VALUES (%s,1,'test.xlsx',1,'processing',%s,NOW()+INTERVAL '5 minutes')", (job["id"], job["worker_token"]))

                @contextmanager
                def temporary_connection():
                    with connection.transaction():
                        yield connection

                def assemble(job, path, **kwargs):
                    bad = list(VALID_ROW)
                    bad[0] = ""
                    path.write_bytes(shared_workbook([VALID_ROW, VALID_ROW, bad, [None] * 6]))

                with patch("app.import_worker.import_connection", side_effect=temporary_connection), patch(
                    "app.import_worker.assemble_file", side_effect=assemble
                ), patch("app.import_worker.ImportStorage"):
                    self.assertTrue(process_job(job))
                result = connection.execute("SELECT status,total_records,processed_rows,imported_count,duplicate_count,invalid_count,last_row,files_deleted_at FROM property_import_jobs").fetchone()
                self.assertEqual({key: result[key] for key in result if key != "files_deleted_at"},
                                 {"status": "completed_with_errors", "total_records": 3, "processed_rows": 3,
                                  "imported_count": 1, "duplicate_count": 1, "invalid_count": 1, "last_row": 4})
                self.assertIsNotNone(result["files_deleted_at"])
                self.assertEqual(connection.execute("SELECT row_number FROM property_import_errors").fetchone()["row_number"], 4)
                self.assertEqual(connection.execute("SELECT phone_01,ic_number FROM properties").fetchone(),
                                 {"phone_01": "0123456789", "ic_number": "001234567890"})
            finally:
                connection.rollback()

    def test_six_field_import_batch_saves_remarks_and_skips_full_row_duplicates(self):
        from app.import_worker import process_batch
        from app.properties import PropertyCreate
        params = conninfo_to_dict(get_settings().db_url.get_secret_value())
        if "-pooler" in params.get("host", ""):
            params["host"] = params["host"].replace("-pooler", "")
        with psycopg.connect(**params, row_factory=dict_row, connect_timeout=15) as connection:
            try:
                connection.execute("SET LOCAL statement_timeout = '30s'")
                source = Path("sql/schema.sql").read_text().replace("CREATE TABLE", "CREATE TEMP TABLE").replace("property_duplicate_text", "pg_temp.property_duplicate_text")
                connection.execute(source)
                connection.execute("INSERT INTO users (clerk_user_id,email,role) VALUES ('import_test','import@example.invalid','Admin')")
                job = {"id": uuid4(), "created_by": 1, "worker_token": uuid4()}
                connection.execute("INSERT INTO property_import_jobs (id,created_by,filename,file_size,status,worker_token,lease_until) VALUES (%s,1,'new.csv',1,'processing',%s,NOW()+INTERVAL '5 minutes')", (job["id"], job["worker_token"]))
                record = PropertyCreate(owner_name="Test owner", phone_01="00123", phone_02="00456", ic_number="000123", address="Address", remark="First line\nSecond line")
                @contextmanager
                def temporary_connection():
                    with connection.transaction():
                        yield connection
                with patch("app.import_worker.import_connection", side_effect=temporary_connection):
                    process_batch(job, [(2, record, []), (3, record, [])])
                self.assertEqual(connection.execute("SELECT owner_name,phone_01,phone_02,ic_number,address,remark FROM properties").fetchone(), record.model_dump())
                self.assertEqual(connection.execute("SELECT imported_count,duplicate_count,last_row FROM property_import_jobs").fetchone(), {"imported_count": 1, "duplicate_count": 1, "last_row": 3})
            finally:
                connection.rollback()

    def test_fresh_schema_supports_credit_adjustments_and_audit_history(self):
        params = conninfo_to_dict(get_settings().db_url.get_secret_value())
        if "-pooler" in params.get("host", ""):
            params["host"] = params["host"].replace("-pooler", "")
        with psycopg.connect(**params, connect_timeout=15) as connection:
            try:
                connection.execute("SET LOCAL statement_timeout = '30s'")
                source = Path("sql/schema.sql").read_text()
                connection.execute(source.replace("CREATE TABLE", "CREATE TEMP TABLE").replace("property_duplicate_text", "pg_temp.property_duplicate_text"))
                connection.execute("INSERT INTO users (clerk_user_id,email,role,credit_balance) VALUES ('test_admin','a@example.invalid','Admin',0),('test_user','u@example.invalid','User',7)")
                connection.execute("INSERT INTO credit_assignments (request_id,user_id,actor_user_id,amount,reference,balance_after) VALUES ('00000000-0000-0000-0000-000000000001',2,1,7,'TEST',7)")
                self.assertEqual(connection.execute("SELECT credit_balance FROM users WHERE id=2").fetchone()[0], 7)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM credit_assignments").fetchone()[0], 1)
                connection.execute("INSERT INTO audit_logs (actor_user_id,action,entity_type,entity_id) VALUES (1,'user.credits_adjusted','user',2),(1,'user.credits_assigned','user',2)")
                connection.execute("INSERT INTO credit_adjustments (request_id,user_id,actor_user_id,delta,reason,balance_before,balance_after) VALUES ('00000000-0000-0000-0000-000000000002',2,1,-1,'TEST CORRECTION',7,6)")
                with self.assertRaises(psycopg.errors.CheckViolation), connection.transaction():
                    connection.execute("UPDATE credit_adjustments SET balance_after=-1")
            finally:
                connection.rollback()

    def test_empty_schema_final_constraints_and_search_indexes(self):
        params = conninfo_to_dict(get_settings().db_url.get_secret_value())
        if "-pooler" in params.get("host", ""):
            params["host"] = params["host"].replace("-pooler", "")
        with psycopg.connect(**params, connect_timeout=15) as connection:
            try:
                connection.execute("SET LOCAL statement_timeout = '30s'")
                source = Path("sql/schema.sql").read_text()
                temporary = source.replace("CREATE TABLE", "CREATE TEMP TABLE").replace(
                    "property_duplicate_text", "pg_temp.property_duplicate_text")
                connection.execute(temporary)
                tables = {row[0] for row in connection.execute(
                    "SELECT relname FROM pg_class WHERE relnamespace = pg_my_temp_schema() AND relkind = 'r'"
                ).fetchall()}
                expected = {"users", "properties", "audit_logs", "property_import_jobs",
                            "property_import_chunks", "property_import_errors",
                            "credit_assignments", "credit_adjustments", "credit_searches"}
                self.assertEqual(tables, expected)
                for table in expected:
                    count = connection.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier("pg_temp", table))).fetchone()[0]
                    self.assertEqual(count, 0, table)
                user_id = connection.execute(
                    "INSERT INTO users (clerk_user_id,email) VALUES ('user_schema_test','schema@example.invalid') RETURNING id"
                ).fetchone()[0]
                self.assertEqual(connection.execute("SELECT role,status,credit_balance FROM users WHERE id=%s", (user_id,)).fetchone(), ("User", "Active", 0))
                for state in ("Deleting", "Deleted", "Active"):
                    connection.execute("UPDATE users SET status=%s WHERE id=%s", (state, user_id))
                connection.execute(
                    "INSERT INTO audit_logs (actor_user_id,action,entity_type,entity_id) VALUES (%s,'user.credits_assigned','user',%s)",
                    (user_id, user_id),
                )
                connection.execute(
                    "INSERT INTO properties (owner_name,phone_01,address,created_by) "
                    "VALUES ('Test','0123','Garden',%s)", (user_id,),
                )
                with self.assertRaises(psycopg.errors.UniqueViolation), connection.transaction():
                    connection.execute(
                        "INSERT INTO properties (owner_name,phone_01,address,created_by) "
                        "VALUES ('Test','0123',' Garden ',%s)", (user_id,),
                    )
                with self.assertRaises(psycopg.errors.CheckViolation), connection.transaction():
                    connection.execute("UPDATE users SET credit_balance = -1 WHERE id = %s", (user_id,))
                indexes = connection.execute(
                    "SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indrelid='pg_temp.properties'::regclass"
                ).fetchall()
                self.assertEqual(sum("gin_trgm_ops" in row[0] for row in indexes), 6)
                self.assertEqual(sum("text_pattern_ops" in row[0] for row in indexes), 2)
            finally:
                # Includes any extension/function DDL, so validation never commits
                # application schema changes or test data to the live database.
                connection.rollback()
