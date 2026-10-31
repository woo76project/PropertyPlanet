"""Expire abandoned uploads after 24 inactive hours; retry raw-file deletion safely."""
import logging

from app.import_jobs import import_connection
from app.import_storage import ImportStorage

logger = logging.getLogger(__name__)


def cleanup_import_uploads(stop_requested=None):
    if stop_requested and stop_requested():
        return
    with import_connection() as connection:
        # Same row locks as uploads/retries. Commit expiry BEFORE deleting files,
        # so a retry cannot start against a partially deleted upload.
        connection.execute(
            "UPDATE property_import_jobs SET status = 'expired', updated_at = NOW() "
            "WHERE id IN (SELECT id FROM property_import_jobs "
            "WHERE status IN ('uploading', 'failed') AND files_deleted_at IS NULL "
            "AND updated_at <= NOW() - INTERVAL '24 hours' "
            "ORDER BY updated_at, id FOR UPDATE SKIP LOCKED LIMIT 25)"
        )
    with import_connection() as connection:
        jobs = connection.execute(
            "SELECT id, storage_bucket FROM property_import_jobs "
            "WHERE status IN ('expired', 'completed', 'completed_with_errors') AND files_deleted_at IS NULL "
            "ORDER BY CASE WHEN status = 'expired' THEN 0 ELSE 1 END, updated_at, id LIMIT 25"
        ).fetchall()
    for job in jobs:
        if stop_requested and stop_requested():
            break
        try:
            ImportStorage(job["storage_bucket"]).delete_job(job["id"])
            with import_connection() as connection:
                connection.execute(
                    "UPDATE property_import_jobs SET files_deleted_at = NOW() "
                    "WHERE id = %s AND status IN ('expired', 'completed', 'completed_with_errors')",
                    (job["id"],),
                )
        except Exception as error:
            # Leave the marker unset: the next sweep retries deletion. Keep paths,
            # credentials and raw file contents out of logs.
            logger.warning("Import file cleanup will retry (%s)", type(error).__name__)


def maintain_import_storage(stop_requested=None):
    try:
        cleanup_import_uploads(stop_requested=stop_requested)
    except Exception as error:
        # Maintenance/provider failure must not stop otherwise valid imports.
        logger.warning("Import cleanup temporarily unavailable (%s)", type(error).__name__)
