"""Durable, lease-guarded interview report generation."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.llm.interview_prompts import (
    INTERVIEW_REPORT_PROMPT,
    INTERVIEW_REPORT_RESPONSE_SCHEMA,
)
from backend.schemas.models import SessionMode, SessionStatus
from backend.sessions.notes import deserialize_recruiter_notes
from backend.sessions.reports import InterviewReport, parse_generated_report
from backend.sessions.review import deserialize_session, deserialize_transcript


REPORT_LEASE_DURATION = timedelta(minutes=5)


class ReportGenerationError(ValueError):
    """A report job cannot safely advance."""


class ReportGenerationNotFound(ReportGenerationError):
    """The session/job is absent, deleted, or outside the requested scope."""


class ReportGenerationConflict(ReportGenerationError):
    """The report job is active, stale, failed, or internally inconsistent."""


class ReportGenerationStatus(str, Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class ReportGenerationJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    owner_id: str | None
    org_id: str | None
    status: ReportGenerationStatus
    version: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    reason_code: str | None = None
    updated_at: datetime


class ReportGenerationClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: ReportGenerationJob
    current_report: InterviewReport | None = None
    idempotent_ready: bool = False


class InterviewReportGenerator(Protocol):
    async def generate(self, **kwargs: Any) -> str: ...


class DurableInterviewReportStorage(Protocol):
    async def claim_report_generation(
        self,
        session_id: str,
        *,
        owner_id: str | None,
        org_id: str | None,
        lease_token: str,
        lease_expires_at: datetime,
        updated_at: datetime,
    ) -> ReportGenerationClaim: ...

    async def fail_report_generation(
        self,
        session_id: str,
        *,
        owner_id: str | None,
        org_id: str | None,
        lease_token: str,
        version: int,
        reason_code: str,
        updated_at: datetime,
    ) -> ReportGenerationJob: ...

    async def complete_report_generation(
        self,
        report: InterviewReport,
        *,
        lease_token: str,
        version: int,
        updated_at: datetime,
    ) -> InterviewReport: ...

    async def get_session_record(self, session_id: str) -> dict | None: ...

    async def get_session_transcript(self, session_id: str) -> list[dict]: ...

    async def get_session_notes(self, session_id: str) -> list[dict]: ...

    async def get_interview_context(self, session_id: str) -> list[dict]: ...


def build_durable_report_prompt(
    transcript,
    notes,
    context: dict[str, str],
) -> str:
    parts = ["## Fontes de contexto duráveis"]
    for context_id in sorted(context):
        parts.append(
            f"[source=context evidence_id={context_id}]\n{context[context_id]}"
        )
    parts.append("## Transcrição final durável")
    for segment in transcript:
        speaker = segment.speaker_override or segment.speaker
        parts.append(
            f"[source=transcript evidence_id={segment.id} "
            f"offset_ms={round(segment.end_time * 1000)} "
            f"speaker={speaker}]\n{segment.text}"
        )
    parts.append("## Julgamentos da recrutadora")
    if notes:
        for note in notes:
            parts.append(
                f"[source=recruiter_note evidence_id={note.id} "
                f"kind={note.kind.value} "
                f"transcript_segment_id={note.transcript_segment_id}]"
            )
    else:
        parts.append("(Nenhuma nota da recrutadora foi registrada.)")
    return "\n\n".join(parts)


class DurableInterviewReportWorker:
    """Claim, generate, validate, and atomically publish one existing report schema."""

    def __init__(
        self,
        storage: DurableInterviewReportStorage,
        generator: InterviewReportGenerator,
        *,
        max_input_chars: int = 120_000,
        max_output_tokens: int = 8192,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._generator = generator
        self._max_input_chars = max_input_chars
        self._max_output_tokens = min(8192, max_output_tokens)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def run(
        self,
        session_id: str,
        *,
        owner_id: str | None,
        org_id: str | None,
        now: datetime | None = None,
    ) -> InterviewReport:
        claim_at = now or self._clock()
        lease_token = secrets.token_urlsafe(32)
        claim = await self._storage.claim_report_generation(
            session_id,
            owner_id=owner_id,
            org_id=org_id,
            lease_token=lease_token,
            lease_expires_at=claim_at + REPORT_LEASE_DURATION,
            updated_at=claim_at,
        )
        if claim.idempotent_ready:
            if claim.current_report is None:
                raise ReportGenerationConflict("ready report is missing")
            return claim.current_report

        reason_code = "provider_or_validation_failure"
        try:
            report = claim.current_report
            if report is None:
                record = await self._storage.get_session_record(session_id)
                if (
                    record is None
                    or record.get("ownerId") != owner_id
                    or record.get("orgId") != org_id
                ):
                    raise ReportGenerationNotFound("completed interview not found")
                session = deserialize_session(session_id, record)
                if (
                    session.mode != SessionMode.INTERVIEW
                    or session.status != SessionStatus.COMPLETED
                ):
                    raise ReportGenerationNotFound("completed interview not found")

                transcript_records = await self._storage.get_session_transcript(session_id)
                if any(
                    item.get("ownerId") != owner_id or item.get("orgId") != org_id
                    for item in transcript_records
                ):
                    raise ReportGenerationNotFound("completed interview not found")
                transcript = deserialize_transcript(transcript_records)
                if not transcript:
                    raise ReportGenerationError(
                        "durable transcript is required for report generation"
                    )

                note_records = await self._storage.get_session_notes(session_id)
                if any(
                    item.get("ownerId") != owner_id or item.get("orgId") != org_id
                    for item in note_records
                ):
                    raise ReportGenerationNotFound("completed interview not found")
                notes = deserialize_recruiter_notes(session_id, note_records)

                context_records = await self._storage.get_interview_context(session_id)
                if any(
                    item.get("ownerId") != owner_id or item.get("orgId") != org_id
                    for item in context_records
                ):
                    raise ReportGenerationNotFound("completed interview not found")
                context: dict[str, str] = {}
                for item in context_records:
                    context_id = item.get("id")
                    if (
                        not isinstance(context_id, str)
                        or item.get("type") != context_id
                        or not isinstance(item.get("text"), str)
                        or not item["text"].strip()
                    ):
                        raise ReportGenerationError(
                            "durable report context is invalid"
                        )
                    context[context_id] = item["text"]

                user_message = build_durable_report_prompt(transcript, notes, context)
                if len(user_message) > self._max_input_chars:
                    reason_code = "report_input_too_large"
                    raise ReportGenerationError("report input is too large")
                raw_report = await asyncio.wait_for(
                    self._generator.generate(
                        system_instruction=INTERVIEW_REPORT_PROMPT,
                        user_message=user_message,
                        temperature=0.2,
                        max_output_tokens=self._max_output_tokens,
                        response_mime_type="application/json",
                        response_schema=INTERVIEW_REPORT_RESPONSE_SCHEMA,
                    ),
                    timeout=60,
                )
                report_at = now or self._clock()
                report = parse_generated_report(
                    session_id,
                    raw_report,
                    transcript_ids={segment.id for segment in transcript},
                    note_ids={note.id for note in notes},
                    context_ids=set(context),
                    owner_id=owner_id,
                    org_id=org_id,
                    now=report_at,
                )

            complete_at = now or self._clock()
            return await self._storage.complete_report_generation(
                report,
                lease_token=lease_token,
                version=claim.job.version,
                updated_at=complete_at,
            )
        except Exception:
            try:
                await self._storage.fail_report_generation(
                    session_id,
                    owner_id=owner_id,
                    org_id=org_id,
                    lease_token=lease_token,
                    version=claim.job.version,
                    reason_code=reason_code,
                    updated_at=now or self._clock(),
                )
            except Exception:
                pass
            raise
