"""Typed, evidence-linked interview reports with an explicit approval gate."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.sessions.notes import IDENTIFIER_PATTERN


AI_DRAFT_LABEL = "Rascunho gerado por IA"
CLIENT_FORBIDDEN_PATTERN = re.compile(
    r"(?:\b(?:nota|score|rating|rubrica|pontua(?:ção|cao))\b|"
    r"\bnível\s*(?:de\s*)?(?:[1-5]|um|dois|três|tres|quatro|cinco)\b|"
    r"\b[1-5]\s*(?:/|de)\s*5\b|"
    r"(?:^|[.!?]\s*)(?:não\s+)?recomendad[oa]"
    r"(?:\s+com\s+ressalvas)?\s*[.!?]?$|"
    r"\b(?:o\s+perfil|a\s+candidata|o\s+candidato|perfil|candidat[oa])\s+"
    r"(?:está\s+|é\s+|foi\s+)?(?:não\s+)?"
    r"(?:recomendad[oa]|aprovad[oa]|reprovad[oa])\b|"
    r"\b(?:aprovad[oa]|reprovad[oa])\s+para\s+(?:a\s+)?vaga\b|"
    r"\b(?:recomendo|recomendamos)\s+(?:a\s+)?contrata(?:ção|cao)\s+"
    r"(?:do\s+candidato|da\s+candidata|do\s+perfil)\b|"
    r"\b(?:a|sua)\s+contrata(?:ção|cao)\s+"
    r"(?:(?:do\s+candidato|da\s+candidata|do\s+perfil)\s+)?"
    r"(?:não\s+)?é\s+recomendad[oa]\b|"
    r"\b(?:aprovar|reprovar|contratar)\s+(?:o\s+candidato|a\s+candidata)\b)",
    re.IGNORECASE,
)
CLIENT_MARKDOWN_PATTERN = re.compile(r"(^|\n)\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)")
REPORT_GENERATION_STALE_AFTER = timedelta(seconds=75)


class InterviewReportError(ValueError):
    """Raised when a report would violate its evidence or approval contract."""


class InterviewReportConflict(InterviewReportError):
    """Raised for stale edits or forbidden state transitions."""


class ReportStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"


class EvidenceSource(str, Enum):
    TRANSCRIPT = "transcript"
    RECRUITER_NOTE = "recruiter_note"
    CONTEXT = "context"


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: EvidenceSource
    evidence_id: str = Field(pattern=IDENTIFIER_PATTERN)


class InternalReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=IDENTIFIER_PATTERN)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=12_000)
    rating: int | None = Field(default=None, ge=1, le=5)
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=100)

    @field_validator("title", "body")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("report section text must not be blank")
        return stripped


class ClientNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trajectory: str = Field(min_length=1, max_length=12_000)
    assessment: str = Field(min_length=1, max_length=12_000)
    trajectory_evidence: list[EvidenceReference] = Field(
        min_length=1,
        max_length=100,
    )
    assessment_evidence: list[EvidenceReference] = Field(
        min_length=1,
        max_length=100,
    )

    @field_validator("trajectory", "assessment")
    @classmethod
    def validate_client_paragraph(cls, value: str) -> str:
        stripped = value.strip()
        if CLIENT_MARKDOWN_PATTERN.search(stripped):
            raise ValueError("client narrative must be continuous prose")
        if CLIENT_FORBIDDEN_PATTERN.search(stripped):
            raise ValueError("client narrative contains a forbidden rating or verdict")
        return " ".join(stripped.split())


class GeneratedInterviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal_sections: list[InternalReportSection] = Field(
        min_length=1,
        max_length=20,
    )
    client_narrative: ClientNarrative

    @model_validator(mode="after")
    def require_unique_section_ids(self) -> "GeneratedInterviewReport":
        section_ids = [section.id for section in self.internal_sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("internal section ids must be unique")
        return self


class InterviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    version: int = Field(ge=1)
    status: ReportStatus
    ai_draft_label: Literal["Rascunho gerado por IA"] = AI_DRAFT_LABEL
    internal_sections: list[InternalReportSection] = Field(min_length=1)
    client_narrative: ClientNarrative
    created_at: datetime
    updated_at: datetime
    approved_version: int | None = Field(default=None, ge=1)
    approved_at: datetime | None = None

    @model_validator(mode="after")
    def validate_approval_state(self) -> "InterviewReport":
        for timestamp in (self.created_at, self.updated_at, self.approved_at):
            if timestamp is not None and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                raise ValueError("report timestamps must include a timezone")
        if self.status == ReportStatus.DRAFT:
            if self.approved_version is not None or self.approved_at is not None:
                raise ValueError("draft report cannot contain approval metadata")
        elif (
            self.approved_version != self.version
            or self.approved_at is None
        ):
            raise ValueError("approved report must pin its current version")
        return self


class ReportSectionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=IDENTIFIER_PATTERN)
    body: str = Field(min_length=1, max_length=12_000)

    @field_validator("body")
    @classmethod
    def require_visible_body(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("report section body must not be blank")
        return stripped


class ClientNarrativeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trajectory: str = Field(min_length=1, max_length=12_000)
    assessment: str = Field(min_length=1, max_length=12_000)


class UpdateInterviewReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    sections: list[ReportSectionEdit] = Field(min_length=1, max_length=20)
    client_narrative: ClientNarrativeEdit


class ApproveInterviewReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class ApprovedClientReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    version: int = Field(ge=1)
    trajectory: str
    assessment: str
    approved_at: datetime


def report_generation_is_stale(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """Treat an unclocked or expired generation lease as interrupted."""
    updated_at = state.get("updatedAt")
    if not isinstance(updated_at, datetime):
        return True
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return updated_at <= current - REPORT_GENERATION_STALE_AFTER


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
    return text


def _validate_evidence(
    generated: GeneratedInterviewReport,
    *,
    transcript_ids: set[str],
    note_ids: set[str],
    context_ids: set[str],
) -> None:
    evidence_groups = [
        section.evidence for section in generated.internal_sections
    ] + [
        generated.client_narrative.trajectory_evidence,
        generated.client_narrative.assessment_evidence,
    ]
    allowed = {
        EvidenceSource.TRANSCRIPT: transcript_ids,
        EvidenceSource.RECRUITER_NOTE: note_ids,
        EvidenceSource.CONTEXT: context_ids,
    }
    for evidence in evidence_groups:
        identities = [(item.source, item.evidence_id) for item in evidence]
        if len(identities) != len(set(identities)):
            raise InterviewReportError("report contains duplicate evidence references")
        for item in evidence:
            if item.evidence_id not in allowed[item.source]:
                raise InterviewReportError(
                    "report references missing or cross-session evidence"
                )


def parse_generated_report(
    session_id: str,
    raw: str,
    *,
    transcript_ids: set[str],
    note_ids: set[str],
    context_ids: set[str],
    now: datetime | None = None,
) -> InterviewReport:
    """Strictly parse provider JSON and bind every claim to durable sources."""
    try:
        payload = json.loads(_strip_json_fence(raw))
        generated = GeneratedInterviewReport.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise InterviewReportError("generated report is invalid") from exc

    _validate_evidence(
        generated,
        transcript_ids=transcript_ids,
        note_ids=note_ids,
        context_ids=context_ids,
    )
    timestamp = now or datetime.now(timezone.utc)
    return InterviewReport(
        session_id=session_id,
        version=1,
        status=ReportStatus.DRAFT,
        internal_sections=generated.internal_sections,
        client_narrative=generated.client_narrative,
        created_at=timestamp,
        updated_at=timestamp,
    )


def update_report_content(
    report: InterviewReport,
    request: UpdateInterviewReportRequest,
    *,
    now: datetime | None = None,
) -> InterviewReport:
    """Apply editable prose only; ratings and evidence remain provider/source-bound."""
    if report.status != ReportStatus.DRAFT:
        raise InterviewReportConflict("approved report is immutable")
    if report.version != request.expected_version:
        raise InterviewReportConflict("report version is stale")

    edits = {edit.id: edit.body.strip() for edit in request.sections}
    if len(edits) != len(request.sections):
        raise InterviewReportConflict("report section edits contain duplicate ids")
    existing_ids = {section.id for section in report.internal_sections}
    if set(edits) != existing_ids:
        raise InterviewReportConflict("report section edit set does not match draft")

    updated_sections = [
        section.model_copy(update={"body": edits[section.id]})
        for section in report.internal_sections
    ]
    client = ClientNarrative(
        trajectory=request.client_narrative.trajectory,
        assessment=request.client_narrative.assessment,
        trajectory_evidence=report.client_narrative.trajectory_evidence,
        assessment_evidence=report.client_narrative.assessment_evidence,
    )
    return report.model_copy(
        update={
            "version": report.version + 1,
            "internal_sections": updated_sections,
            "client_narrative": client,
            "updated_at": now or datetime.now(timezone.utc),
        }
    )


def approve_report(
    report: InterviewReport,
    expected_version: int,
    *,
    now: datetime | None = None,
) -> InterviewReport:
    """Pin one exact version; an exact retry is idempotent."""
    if report.status == ReportStatus.APPROVED:
        if report.approved_version == expected_version:
            return report
        raise InterviewReportConflict("approved report version does not match")
    if report.version != expected_version:
        raise InterviewReportConflict("report version is stale")
    timestamp = now or datetime.now(timezone.utc)
    return report.model_copy(
        update={
            "status": ReportStatus.APPROVED,
            "approved_version": report.version,
            "approved_at": timestamp,
            "updated_at": timestamp,
        }
    )


def approved_client_report(report: InterviewReport) -> ApprovedClientReport:
    """Project only approved client prose; internal aids can never enter export."""
    if report.status != ReportStatus.APPROVED or report.approved_at is None:
        raise InterviewReportConflict("report is not approved")
    return ApprovedClientReport(
        session_id=report.session_id,
        version=report.version,
        trajectory=report.client_narrative.trajectory,
        assessment=report.client_narrative.assessment,
        approved_at=report.approved_at,
    )


def render_internal_summary(report: InterviewReport) -> str:
    """Compatibility rendering for the internal UI and existing WebSocket."""
    blocks = [f"## {AI_DRAFT_LABEL}"]
    for section in report.internal_sections:
        heading = f"### {section.title}"
        if section.rating is not None:
            heading += f" — {section.rating}/5"
        blocks.extend([heading, section.body])
    return "\n\n".join(blocks)


def report_to_record(report: InterviewReport) -> dict[str, Any]:
    """Translate the API model into the Firestore report document."""
    return {
        "version": report.version,
        "status": report.status.value,
        "aiDraftLabel": report.ai_draft_label,
        "internalSections": [
            {
                "id": section.id,
                "title": section.title,
                "body": section.body,
                "rating": section.rating,
                "evidence": [
                    {
                        "source": item.source.value,
                        "evidenceId": item.evidence_id,
                    }
                    for item in section.evidence
                ],
            }
            for section in report.internal_sections
        ],
        "clientNarrative": {
            "trajectory": report.client_narrative.trajectory,
            "assessment": report.client_narrative.assessment,
            "trajectoryEvidence": [
                {"source": item.source.value, "evidenceId": item.evidence_id}
                for item in report.client_narrative.trajectory_evidence
            ],
            "assessmentEvidence": [
                {"source": item.source.value, "evidenceId": item.evidence_id}
                for item in report.client_narrative.assessment_evidence
            ],
        },
        "createdAt": report.created_at,
        "updatedAt": report.updated_at,
        "approvedVersion": report.approved_version,
        "approvedAt": report.approved_at,
    }


def report_from_record(
    session_id: str,
    record: dict[str, Any],
) -> InterviewReport:
    """Reconstruct a persisted report without weakening validation."""
    try:
        sections = [
            InternalReportSection(
                id=section["id"],
                title=section["title"],
                body=section["body"],
                rating=section.get("rating"),
                evidence=[
                    EvidenceReference(
                        source=item["source"],
                        evidence_id=item["evidenceId"],
                    )
                    for item in section["evidence"]
                ],
            )
            for section in record["internalSections"]
        ]
        narrative = record["clientNarrative"]
        report = InterviewReport(
            session_id=session_id,
            version=record["version"],
            status=record["status"],
            ai_draft_label=record["aiDraftLabel"],
            internal_sections=sections,
            client_narrative=ClientNarrative(
                trajectory=narrative["trajectory"],
                assessment=narrative["assessment"],
                trajectory_evidence=[
                    EvidenceReference(
                        source=item["source"],
                        evidence_id=item["evidenceId"],
                    )
                    for item in narrative["trajectoryEvidence"]
                ],
                assessment_evidence=[
                    EvidenceReference(
                        source=item["source"],
                        evidence_id=item["evidenceId"],
                    )
                    for item in narrative["assessmentEvidence"]
                ],
            ),
            created_at=record["createdAt"],
            updated_at=record["updatedAt"],
            approved_version=record.get("approvedVersion"),
            approved_at=record.get("approvedAt"),
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise InterviewReportError("persisted report is invalid") from exc
    return report
