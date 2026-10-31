from contextlib import contextmanager
import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import require_complete_profile
from app.import_jobs import ImportJobCreate, upload_chunk, put_import_chunk
from app.import_storage import ImportStorage
from app.import_worker import (
    ImportFileError, LostLease, clean_row, iter_import_rows, process_batch, process_job, stream_import,
)
from app.main import app
from app.property_files import FIELDS
from test_csv_import import csv_file
from test_property_files import VALID_ROW, spreadsheet


@contextmanager
def mock_connection(connection):
    yield connection


class ImportJobTests(unittest.TestCase):
    def test_chunk_timings_separate_receiving_from_storage_without_logging_contents(self):
        class Upload:
            async def stream(self):
                yield b"private-"
                yield b"contents"

        job_id = uuid4()
        result = {"uploaded_bytes": 15, "status": "uploading"}
        with patch("app.import_jobs.upload_chunk", return_value=result) as save, patch(
            "app.import_jobs.monotonic", side_effect=[10, 12, 12, 12.5]
        ), self.assertLogs("uvicorn.error.imports", level="INFO") as logs:
            self.assertEqual(asyncio.run(put_import_chunk(Upload(), job_id, 0, {"role": "Admin"})), result)
        save.assert_called_once_with(job_id, 0, b"private-contents")
        self.assertIn("receive_seconds=2.00 store_seconds=0.50", logs.output[0])
        self.assertNotIn("private-contents", logs.output[0])

    def tearDown(self):
        app.dependency_overrides.clear()

    def authorize_admin(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1, "role": "Admin"}

    def test_all_job_routes_require_admin(self):
        job_id = str(uuid4())
        client = TestClient(app)
        for expected in (401, 403):
            for method, path, kwargs in (
                ("get", "/properties/import-jobs", {}),
                ("post", "/properties/import-jobs", {"json": {"filename": "a.csv", "file_size": 1}}),
                ("put", f"/properties/import-jobs/{job_id}/chunks/0", {"content": b"a"}),
                ("post", f"/properties/import-jobs/{job_id}/start", {}),
                ("post", f"/properties/import-jobs/{job_id}/retry", {}),
                ("get", f"/properties/import-jobs/{job_id}", {}),
                ("get", f"/properties/import-jobs/{job_id}/errors", {}),
                ("get", f"/properties/import-jobs/{job_id}/errors/download", {}),
            ):
                self.assertEqual(client.request(method, path, **kwargs).status_code, expected)
            app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}

    def test_metadata_limits_and_invalid_extension(self):
        self.authorize_admin()
        with patch("app.import_jobs.import_connection") as connect:
            for data in ({"filename": "a.xls", "file_size": 1},
                         {"filename": "a.csv", "file_size": 0},
                         {"filename": "a.csv", "file_size": 104857601}):
                self.assertEqual(TestClient(app).post("/properties/import-jobs", json=data).status_code, 422)
            connect.assert_not_called()
        self.assertEqual(ImportJobCreate(filename="a.xlsx", file_size=104857600).file_size, 104857600)

    def test_history_uses_public_columns_and_one_extra_row(self):
        self.authorize_admin()
        connection = MagicMock()
        first, second = uuid4(), uuid4()
        connection.execute.return_value.fetchall.return_value = [{"id": first}, {"id": second}]
        with patch("app.import_jobs.import_connection", side_effect=lambda: mock_connection(connection)):
            response = TestClient(app).get("/properties/import-jobs?limit=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"jobs": [{"id": str(first)}], "has_more": True, "next_before_id": str(first)})
        query, params = connection.execute.call_args.args
        self.assertIn("ORDER BY created_at DESC, id DESC", query)
        self.assertNotIn("storage_bucket", query)
        self.assertNotIn("worker_token", query)
        self.assertEqual(params, (2,))

    def test_history_cursor_uses_timestamp_and_id_for_equal_timestamps(self):
        self.authorize_admin()
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        before_id = uuid4()
        with patch("app.import_jobs.import_connection", side_effect=lambda: mock_connection(connection)), patch("app.import_jobs.get_job", return_value={"created_at": "2026-10-17T00:00:00Z"}):
            response = TestClient(app).get(f"/properties/import-jobs?before_id={before_id}&limit=20")
        self.assertEqual(response.json(), {"jobs": [], "has_more": False, "next_before_id": None})
        query, params = connection.execute.call_args.args
        self.assertIn("(created_at, id) < (%s, %s)", query)
        self.assertEqual(params, ("2026-10-17T00:00:00Z", before_id, 21))

    def test_history_rejects_invalid_cursor_and_page_size_before_database(self):
        self.authorize_admin()
        with patch("app.import_jobs.import_connection") as connection:
            for query in ("before_id=bad", "limit=0", "limit=101"):
                self.assertEqual(TestClient(app).get("/properties/import-jobs?" + query).status_code, 422)
            connection.assert_not_called()

    def test_chunk_upload_replay_and_conflicting_replay(self):
        connection = MagicMock()
        job = dict(id=uuid4(), uploaded_bytes=0, file_size=3, status="uploading", storage_bucket=None)
        connection.execute.return_value.fetchone.return_value = None
        with patch("app.import_jobs.import_connection", side_effect=lambda: mock_connection(connection)), patch("app.import_jobs.get_job", return_value=job), patch("app.import_jobs.ImportStorage") as storage:
            self.assertEqual(upload_chunk(job["id"], 0, b"abc")["uploaded_bytes"], 3)
            storage.return_value.write.assert_called_once()
            connection.execute.return_value.fetchone.return_value = {"byte_count": 3, "sha256": sha256(b"abc").hexdigest()}
            job.update(uploaded_bytes=3, status="queued")
            self.assertEqual(upload_chunk(job["id"], 0, b"abc")["status"], "queued")
            storage.return_value.write.assert_called_once()
            with self.assertRaises(HTTPException) as caught:
                upload_chunk(job["id"], 0, b"bad")
            self.assertEqual(caught.exception.status_code, 409)

    def test_chunk_order_size_and_storage_failure(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = None
        job = dict(id=uuid4(), uploaded_bytes=0, file_size=3, status="uploading", storage_bucket=None)
        with patch("app.import_jobs.import_connection", side_effect=lambda: mock_connection(connection)), patch("app.import_jobs.get_job", return_value=job), patch("app.import_jobs.ImportStorage") as storage:
            for index, data, code in ((1, b"abc", 409), (0, b"ab", 422)):
                with self.assertRaises(HTTPException) as caught:
                    upload_chunk(job["id"], index, data)
                self.assertEqual(caught.exception.status_code, code)
            storage.return_value.write.assert_not_called()
            storage.return_value.write.side_effect = OSError("private storage path")
            with self.assertRaises(HTTPException) as caught:
                upload_chunk(job["id"], 0, b"abc")
            self.assertEqual(caught.exception.status_code, 503)
            self.assertNotIn("private", caught.exception.detail)

    def test_oversized_chunk_is_rejected_before_database(self):
        self.authorize_admin()
        with patch("app.import_jobs.CHUNK_BYTES", 2), patch("app.import_jobs.import_connection") as connect:
            response = TestClient(app).put(f"/properties/import-jobs/{uuid4()}/chunks/0", content=b"abc")
            self.assertEqual(response.status_code, 413)
            connect.assert_not_called()

    def test_local_chunk_storage_round_trip_and_safe_path(self):
        with TemporaryDirectory() as directory, patch("app.import_storage.get_settings", return_value=SimpleNamespace(import_storage_dir=directory)):
            storage = ImportStorage(None)
            job_id = uuid4()
            storage.write(job_id, 0, b"abc")
            self.assertEqual(storage.read(job_id, 0), b"abc")
            storage.write(job_id, 0, b"replacement")
            self.assertEqual(storage.read(job_id, 0), b"replacement")
            with self.assertRaises(ValueError):
                storage.key("../../outside", 0)

    def test_csv_and_excel_continue_past_invalid_rows(self):
        bad = list(VALID_ROW)
        bad[0] = ""
        for suffix, contents in ((".csv", csv_file([VALID_ROW, bad, VALID_ROW])),
                                 (".xlsx", spreadsheet([VALID_ROW, bad, VALID_ROW]))):
            with TemporaryDirectory() as directory:
                path = Path(directory) / ("data" + suffix)
                path.write_bytes(contents)
                rows = list(iter_import_rows(path))
                self.assertEqual([row[0] for row in rows], [2, 3, 4])
                self.assertIsNotNone(rows[0][1])
                self.assertEqual(rows[1][2][0]["field"], "owner_name")
                self.assertIsNotNone(rows[2][1])
                self.assertEqual(len(list(iter_import_rows(path, validate=False))), 3)

    def test_stream_stops_at_row_limit_without_replaying_committed_batches(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "data.csv"
            path.write_bytes(csv_file([VALID_ROW] * 3))
            with patch("app.import_worker.MAX_IMPORT_ROWS", 2), patch("app.import_worker.BATCH_ROWS", 2), patch("app.import_worker.process_batch") as batch:
                with self.assertRaises(ImportFileError):
                    stream_import({"last_row": 1}, path)
                batch.assert_called_once()
                self.assertEqual([item[0] for item in batch.call_args.args[1]], [2, 3])

    def test_invalid_types_and_null_characters_become_row_errors(self):
        values = list(VALID_ROW)
        values[1] = 123
        values[4] = "bad\x00address"
        record, errors = clean_row(FIELDS, values)
        self.assertIsNone(record)
        self.assertIn("phone_01", [error["field"] for error in errors])
        self.assertIn("address", [error["field"] for error in errors])

    def test_batch_counts_invalid_and_duplicate_rows_and_commits_progress(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.rowcount = 1
        record, _ = clean_row(FIELDS, VALID_ROW)
        errors = [{"field": "owner_name", "message": "Required"}]
        job = {"id": uuid4(), "created_by": 1, "worker_token": uuid4()}
        with patch("app.import_worker.import_connection", side_effect=lambda: mock_connection(connection)), patch("app.import_worker.lock_job", return_value={"last_row": 1}), patch("app.import_worker.check_admin"), patch("app.import_worker.record_audit") as audit:
            process_batch(job, [(2, record, []), (3, record, []), (4, None, errors)])
            params = connection.execute.call_args.args[1]
            self.assertEqual(params[:5], (4, 3, 1, 1, 1))
            self.assertIs(audit.call_args.args[0], connection)
            self.assertEqual(audit.call_args.kwargs["details"]["invalid_rows"], 1)

    def test_replayed_batch_does_not_insert_or_double_count(self):
        connection = MagicMock()
        job = {"id": uuid4(), "created_by": 1, "worker_token": uuid4()}
        with patch("app.import_worker.import_connection", side_effect=lambda: mock_connection(connection)), patch("app.import_worker.lock_job", return_value={"last_row": 4}), patch("app.import_worker.check_admin"), patch("app.import_worker.record_audit") as audit:
            process_batch(job, [(2, None, []), (3, None, [])])
            connection.cursor.assert_not_called()
            audit.assert_not_called()

    def test_bulk_batch_uses_bound_arrays_and_two_statements_for_mixed_rows(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        values = list(VALID_ROW)
        values[0] = "Property 'quoted'\\with\nnew line"
        record, errors = clean_row(FIELDS, values)
        self.assertFalse(errors)
        job = {"id": uuid4(), "created_by": 1, "worker_token": uuid4()}
        with patch("app.import_worker.import_connection", side_effect=lambda: mock_connection(connection)), patch(
            "app.import_worker.lock_job", return_value={"last_row": 1}
        ), patch("app.import_worker.check_admin"), patch("app.import_worker.record_audit"):
            process_batch(job, [(2, record, []), (3, record, []), (4, None, [{"field": "owner_name", "message": "Required"}])])
        cursor.executemany.assert_not_called()
        self.assertEqual(cursor.execute.call_count, 2)
        sql, params = cursor.execute.call_args_list[0].args
        self.assertIn("unnest", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertNotIn(values[0], sql)
        self.assertEqual(params[0], 1)
        self.assertEqual(params[1], [record.owner_name, record.owner_name])
        self.assertEqual(params[2], [record.phone_01, record.phone_01])
        self.assertEqual(connection.execute.call_args.args[1][:5], (4, 3, 1, 1, 1))

    def test_lost_lease_does_not_mark_another_workers_job_failed(self):
        job = {"id": uuid4(), "created_by": 1, "worker_token": uuid4(), "filename": "a.csv"}
        connection = MagicMock()
        with patch("app.import_worker.import_connection", side_effect=lambda: mock_connection(connection)), patch("app.import_worker.check_admin"), patch("app.import_worker.assemble_file", side_effect=LostLease()):
            self.assertFalse(process_job(job))
            connection.execute.assert_not_called()

    def test_report_only_available_when_finished_and_contains_invalid_rows(self):
        self.authorize_admin()
        connection = MagicMock()
        with patch("app.import_jobs.import_connection", side_effect=lambda: mock_connection(connection)), patch("app.import_jobs.get_job", return_value={"status": "processing"}):
            self.assertEqual(TestClient(app).get(f"/properties/import-jobs/{uuid4()}/errors").status_code, 409)
        with patch("app.import_jobs.import_connection", side_effect=lambda: mock_connection(connection)), patch("app.import_jobs.get_job", return_value={"status": "completed_with_errors"}):
            connection.execute.return_value.fetchall.return_value = [{"row": 400000, "errors": [{"field": "owner_name", "message": "Required"}]}]
            response = TestClient(app).get(f"/properties/import-jobs/{uuid4()}/errors")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["invalid_rows"][0]["row"], 400000)
            self.assertNotIn("valid_rows", response.json())


if __name__ == "__main__":
    unittest.main()
