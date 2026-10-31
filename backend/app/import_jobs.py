from contextlib import contextmanager
from hashlib import sha256
from uuid import UUID, uuid4
import csv
from io import StringIO
import logging
from time import monotonic

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.auth import require_admin
from app.core.config import get_settings
from app.import_storage import ImportStorage
from app.property_files import FIELDS, LABELS

router = APIRouter(prefix="/properties/import-jobs", tags=["Large property imports"])
logger = logging.getLogger("uvicorn.error.imports")
MAX_IMPORT_ROWS = 500_000
MAX_IMPORT_BYTES = 100 * 1024 * 1024
CHUNK_BYTES = 5 * 1024 * 1024
FINAL_STATES = {"completed", "completed_with_errors", "failed", "expired"}
PUBLIC_COLUMNS = (
    "id, created_by, filename, file_size, status, uploaded_bytes, total_records, "
    "last_row, processed_rows, imported_count, duplicate_count, invalid_count, "
    "error_message, created_at, updated_at"
)


@contextmanager
def import_connection():
    try:
        with psycopg.connect(get_settings().db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15) as connection:
            yield connection
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Import service is temporarily unavailable") from None


class ImportJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    filename: str = Field(min_length=1, max_length=255)
    file_size: int = Field(gt=0, le=MAX_IMPORT_BYTES, strict=True)

    @field_validator("filename")
    @classmethod
    def supported_filename(cls, value):
        if not value.lower().endswith((".csv", ".xlsx")):
            raise ValueError("Upload an Excel .xlsx or CSV .csv file")
        if any(ord(character) < 32 for character in value):
            raise ValueError("Filename contains invalid characters")
        return value


def get_job(connection, job_id: UUID, lock: bool = False):
    job = connection.execute(
        "SELECT * FROM property_import_jobs WHERE id = %s" + (" FOR UPDATE" if lock else ""),
        (job_id,),
    ).fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    return job


def upload_chunk(job_id: UUID, chunk_index: int, contents: bytes):
    digest = sha256(contents).hexdigest()
    with import_connection() as connection:
        job = get_job(connection, job_id, lock=True)
        if job["status"] == "expired":
            raise HTTPException(status_code=410, detail="Upload expired after 24 hours of inactivity. Create a new upload; saved records and reports are retained.")
        previous = connection.execute(
            "SELECT byte_count, sha256 FROM property_import_chunks WHERE job_id = %s AND chunk_index = %s",
            (job_id, chunk_index),
        ).fetchone()
        if previous:
            if previous["byte_count"] != len(contents) or previous["sha256"] != digest:
                raise HTTPException(status_code=409, detail="This chunk was already uploaded with different contents")
            if job["status"] == "uploading":
                connection.execute("UPDATE property_import_jobs SET updated_at = NOW() WHERE id = %s", (job_id,))
            return {"uploaded_bytes": job["uploaded_bytes"], "status": job["status"]}
        if job["status"] != "uploading":
            raise HTTPException(status_code=409, detail="This job is no longer accepting uploads")
        expected_index = job["uploaded_bytes"] // CHUNK_BYTES
        expected_size = min(CHUNK_BYTES, job["file_size"] - job["uploaded_bytes"])
        if chunk_index != expected_index or expected_size <= 0:
            raise HTTPException(status_code=409, detail="Upload chunks in order; resume from uploaded_bytes")
        if len(contents) != expected_size:
            raise HTTPException(status_code=422, detail=f"This chunk must contain {expected_size} bytes")
        try:
            ImportStorage(job["storage_bucket"]).write(job_id, chunk_index, contents)
        except Exception:
            raise HTTPException(status_code=503, detail="Could not store this chunk; retry the same chunk") from None
        connection.execute(
            "INSERT INTO property_import_chunks (job_id, chunk_index, byte_count, sha256) VALUES (%s, %s, %s, %s)",
            (job_id, chunk_index, len(contents), digest),
        )
        connection.execute(
            "UPDATE property_import_jobs SET uploaded_bytes = uploaded_bytes + %s, updated_at = NOW() WHERE id = %s",
            (len(contents), job_id),
        )
    return {"uploaded_bytes": job["uploaded_bytes"] + len(contents), "status": "uploading"}


@router.post("", status_code=201)
def create_import_job(data: ImportJobCreate, admin: dict = Depends(require_admin)):
    job_id = uuid4()
    with import_connection() as connection:
        job = connection.execute(
            "INSERT INTO property_import_jobs (id, created_by, filename, file_size, storage_bucket) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING " + PUBLIC_COLUMNS,
            (job_id, admin["id"], data.filename, data.file_size, get_settings().import_storage_bucket),
        ).fetchone()
    return {"job": job, "chunk_bytes": CHUNK_BYTES, "max_records": MAX_IMPORT_ROWS}


@router.get("")
def list_import_jobs(
    before_id: UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    admin: dict = Depends(require_admin),
):
    with import_connection() as connection:
        values = []
        where = ""
        if before_id is not None:
            cursor = get_job(connection, before_id)
            where = " WHERE (created_at, id) < (%s, %s)"
            values.extend((cursor["created_at"], before_id))
        rows = connection.execute(
            "SELECT " + PUBLIC_COLUMNS + " FROM property_import_jobs" + where
            + " ORDER BY created_at DESC, id DESC LIMIT %s",
            (*values, limit + 1),
        ).fetchall()
    results = rows[:limit]
    has_more = len(rows) > limit
    return {"jobs": results, "has_more": has_more,
            "next_before_id": results[-1]["id"] if has_more else None}


@router.put("/{job_id}/chunks/{chunk_index}")
async def put_import_chunk(
    request: Request, job_id: UUID, chunk_index: int = Path(ge=0),
    admin: dict = Depends(require_admin),
):
    received_at = monotonic()
    contents = bytearray()
    async for block in request.stream():
        if len(contents) + len(block) > CHUNK_BYTES:
            raise HTTPException(status_code=413, detail="Upload chunks of at most 5 MB")
        contents.extend(block)
    received_seconds = monotonic() - received_at
    storing_at = monotonic()
    result = await run_in_threadpool(upload_chunk, job_id, chunk_index, bytes(contents))
    logger.info("Import job=%s stage=upload_chunk chunk=%s bytes=%s receive_seconds=%.2f store_seconds=%.2f",
                job_id, chunk_index, len(contents), received_seconds, monotonic() - storing_at)
    return result


@router.post("/{job_id}/start", status_code=202)
def start_import_job(request: Request, job_id: UUID, admin: dict = Depends(require_admin)):
    with import_connection() as connection:
        job = get_job(connection, job_id, lock=True)
        if job["status"] == "expired":
            raise HTTPException(status_code=410, detail="Upload expired. Create a new upload; saved records and reports are retained.")
        if job["status"] == "uploading":
            if job["uploaded_bytes"] != job["file_size"]:
                raise HTTPException(status_code=409, detail="Finish uploading all chunks before starting")
            connection.execute(
                "UPDATE property_import_jobs SET status = 'queued', updated_at = NOW() WHERE id = %s", (job_id,),
            )
            job["status"] = "queued"
        status = job["status"]
    # Wake only after the queue transaction commits, so the consumer can see it.
    processor = getattr(request.app.state, "import_processor", None)
    if processor is not None and status == "queued":
        processor.notify()
    return {"job_id": job_id, "status": status}


@router.get("/{job_id}")
def import_job_status(job_id: UUID, admin: dict = Depends(require_admin)):
    with import_connection() as connection:
        job = get_job(connection, job_id)
    return {key.strip(): job[key.strip()] for key in PUBLIC_COLUMNS.split(",")}


@router.post("/{job_id}/retry", status_code=202)
def retry_import_job(request: Request, job_id: UUID, admin: dict = Depends(require_admin)):
    with import_connection() as connection:
        job = get_job(connection, job_id, lock=True)
        if job["status"] == "expired":
            raise HTTPException(status_code=410, detail="Upload expired. Create a new upload; saved records and reports are retained.")
        if job["status"] != "failed":
            raise HTTPException(status_code=409, detail="Only failed jobs can be retried")
        connection.execute(
            "UPDATE property_import_jobs SET status = 'queued', worker_token = NULL, lease_until = NULL, "
            "error_message = NULL, updated_at = NOW() WHERE id = %s", (job_id,),
        )
    processor = getattr(request.app.state, "import_processor", None)
    if processor is not None:
        processor.notify()
    return {"job_id": job_id, "status": "queued"}


def require_finished(connection, job_id):
    job = get_job(connection, job_id)
    if job["status"] not in FINAL_STATES:
        raise HTTPException(status_code=409, detail="The invalid-row report is available after processing finishes")
    return job


@router.get("/{job_id}/errors")
def import_errors(
    job_id: UUID, after_row: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=1000), admin: dict = Depends(require_admin),
):
    with import_connection() as connection:
        require_finished(connection, job_id)
        rows = connection.execute(
            "SELECT row_number AS row, errors FROM property_import_errors "
            "WHERE job_id = %s AND row_number > %s ORDER BY row_number LIMIT %s",
            (job_id, after_row, limit + 1),
        ).fetchall()
    has_more = len(rows) > limit
    results = rows[:limit]
    return {"invalid_rows": results, "has_more": has_more,
            "next_after_row": results[-1]["row"] if has_more else None}


@router.get("/{job_id}/errors/download")
def download_import_errors(job_id: UUID, admin: dict = Depends(require_admin)):
    with import_connection() as connection:
        require_finished(connection, job_id)

    def report():
        output = StringIO(newline="")
        writer = csv.writer(output)
        labels = dict(zip(FIELDS, LABELS))
        writer.writerow(["Row", "Field", "Error"])
        yield "\ufeff" + output.getvalue()
        after_row = 1
        while True:
            with import_connection() as connection:
                rows = connection.execute(
                    "SELECT row_number AS row, errors FROM property_import_errors "
                    "WHERE job_id = %s AND row_number > %s ORDER BY row_number LIMIT 500",
                    (job_id, after_row),
                ).fetchall()
            if not rows:
                break
            output.seek(0)
            output.truncate()
            for row in rows:
                for error in row["errors"]:
                    writer.writerow([row["row"], labels.get(error["field"], "Row"), error["message"]])
            yield output.getvalue()
            after_row = rows[-1]["row"]
    return StreamingResponse(report(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="invalid_property_rows.csv"',
    })
