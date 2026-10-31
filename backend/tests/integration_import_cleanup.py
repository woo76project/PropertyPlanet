"""Run against private PostgreSQL temporary tables; never expire real uploads."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.core.config import get_settings
from app.import_cleanup import cleanup_import_uploads
from app.import_storage import ImportStorage


class ImportCleanupIntegrationTests(unittest.TestCase):
    def setUp(self):
        params = conninfo_to_dict(get_settings().db_url.get_secret_value())
        if "-pooler" in params.get("host", ""):
            params["host"] = params["host"].replace("-pooler", "")
        self.connection = psycopg.connect(**params, row_factory=dict_row, connect_timeout=15)
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.connection.execute("SET LOCAL statement_timeout = '30s'")
        schema = Path("sql/schema.sql").read_text().replace("CREATE TABLE", "CREATE TEMP TABLE").replace("property_duplicate_text", "pg_temp.property_duplicate_text")
        self.connection.execute(schema)
        self.connection.execute("INSERT INTO users (clerk_user_id,email,role) VALUES ('cleanup_test','test@example.invalid','Admin')")

    @contextmanager
    def connect(self):
        with self.connection.transaction():
            yield self.connection

    def job(self, status, hours):
        job_id = uuid4()
        self.connection.execute(
            "INSERT INTO property_import_jobs (id,created_by,filename,file_size,status,updated_at,error_message) VALUES (%s,1,'test.csv',1,%s,%s,'Original error')",
            (job_id, status, datetime.now(timezone.utc) - timedelta(hours=hours)),
        )
        return job_id

    def test_expiry_deletes_stale_files_but_keeps_reports_records_and_active_jobs(self):
        stale_failed, stale_upload = self.job("failed", 25), self.job("uploading", 25)
        recent_failed, recent_upload = self.job("failed", 23), self.job("uploading", 1)
        processing, queued, completed = self.job("processing", 48), self.job("queued", 48), self.job("completed", 1)
        self.connection.execute("INSERT INTO property_import_errors (job_id,row_number,errors) VALUES (%s,2,'[]')", (stale_failed,))
        self.connection.execute("INSERT INTO properties (owner_name,phone_01,address,created_by) VALUES ('Keep','TEST','KL',1)")
        with TemporaryDirectory() as folder, patch("app.import_storage.get_settings", return_value=SimpleNamespace(import_storage_dir=Path(folder))), patch("app.import_cleanup.import_connection", self.connect):
            storage = ImportStorage(None)
            for job_id in (stale_failed, stale_upload, recent_failed, recent_upload, processing, queued, completed):
                storage.write(job_id, 0, b"x")
            # An orphan chunk with no DB manifest is also cleaned.
            storage.write(stale_failed, 1, b"orphan")
            cleanup_import_uploads()
            cleanup_import_uploads()  # Idempotent sweep.
            for job_id in (stale_failed, stale_upload, completed):
                self.assertFalse((storage.root / storage.key(job_id, 0)).exists())
                self.assertIsNotNone(self.connection.execute("SELECT files_deleted_at FROM property_import_jobs WHERE id=%s", (job_id,)).fetchone()["files_deleted_at"])
            for job_id in (recent_failed, recent_upload, processing, queued):
                self.assertEqual(storage.read(job_id, 0), b"x")
            self.assertEqual(self.connection.execute("SELECT status,error_message FROM property_import_jobs WHERE id=%s", (stale_failed,)).fetchone(), {"status": "expired", "error_message": "Original error"})
            self.assertEqual(self.connection.execute("SELECT COUNT(*) AS n FROM property_import_errors").fetchone()["n"], 1)
            self.assertEqual(self.connection.execute("SELECT COUNT(*) AS n FROM properties").fetchone()["n"], 1)

    def test_fresh_schema_supports_expiry_and_preserves_job_history(self):
        job_id = self.job("failed", 25)
        with TemporaryDirectory() as folder, patch("app.import_storage.get_settings", return_value=SimpleNamespace(import_storage_dir=Path(folder))), patch("app.import_cleanup.import_connection", self.connect):
            cleanup_import_uploads()
        row = self.connection.execute("SELECT status,files_deleted_at,error_message FROM property_import_jobs WHERE id=%s", (job_id,)).fetchone()
        self.assertEqual(row["status"], "expired")
        self.assertEqual(row["error_message"], "Original error")
        self.assertIsNotNone(row["files_deleted_at"])
