"""Synchronous request/response seam for durable Google Meet artifact imports."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.sessions.workspace_imports import (
    GoogleMeetImportRequest,
    NormalizedGoogleMeetImport,
    normalize_google_meet_import,
)


IMPORT_LEASE_DURATION = timedelta(minutes=5)


class TranscriptImportError(ValueError):
    """Base class for safe, content-free import failures."""


class TranscriptImportNotFound(TranscriptImportError):
    """The job is absent or belongs to a different principal."""


class TranscriptImportConflict(TranscriptImportError):
    """The source identity is already bound to different content or active work."""


class TranscriptImportDeleted(TranscriptImportConflict):
    """The deterministic session has been fenced by deletion."""


class ImportJobStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"


class ImportJobState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str
    source_digest: str
    session_id: str
    owner_id: str
    org_id: str
    status: ImportJobStatus
    version: int = Field(ge=1)
    attempt_count: int = Field(ge=0)
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    reason_code: str | None = None
    segment_count: int = Field(default=0, ge=0)
    updated_at: datetime


class ImportClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: ImportJobState
    idempotent_replay: bool = False


class GoogleMeetImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    source_key: str
    source_digest: str
    status: ImportJobStatus
    segment_count: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    idempotent_replay: bool


class GoogleMeetImportStorage(Protocol):
    async def queue_transcript_import(
        self,
        normalized: NormalizedGoogleMeetImport,
        *,
        updated_at: datetime,
    ) -> ImportJobState: ...

    async def claim_transcript_import(
        self,
        *,
        source_key: str,
        source_digest: str,
        session_id: str,
        owner_id: str,
        org_id: str,
        lease_token: str,
        lease_expires_at: datetime,
        updated_at: datetime,
    ) -> ImportClaim: ...

    async def commit_transcript_import(
        self,
        normalized: NormalizedGoogleMeetImport,
        *,
        lease_token: str,
        version: int,
        updated_at: datetime,
    ) -> ImportJobState: ...

    async def fail_transcript_import(
        self,
        *,
        source_key: str,
        owner_id: str,
        org_id: str,
        lease_token: str,
        version: int,
        reason_code: str,
        updated_at: datetime,
    ) -> ImportJobState: ...

    async def get_transcript_import_job(
        self, source_key: str, *, owner_id: str, org_id: str
    ) -> ImportJobState: ...


def _result(job: ImportJobState, *, replay: bool) -> GoogleMeetImportResult:
    return GoogleMeetImportResult(
        session_id=job.session_id,
        source_key=job.source_key,
        source_digest=job.source_digest,
        status=job.status,
        segment_count=job.segment_count,
        attempt_count=job.attempt_count,
        idempotent_replay=replay,
    )


class GoogleMeetImportWorker:
    """Run one import inline while durability and leases live in storage."""

    def __init__(
        self,
        storage: GoogleMeetImportStorage,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def run(
        self,
        request: GoogleMeetImportRequest,
        *,
        owner_id: str,
        org_id: str,
        now: datetime | None = None,
    ) -> GoogleMeetImportResult:
        imported_at = now or self._clock()
        normalized = normalize_google_meet_import(
            request,
            owner_id=owner_id,
            org_id=org_id,
            imported_at=imported_at,
        )
        await self._storage.queue_transcript_import(
            normalized,
            updated_at=imported_at,
        )
        lease_token = secrets.token_urlsafe(32)
        claim = await self._storage.claim_transcript_import(
            source_key=normalized.source_key,
            source_digest=normalized.source_digest,
            session_id=normalized.session.id,
            owner_id=owner_id,
            org_id=org_id,
            lease_token=lease_token,
            lease_expires_at=imported_at + IMPORT_LEASE_DURATION,
            updated_at=imported_at,
        )
        if claim.job.status == ImportJobStatus.COMPLETED:
            return _result(claim.job, replay=True)

        commit_at = now or self._clock()
        try:
            completed = await self._storage.commit_transcript_import(
                normalized,
                lease_token=lease_token,
                version=claim.job.version,
                updated_at=commit_at,
            )
        except Exception:
            try:
                failure_at = now or self._clock()
                await self._storage.fail_transcript_import(
                    source_key=normalized.source_key,
                    owner_id=owner_id,
                    org_id=org_id,
                    lease_token=lease_token,
                    version=claim.job.version,
                    reason_code="atomic_commit_failed",
                    updated_at=failure_at,
                )
            except Exception:
                # The original commit error is authoritative. A later claim can
                # recover an expired lease even if this best-effort marker fails.
                pass
            raise
        return _result(completed, replay=False)

    async def status(
        self, source_key: str, *, owner_id: str, org_id: str
    ) -> GoogleMeetImportResult:
        job = await self._storage.get_transcript_import_job(
            source_key, owner_id=owner_id, org_id=org_id
        )
        return _result(job, replay=job.status == ImportJobStatus.COMPLETED)
