from threading import Event, enumerate as enumerate_threads
import unittest
from unittest.mock import patch

from app.import_worker import LostLease, import_stage, keep_lease_alive


class ImportLeaseTests(unittest.TestCase):
    def test_renews_while_parser_is_blocked_and_stops_on_exit(self):
        renewed = Event()
        with patch("app.import_worker.heartbeat", side_effect=lambda job: renewed.set()) as heartbeat:
            with keep_lease_alive({"id": "job"}, interval=0.01):
                # No iterator progress or database batches occur during this wait.
                self.assertTrue(renewed.wait(2))
            self.assertGreater(heartbeat.call_count, 0)
            self.assertFalse(any(t.name == "import-lease" and t.is_alive()
                                 for t in enumerate_threads()))

    def test_lost_lease_is_not_retried(self):
        attempted = Event()

        def lose(job):
            attempted.set()
            raise LostLease()

        with patch("app.import_worker.heartbeat", side_effect=lose) as heartbeat:
            with keep_lease_alive({"id": "job"}, interval=0.01):
                self.assertTrue(attempted.wait(2))
            heartbeat.assert_called_once()

    def test_transient_renewal_failure_is_retried_without_logging_private_text(self):
        recovered = Event()
        calls = []

        def renew(job):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("private connection details")
            recovered.set()

        with patch("app.import_worker.heartbeat", side_effect=renew), self.assertLogs(
            "uvicorn.error.imports", level="WARNING"
        ) as logs:
            with keep_lease_alive({"id": "job"}, interval=0.01):
                self.assertTrue(recovered.wait(2))
        self.assertNotIn("private connection details", " ".join(logs.output))

    def test_stage_reports_failure_type_without_private_values(self):
        with self.assertLogs("uvicorn.error.imports", level="INFO") as logs:
            with self.assertRaises(MemoryError):
                with import_stage({"id": "job"}, "opening_excel"):
                    raise MemoryError("private file data")
        output = " ".join(logs.output)
        self.assertIn("opening_excel failed type=MemoryError", output)
        self.assertNotIn("private file data", output)
