import csv
import logging
from contextlib import contextmanager, ExitStack, closing
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic
from uuid import uuid4
from zipfile import ZipFile

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.audit import record_audit
from app.import_jobs import CHUNK_BYTES, MAX_IMPORT_BYTES, MAX_IMPORT_ROWS, import_connection
from app.import_storage import ImportStorage
from app.import_excel import open_import_workbook
from app.properties import PropertyCreate
from app.property_files import FIELDS, REQUIRED_FIELDS, TEXT_IDENTIFIERS, validate_headers

BATCH_ROWS = 5_000
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
logger = logging.getLogger("uvicorn.error.imports")


@contextmanager
def import_stage(job, stage):
    started = monotonic()
    logger.info("Import job=%s stage=%s started", job.get("id"), stage)
    try:
        yield
    except Exception as error:
        # Log types only: exception text can contain private file data or credentials.
        logger.warning("Import job=%s stage=%s failed type=%s seconds=%.2f",
                       job.get("id"), stage, type(error).__name__, monotonic() - started)
        raise
    else:
        logger.info("Import job=%s stage=%s finished seconds=%.2f",
                    job.get("id"), stage, monotonic() - started)


class ImportFileError(Exception):
    """A safe error message about the uploaded file, suitable for the client."""


class LostLease(Exception):
    pass


class ImportStopped(Exception):
    pass


def check_stopping(stop_requested):
    if stop_requested is not None and stop_requested():
        raise ImportStopped()


def check_admin(connection, user_id):
    user = connection.execute("SELECT role, status FROM users WHERE id = %s", (user_id,)).fetchone()
    if not user or user["role"] != "Admin" or user["status"] != "Active":
        raise ImportFileError("The uploading admin is no longer active or no longer has admin access")


def clean_row(headers, values, cell_types=None):
    errors = []
    if len(values) != len(headers):
        return None, [{"field": "row", "message": "Row must have the same number of columns as the headings"}]
    data = {}
    for index, (field, raw) in enumerate(zip(headers, values)):
        if cell_types and cell_types[index] == "f":
            errors.append({"field": field, "message": "Replace formulas with plain values"})
        elif field in TEXT_IDENTIFIERS and raw is not None and not isinstance(raw, str):
            errors.append({"field": field, "message": "Enter this value as Text to preserve leading zeros"})
        value = str(raw).strip() if raw is not None else ""
        # PostgreSQL text cannot contain NUL; report it as a row error, not a failed batch.
        if "\x00" in value:
            errors.append({"field": field, "message": "Remove the invalid null character"})
        data[field] = value if value or field in REQUIRED_FIELDS else None
    try:
        record = PropertyCreate.model_validate(data)
    except ValidationError as error:
        for item in error.errors(include_input=False):
            field = str(item["loc"][0])
            message = "This field is required" if field in REQUIRED_FIELDS and data.get(field) == "" else item["msg"]
            errors.append({"field": field, "message": message})
        record = None
    return (None, errors) if errors else (record, [])


def iter_import_rows(path: Path, validate=True, job=None, on_total=None):
    """Yield logical row number, validated record (or None), and errors. Skip blank rows."""
    resources = ExitStack()
    try:
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                first_line = stream.readline()
                stream.seek(0)
                try:
                    delimiter = csv.Sniffer().sniff(first_line, delimiters=",;\t").delimiter
                except csv.Error:
                    delimiter = ","
                # The single consumer bounds CSV fields by the file-size limit.
                previous_limit = csv.field_size_limit(MAX_IMPORT_BYTES)
                try:
                    reader = csv.reader(stream, delimiter=delimiter, strict=True)
                    headers = validate_headers(next(reader, []))
                    for row_number, values in enumerate(reader, start=2):
                        if not values or all(not value.strip() for value in values):
                            continue
                        record, errors = clean_row(headers, values) if validate else (None, [])
                        yield row_number, record, errors
                finally:
                    csv.field_size_limit(previous_limit)
        else:
            with ZipFile(path) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_UNPACKED_BYTES:
                    raise ImportFileError("Excel workbook exceeds the 1 GB unpacked limit")
            with import_stage(job or {}, "opening_excel"):
                workbook = resources.enter_context(open_import_workbook(path))
            if len(workbook.sheetnames) != 1:
                raise ImportFileError("Use one worksheet with the property data")
            sheet = workbook.worksheets[0]
            if (sheet.max_column or 0) > len(FIELDS):
                raise ImportFileError("Use only the six columns in the property template")
            header_cells = next(sheet.iter_rows(min_row=1, max_row=1), ())
            headers = validate_headers([cell.value for cell in header_cells])
            if on_total is not None and sheet.max_row and sheet.max_row > 1:
                # Worksheet dimensions are available without counting rows. They may
                # include blanks, so this is only a progress estimate until EOF.
                on_total(min(MAX_IMPORT_ROWS, sheet.max_row - 1))
            for row_number, cells in enumerate(sheet.iter_rows(min_row=2), start=2):
                values = [cell.value for cell in cells]
                if all(value is None or str(value).strip() == "" for value in values):
                    continue
                record, errors = clean_row(headers, values, [cell.data_type for cell in cells]) if validate else (None, [])
                yield row_number, record, errors
    except ImportFileError:
        raise
    except HTTPException as error:
        raise ImportFileError(str(error.detail)) from None
    except UnicodeDecodeError:
        raise ImportFileError("Save the CSV as CSV UTF-8 and upload again") from None
    except (csv.Error, OSError, ValueError, IndexError):
        raise ImportFileError("Cannot read the file; check its contents, encoding, and CSV quoting") from None
    finally:
        resources.close()


def claim_job():
    token = uuid4()
    with import_connection() as connection:
        job = connection.execute(
            "SELECT * FROM property_import_jobs WHERE status = 'queued' "
            "OR (status = 'processing' AND lease_until < NOW()) "
            "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
        ).fetchone()
        if not job:
            return None
        connection.execute(
            "UPDATE property_import_jobs SET status = 'processing', worker_token = %s, "
            "lease_until = NOW() + INTERVAL '5 minutes', error_message = NULL, updated_at = NOW() WHERE id = %s",
            (token, job["id"]),
        )
        job.update(status="processing", worker_token=token)
    return job


def lock_job(connection, job):
    current = connection.execute(
        "SELECT * FROM property_import_jobs WHERE id = %s AND status = 'processing' "
        "AND worker_token = %s AND lease_until > NOW() FOR UPDATE",
        (job["id"], job["worker_token"]),
    ).fetchone()
    if not current:
        raise LostLease()
    return current


def heartbeat(job):
    with import_connection() as connection:
        cursor = connection.execute(
            "UPDATE property_import_jobs SET lease_until = NOW() + INTERVAL '5 minutes', updated_at = NOW() "
            "WHERE id = %s AND worker_token = %s AND status = 'processing' AND lease_until > NOW()",
            (job["id"], job["worker_token"]),
        )
        if cursor.rowcount != 1:
            raise LostLease()


@contextmanager
def keep_lease_alive(job, interval=30):
    """Renew independently of parsers that block before yielding their first row.

    Each write still checks the token and lease under a database lock. This helper
    cannot resurrect an expired lease or permit writes after ownership is lost.
    """
    stopped = Event()

    def renew():
        while not stopped.wait(interval):
            try:
                heartbeat(job)
            except LostLease:
                return
            except Exception as error:
                logger.warning("Import job=%s lease renewal failed type=%s",
                               job["id"], type(error).__name__)

    thread = Thread(target=renew, name="import-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)


def assemble_file(job, destination: Path, stop_requested=None):
    with import_connection() as connection:
        chunks = connection.execute(
            "SELECT chunk_index, byte_count, sha256 FROM property_import_chunks WHERE job_id = %s ORDER BY chunk_index",
            (job["id"],),
        ).fetchall()
    storage = ImportStorage(job["storage_bucket"])
    total = 0
    with destination.open("wb") as output:
        for expected_index, chunk in enumerate(chunks):
            check_stopping(stop_requested)
            heartbeat(job)
            try:
                content = storage.read(job["id"], chunk["chunk_index"])
            except FileNotFoundError:
                raise ImportFileError(
                    "Temporary upload files are no longer available, possibly after a server restart. "
                    "Create a new upload with the original file. Previously saved batches are retained."
                ) from None
            if (chunk["chunk_index"] != expected_index or len(content) != chunk["byte_count"]
                    or len(content) > CHUNK_BYTES or sha256(content).hexdigest() != chunk["sha256"]):
                raise ImportFileError("Uploaded file is incomplete or damaged; create a new upload")
            output.write(content)
            total += len(content)
    if total != job["file_size"]:
        raise ImportFileError("Uploaded file is incomplete; create a new upload")
    return chunks


def report_total_estimate(job, total):
    with import_connection() as connection:
        current = lock_job(connection, job)
        connection.execute("UPDATE property_import_jobs SET total_records = %s WHERE id = %s",
                           (max(total, current["processed_rows"]), job["id"]))


def stream_import(job, path, stop_requested=None):
    """Validate and commit bounded batches in one pass; never pre-scan all rows.

    A later parse error or row-limit failure retains previously committed batches.
    Retries re-read the source but skip rows already checkpointed by process_batch.
    """
    count = 0
    batch = []
    last_heartbeat = monotonic()
    with closing(iter_import_rows(path, job=job, on_total=lambda total: report_total_estimate(job, total))) as rows:
        for row_number, record, errors in rows:
            check_stopping(stop_requested)
            count += 1
            if count > MAX_IMPORT_ROWS:
                raise ImportFileError("File exceeds 500,000 nonblank property records. Previously saved batches are retained; split the file before uploading again.")
            if monotonic() - last_heartbeat > 10:
                heartbeat(job)
                last_heartbeat = monotonic()
            if row_number <= job["last_row"]:
                continue
            batch.append((row_number, record, errors))
            if len(batch) >= BATCH_ROWS:
                process_batch(job, batch)
                batch = []
    if count == 0:
        raise ImportFileError("File contains no property records")
    if batch:
        check_stopping(stop_requested)
        process_batch(job, batch)
    check_stopping(stop_requested)
    with import_connection() as connection:
        lock_job(connection, job)
        connection.execute("UPDATE property_import_jobs SET total_records = %s WHERE id = %s", (count, job["id"]))


def process_batch(job, batch):
    started = monotonic()
    with import_connection() as connection:
        current = lock_job(connection, job)
        check_admin(connection, job["created_by"])
        # Re-read progress under the lock: retries never double-count committed rows.
        batch = [item for item in batch if item[0] > current["last_row"]]
        if not batch:
            return
        valid = [record for _, record, errors in batch if not errors]
        invalid = [(job["id"], row, Jsonb(errors)) for row, _, errors in batch if errors]
        inserted = 0
        with connection.cursor() as cursor:
            if valid:
                # One set-based INSERT per batch instead of thousands of
                # individual statements. Arrays remain bound parameters.
                cursor.execute(
                    "INSERT INTO properties (owner_name, phone_01, phone_02, ic_number, address, remark, created_by) "
                    "SELECT incoming.*, %s FROM unnest("
                    "%s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[]) "
                    "AS incoming(owner_name, phone_01, phone_02, ic_number, address, remark) WHERE true "
                    "ON CONFLICT ON CONSTRAINT properties_duplicate_key_unique DO NOTHING",
                    (job["created_by"], *[[getattr(record, field) for record in valid] for field in FIELDS]),
                )
                inserted = cursor.rowcount
            if invalid:
                cursor.execute(
                    "INSERT INTO property_import_errors (job_id, row_number, errors) "
                    "SELECT %s, incoming.* FROM unnest(%s::bigint[], %s::jsonb[]) AS incoming(row_number, errors)",
                    (job["id"], [row for _, row, _ in invalid], [errors for _, _, errors in invalid]),
                )
        duplicates = len(valid) - inserted
        record_audit(connection, job["created_by"], "properties.imported", "property", details={
            "import_job_id": str(job["id"]), "count": inserted,
            "duplicates_skipped": duplicates, "invalid_rows": len(invalid),
        })
        connection.execute(
            "UPDATE property_import_jobs SET last_row = %s, processed_rows = processed_rows + %s, "
            "imported_count = imported_count + %s, duplicate_count = duplicate_count + %s, "
            "invalid_count = invalid_count + %s, lease_until = NOW() + INTERVAL '5 minutes', updated_at = NOW() "
            "WHERE id = %s",
            (batch[-1][0], len(batch), inserted, duplicates, len(invalid), job["id"]),
        )
    logger.info("Import job=%s stage=batch_committed rows=%s inserted=%s duplicates=%s invalid=%s seconds=%.2f",
                job["id"], len(batch), inserted, duplicates, len(invalid), monotonic() - started)


def finish_job(job):
    with import_connection() as connection:
        current = lock_job(connection, job)
        if current["processed_rows"] != current["total_records"]:
            raise ImportFileError("Not all records were processed; retry this job or contact an admin")
        connection.execute(
            "UPDATE property_import_jobs SET status = %s, worker_token = NULL, lease_until = NULL, updated_at = NOW() WHERE id = %s",
            ("completed_with_errors" if current["invalid_count"] else "completed", job["id"]),
        )


def process_job(job, stop_requested=None):
    try:
        check_stopping(stop_requested)
        with import_connection() as connection:
            check_admin(connection, job["created_by"])
        with TemporaryDirectory(prefix="property-import-") as temporary, keep_lease_alive(job):
            suffix = ".csv" if job["filename"].lower().endswith(".csv") else ".xlsx"
            path = Path(temporary) / ("upload" + suffix)
            with import_stage(job, "assembling_upload"):
                assemble_file(job, path, stop_requested=stop_requested)
            with import_stage(job, "streaming_rows"):
                stream_import(job, path, stop_requested=stop_requested)
            check_stopping(stop_requested)
            finish_job(job)
            # The report/counters live in Neon. Completed jobs no longer need the raw upload.
            # Cleanup failure must not turn a committed completed job into a failed job.
            try:
                storage = ImportStorage(job["storage_bucket"])
                storage.delete_job(job["id"])
                with import_connection() as connection:
                    connection.execute("UPDATE property_import_jobs SET files_deleted_at = NOW() WHERE id = %s", (job["id"],))
            except Exception:
                pass
        return True
    except ImportStopped:
        # Each batch and its checkpoint commit together. Requeue with the same
        # progress; a later consumer skips all rows already committed.
        with import_connection() as connection:
            connection.execute(
                "UPDATE property_import_jobs SET status = 'queued', worker_token = NULL, "
                "lease_until = NULL, updated_at = NOW() "
                "WHERE id = %s AND worker_token = %s AND status = 'processing' AND lease_until > NOW()",
                (job["id"], job["worker_token"]),
            )
        return False
    except LostLease:
        # Another worker owns this job; this worker must not change its state.
        logger.warning("Import job=%s stopped after losing its lease", job["id"])
        return False
    except Exception as error:
        logger.warning("Import job=%s processing failed type=%s", job["id"], type(error).__name__)
        message = str(error) if isinstance(error, ImportFileError) else "Import could not finish. Previously saved batches are retained; retry or contact an admin."
        with import_connection() as connection:
            connection.execute(
                "UPDATE property_import_jobs SET status = 'failed', error_message = %s, worker_token = NULL, "
                "lease_until = NULL, updated_at = NOW() WHERE id = %s AND worker_token = %s AND status = 'processing'",
                (message, job["id"], job["worker_token"]),
            )
        return False


def run_next_job(stop_requested=None):
    if stop_requested is not None and stop_requested():
        return None
    job = claim_job()
    if job is None:
        return None
    return process_job(job, stop_requested=stop_requested)
