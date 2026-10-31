from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth import require_complete_profile
from app.import_cleanup import cleanup_import_uploads, maintain_import_storage
from app.import_storage import ImportStorage
from app.main import app


class ImportCleanupTests(unittest.TestCase):
    def test_local_cleanup_removes_only_the_selected_job_including_orphan_files(self):
        with TemporaryDirectory() as folder, patch("app.import_storage.get_settings", return_value=SimpleNamespace(import_storage_dir=Path(folder))):
            storage = ImportStorage(None)
            selected, other = uuid4(), uuid4()
            storage.write(selected, 0, b"selected")
            storage.write(other, 0, b"keep")
            job_dir = (storage.root / storage.key(selected, 0)).parent
            (job_dir / "orphan-write.tmp").write_bytes(b"partial")
            storage.delete_job(selected)
            storage.delete_job(selected)  # Repeat after a crash is safe.
            self.assertFalse(job_dir.exists())
            self.assertEqual(storage.read(other, 0), b"keep")
            with self.assertRaises(ValueError):
                storage.delete_job("../outside")
            self.assertEqual(storage.read(other, 0), b"keep")

    def test_local_cleanup_refuses_unexpected_nested_folders(self):
        with TemporaryDirectory() as folder, patch("app.import_storage.get_settings", return_value=SimpleNamespace(import_storage_dir=Path(folder))):
            storage = ImportStorage(None)
            job_id = uuid4()
            job_dir = (storage.root / storage.key(job_id, 0)).parent
            nested = job_dir / "unexpected"
            nested.mkdir(parents=True)
            (nested / "keep").write_text("keep")
            with self.assertRaises(OSError):
                storage.delete_job(job_id)
            self.assertTrue((nested / "keep").exists())

    def test_bucket_cleanup_uses_validated_job_prefix_only(self):
        storage = ImportStorage(None)
        storage.bucket = MagicMock()
        job_id = uuid4()
        prefix = f"property-imports/{job_id}/"
        blob = MagicMock(); blob.name = prefix + "000000"
        storage.bucket.list_blobs.return_value = [blob]
        storage.delete_job(job_id)
        storage.bucket.list_blobs.assert_called_once_with(prefix=prefix)
        blob.delete.assert_called_once_with(timeout=60)
        outsider = MagicMock(); outsider.name = "other-job/000000"
        storage.bucket.list_blobs.return_value = [outsider]
        with self.assertRaises(ValueError):
            storage.delete_job(job_id)
        outsider.delete.assert_not_called()

    def test_expiry_commits_before_deletion_and_failed_deletion_is_retried(self):
        connection = MagicMock()
        jobs = [{"id": uuid4(), "storage_bucket": None}, {"id": uuid4(), "storage_bucket": None}]
        connection.execute.return_value.fetchall.return_value = jobs
        committed = []

        @contextmanager
        def connect():
            yield connection
            committed.append(True)

        def delete(job_id):
            self.assertGreaterEqual(len(committed), 2)
            if job_id == jobs[0]["id"]:
                raise OSError("private disk path")

        with patch("app.import_cleanup.import_connection", connect), patch("app.import_cleanup.ImportStorage") as factory, self.assertLogs("app.import_cleanup", level="WARNING") as logs:
            factory.return_value.delete_job.side_effect = delete
            cleanup_import_uploads()
        self.assertNotIn("private disk path", " ".join(logs.output))
        marks = [c for c in connection.execute.call_args_list if "SET files_deleted_at" in c.args[0]]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0].args[1], (jobs[1]["id"],))
        expiry = connection.execute.call_args_list[0].args[0]
        self.assertIn("INTERVAL '24 hours'", expiry)
        self.assertIn("FOR UPDATE SKIP LOCKED", expiry)

    def test_shutdown_skips_cleanup_and_maintenance_errors_do_not_escape(self):
        with patch("app.import_cleanup.import_connection") as connect:
            cleanup_import_uploads(stop_requested=lambda: True)
            connect.assert_not_called()
        with patch("app.import_cleanup.cleanup_import_uploads", side_effect=RuntimeError("private")), self.assertLogs("app.import_cleanup", level="WARNING") as logs:
            maintain_import_storage()
        self.assertNotIn("private", " ".join(logs.output))

    def test_expired_jobs_cannot_resume_start_or_retry(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1, "role": "Admin"}
        self.addCleanup(app.dependency_overrides.clear)
        connection = MagicMock()

        @contextmanager
        def connect():
            yield connection

        job_id = uuid4()
        with patch("app.import_jobs.import_connection", connect), patch("app.import_jobs.get_job", return_value={"status": "expired"}), patch("app.import_jobs.ImportStorage") as storage:
            client = TestClient(app)
            for route in ("start", "retry"):
                self.assertEqual(client.post(f"/properties/import-jobs/{job_id}/{route}").status_code, 410)
            self.assertEqual(client.put(f"/properties/import-jobs/{job_id}/chunks/0", content=b"x").status_code, 410)
            storage.assert_not_called()
            connection.execute.assert_not_called()
