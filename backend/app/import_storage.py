from pathlib import Path
from uuid import UUID
import os
import tempfile

from app.core.config import get_settings


class ImportStorage:
    """Local files in development; a private GCS bucket when configured."""

    def __init__(self, bucket: str | None):
        self.bucket = None
        if bucket:
            from google.cloud import storage
            self.bucket = storage.Client().bucket(bucket)
        self.root = Path(get_settings().import_storage_dir).resolve()

    def key(self, job_id, chunk_index: int) -> str:
        # Storage paths come from validated IDs, never the uploaded filename.
        job_id = UUID(str(job_id))
        if chunk_index < 0:
            raise ValueError("Invalid chunk index")
        return f"property-imports/{job_id}/{chunk_index:06d}"

    def write(self, job_id, chunk_index: int, contents: bytes):
        key = self.key(job_id, chunk_index)
        if self.bucket is not None:
            self.bucket.blob(key).upload_from_string(contents, content_type="application/octet-stream", timeout=60)
            return
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A crash must not leave a partially written chunk at the final path.
        handle = tempfile.NamedTemporaryFile(dir=destination.parent, delete=False)
        try:
            with handle:
                handle.write(contents)
            os.replace(handle.name, destination)
        finally:
            Path(handle.name).unlink(missing_ok=True)

    def read(self, job_id, chunk_index: int) -> bytes:
        key = self.key(job_id, chunk_index)
        if self.bucket is not None:
            return self.bucket.blob(key).download_as_bytes(timeout=60)
        return (self.root / key).read_bytes()

    def delete(self, job_id, chunk_index: int):
        key = self.key(job_id, chunk_index)
        if self.bucket is not None:
            from google.api_core.exceptions import NotFound
            try:
                self.bucket.blob(key).delete(timeout=60)
            except NotFound:
                pass
        else:
            (self.root / key).unlink(missing_ok=True)

    def delete_job(self, job_id):
        """Remove only this validated job's raw files, including orphan chunks."""
        prefix = self.key(job_id, 0).rsplit("/", 1)[0] + "/"
        if self.bucket is not None:
            from google.api_core.exceptions import NotFound
            for blob in self.bucket.list_blobs(prefix=prefix):
                if not blob.name.startswith(prefix):
                    raise ValueError("Unexpected upload storage key")
                try:
                    blob.delete(timeout=60)
                except NotFound:
                    pass
            return
        directory = self.root / prefix
        base = (self.root / "property-imports").resolve()
        resolved = directory.resolve()
        if not base.is_relative_to(self.root) or not resolved.is_relative_to(base) or len(resolved.relative_to(base).parts) != 1 or directory.is_symlink():
            raise ValueError("Upload directory is outside the job storage root")
        if not directory.exists():
            return
        # Uploads are flat directories. Never recursively delete unexpected folders.
        for item in directory.iterdir():
            if item.is_file() or item.is_symlink():
                item.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
