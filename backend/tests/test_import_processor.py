from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth import require_complete_profile
from app.import_processor import ImportProcessor
from app.import_worker import ImportFileError, assemble_file, process_job
from app.main import app


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.maintenance = patch("app.import_processor.maintain_import_storage")
        self.cleanup = self.maintenance.start()
        self.addCleanup(self.maintenance.stop)

    def test_startup_drains_jobs_sequentially_and_wakes_without_poll_delay(self):
        idle, processed = Event(), Event()
        pending = []
        results = []

        def run(stop_requested):
            self.assertFalse(stop_requested())
            if pending:
                results.append(pending.pop(0))
                if len(results) == 2:
                    processed.set()
                return results[-1]
            idle.set()
            return None

        processor = ImportProcessor(poll_seconds=60)
        with patch("app.import_processor.run_next_job", side_effect=run):
            processor.start()
            try:
                self.assertTrue(idle.wait(2), "Startup must check the queue")
                pending.extend([True, False])
                processor.notify()
                self.assertTrue(processed.wait(2), "Wake-up must process without waiting for the poll")
            finally:
                processor.stop()
        self.assertEqual(results, [True, False])
        self.assertFalse(processor.thread.is_alive())

    def test_periodic_scan_recovers_a_missed_notification(self):
        scanned_twice = Event()
        calls = []

        def run(stop_requested):
            calls.append(1)
            if len(calls) == 2:
                scanned_twice.set()
            return None

        processor = ImportProcessor(poll_seconds=0.01)
        with patch("app.import_processor.run_next_job", side_effect=run):
            processor.start()
            try:
                self.assertTrue(scanned_twice.wait(2))
            finally:
                processor.stop()

    def test_provider_failure_does_not_kill_processor_or_log_private_details(self):
        recovered = Event()
        calls = []

        def run(stop_requested):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("private-connection-details")
            recovered.set()
            return None

        processor = ImportProcessor(poll_seconds=0.01)
        with patch("app.import_processor.run_next_job", side_effect=run), self.assertLogs(
            "app.import_processor", level="WARNING"
        ) as logs:
            processor.start()
            try:
                self.assertTrue(recovered.wait(2))
            finally:
                processor.stop()
        self.assertNotIn("private-connection-details", " ".join(logs.output))

    def test_lifespan_starts_and_stops_only_when_enabled(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), patch(
                "app.main.settings", SimpleNamespace(import_processor_enabled=enabled)
            ), patch("app.import_processor.ImportProcessor") as factory:
                with TestClient(app) as client:
                    self.assertEqual(client.get("/health").status_code, 200)
                    if enabled:
                        factory.return_value.start.assert_called_once()
                        factory.return_value.stop.assert_not_called()
                if enabled:
                    factory.return_value.stop.assert_called_once()
                else:
                    factory.assert_not_called()

    def test_start_and_retry_notify_after_database_commit_only(self):
        job_id = uuid4()
        app.dependency_overrides[require_complete_profile] = lambda: {
            "id": 1, "role": "Admin", "status": "Active"
        }
        processor = MagicMock()
        app.state.import_processor = processor
        try:
            for route, status in (("start", "uploading"), ("retry", "failed")):
                with self.subTest(route=route):
                    committed = []
                    processor.reset_mock()
                    processor.notify.side_effect = lambda: self.assertEqual(committed, [True])

                    @contextmanager
                    def connection():
                        yield MagicMock()
                        committed.append(True)

                    with patch("app.import_jobs.import_connection", connection), patch(
                        "app.import_jobs.get_job", return_value={
                            "status": status, "uploaded_bytes": 10, "file_size": 10
                        }
                    ):
                        response = TestClient(app).post(f"/properties/import-jobs/{job_id}/{route}")
                    self.assertEqual(response.status_code, 202)
                    processor.notify.assert_called_once()

            processor.reset_mock()
            with patch("app.import_jobs.import_connection", connection), patch(
                "app.import_jobs.get_job", return_value={
                    "status": "uploading", "uploaded_bytes": 5, "file_size": 10
                }
            ):
                response = TestClient(app).post(f"/properties/import-jobs/{job_id}/start")
            self.assertEqual(response.status_code, 409)
            processor.notify.assert_not_called()
        finally:
            app.dependency_overrides.clear()
            app.state.import_processor = None

    def test_shutdown_requeues_owned_job_without_resetting_committed_progress(self):
        connection = MagicMock()
        job = {"id": uuid4(), "worker_token": uuid4()}

        @contextmanager
        def connect():
            yield connection

        with patch("app.import_worker.import_connection", connect), patch(
            "app.import_worker.assemble_file"
        ) as assemble:
            self.assertFalse(process_job(job, stop_requested=lambda: True))
        assemble.assert_not_called()
        sql, params = connection.execute.call_args.args
        self.assertIn("status = 'queued'", sql)
        self.assertIn("worker_token = %s", sql)
        self.assertIn("lease_until > NOW()", sql)
        self.assertNotIn("last_row =", sql)
        self.assertEqual(params, (job["id"], job["worker_token"]))

    def test_lost_local_upload_gives_actionable_error(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [{
            "chunk_index": 0, "byte_count": 1, "sha256": "unused"
        }]

        @contextmanager
        def connect():
            yield connection

        with TemporaryDirectory() as directory, patch(
            "app.import_worker.import_connection", connect
        ), patch("app.import_worker.heartbeat"), patch("app.import_worker.ImportStorage") as storage:
            storage.return_value.read.side_effect = FileNotFoundError("private-file-path")
            with self.assertRaisesRegex(ImportFileError, "Create a new upload") as error:
                assemble_file({"id": uuid4(), "storage_bucket": None}, Path(directory) / "upload.csv")
            self.assertNotIn("private-file-path", str(error.exception))


if __name__ == "__main__":
    unittest.main()
