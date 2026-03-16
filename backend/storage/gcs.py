"""Google Cloud Storage file operations."""

from __future__ import annotations

from pathlib import Path

import structlog
from google.cloud import storage

from backend.config import Settings

logger = structlog.get_logger()


class GCSStorage:
    """Upload files (resumes, JDs, audio) to Google Cloud Storage."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: storage.Client | None = None
        self._bucket_name = f"{settings.google_cloud_project}-tars"

    def _get_client(self) -> storage.Client:
        if self._client is None:
            self._client = storage.Client(project=self.settings.google_cloud_project)
        return self._client

    def _get_bucket(self) -> storage.Bucket:
        client = self._get_client()
        bucket = client.bucket(self._bucket_name)
        if not bucket.exists():
            bucket = client.create_bucket(self._bucket_name, location="us-central1")
            logger.info("gcs_bucket_created", bucket=self._bucket_name)
        return bucket

    def upload_file(
        self,
        local_path: str | Path,
        destination_path: str,
        content_type: str | None = None,
    ) -> str:
        """Upload a file to GCS and return the gs:// path."""
        bucket = self._get_bucket()
        blob = bucket.blob(destination_path)

        blob.upload_from_filename(
            str(local_path),
            content_type=content_type,
        )

        gcs_path = f"gs://{self._bucket_name}/{destination_path}"
        logger.info(
            "gcs_file_uploaded",
            local_path=str(local_path),
            gcs_path=gcs_path,
        )
        return gcs_path

    def upload_bytes(
        self,
        data: bytes,
        destination_path: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload bytes to GCS and return the gs:// path."""
        bucket = self._get_bucket()
        blob = bucket.blob(destination_path)
        blob.upload_from_string(data, content_type=content_type)

        gcs_path = f"gs://{self._bucket_name}/{destination_path}"
        logger.info("gcs_bytes_uploaded", gcs_path=gcs_path, size=len(data))
        return gcs_path
