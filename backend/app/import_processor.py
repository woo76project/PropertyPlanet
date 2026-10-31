"""One sequential import consumer for a single-process API deployment."""
import logging
from threading import Event, Thread

from app.import_worker import run_next_job
from app.import_cleanup import maintain_import_storage

logger = logging.getLogger(__name__)


class ImportProcessor:
    def __init__(self, poll_seconds=60):
        self.poll_seconds = poll_seconds
        self.stopping = Event()
        self.wakeup = Event()
        self.thread = Thread(target=self._run, name="property-imports", daemon=True)

    def start(self):
        self.thread.start()
        logger.info("Embedded import processor started")

    def notify(self):
        self.wakeup.set()

    def stop(self):
        self.stopping.set()
        self.wakeup.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            logger.warning("Import processor still stopping; its job lease will expire if interrupted")

    def _run(self):
        # Drain on startup and after notifications. Periodic checks recover missed
        # notifications and jobs whose previous worker lease has expired.
        while not self.stopping.is_set():
            self.wakeup.clear()
            try:
                while not self.stopping.is_set():
                    maintain_import_storage(stop_requested=self.stopping.is_set)
                    if run_next_job(stop_requested=self.stopping.is_set) is None:
                        break
            except Exception as error:
                # Provider errors can contain credentials or private file data.
                logger.warning("Import processor temporarily unavailable (%s)", type(error).__name__)
            self.wakeup.wait(self.poll_seconds)
