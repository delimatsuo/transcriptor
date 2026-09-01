"""Provider-independent contracts and normalization for workspace transcript imports."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.models import Session, SessionMode, SessionStatus, TranscriptSegment


MAX_REQUEST_BYTES = 2_000_000
MAX_UNIQUE_ENTRIES = 400
MAX_ENTRY_CHARS = 60_000
MAX_TRANSCRIPT_CHARS = 1_000_000


def _require_exact_text(value: str, *, label: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty without surrounding whitespace")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} contains control characters")
    return value


def _aware(value: datetime | None, *, label: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{label} must include a timezone")
    return value


def _canonical_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MeetTranscriptEntry(BaseModel):
    """One structured entry as returned by the Meet transcript entries API."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=1024)
    participant: str = Field(min_length=1, max_length=1024)
    participant_name: str | None = Field(default=None, alias="participantName", max_length=512)
    text: str = Field(min_length=1, max_length=MAX_ENTRY_CHARS)
    start_time: datetime | None = Field(default=None, alias="startTime")
    end_time: datetime | None = Field(default=None, alias="endTime")
    language_code: str | None = Field(default=None, alias="languageCode", max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("name", "participant")
    @classmethod
    def exact_identity(cls, value: str, info) -> str:
        return _require_exact_text(value, label=info.field_name)

    @field_validator("participant_name", "language_code")
    @classmethod
    def exact_optional_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _require_exact_text(value, label=info.field_name)

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain visible content")
        if "\x00" in value:
            raise ValueError("text contains a NUL character")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def timezone_required(cls, value: datetime | None, info) -> datetime | None:
        return _aware(value, label=info.field_name)

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def timestamp_shape(cls, value: Any, info) -> Any:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError(f"{info.field_name} must be an RFC3339 timestamp string")
        return value

    @field_validator("confidence")
    @classmethod
    def finite_confidence(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("confidence must be finite")
        return value

    @model_validator(mode="after")
    def ordered_times(self) -> "MeetTranscriptEntry":
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time < self.start_time
        ):
            raise ValueError("endTime cannot be before startTime")
        return self


class MeetTranscriptSession(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=1024)
    entries: list[MeetTranscriptEntry] = Field(min_length=1, max_length=MAX_UNIQUE_ENTRIES)

    @field_validator("name")
    @classmethod
    def exact_resource_name(cls, value: str) -> str:
        return _require_exact_text(value, label="transcript session name")


class GoogleMeetImportRequest(BaseModel):
    """Strict manual import envelope; paths and provider credentials are not fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_type: Literal["GOOGLE_MEET"] = Field(alias="sourceType")
    source_artifact_id: str = Field(alias="sourceArtifactId", min_length=1, max_length=1024)
    title: str = Field(min_length=1, max_length=240)
    notice_given: bool = Field(alias="noticeGiven")
    notice_provenance: str = Field(alias="noticeProvenance", min_length=1, max_length=1000)
    transcript_sessions: list[MeetTranscriptSession] = Field(
        alias="transcriptSessions", min_length=1, max_length=8
    )
    candidate_id: str | None = Field(default=None, alias="candidateId", max_length=512)
    candidate_name: str | None = Field(default=None, alias="candidateName", max_length=300)
    resume_artifact_id: str | None = Field(default=None, alias="resumeArtifactId", max_length=512)
    resume_text: str | None = Field(default=None, alias="resumeText", max_length=500_000)
    job_description_artifact_id: str | None = Field(
        default=None, alias="jobDescriptionArtifactId", max_length=512
    )
    job_description_text: str | None = Field(
        default=None, alias="jobDescriptionText", max_length=500_000
    )
    briefing: str | None = Field(default=None, max_length=100_000)

    @field_validator(
        "source_artifact_id",
        "title",
        "notice_provenance",
        "candidate_id",
        "candidate_name",
        "resume_artifact_id",
        "job_description_artifact_id",
    )
    @classmethod
    def validate_identity_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _require_exact_text(value, label=info.field_name)

    @field_validator("resume_text", "job_description_text", "briefing")
    @classmethod
    def validate_context_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError(f"{info.field_name} must contain visible content")
        if "\x00" in value:
            raise ValueError(f"{info.field_name} contains a NUL character")
        return value

    @model_validator(mode="after")
    def enforce_import_bounds(self) -> "GoogleMeetImportRequest":
        if (self.resume_artifact_id is None) != (self.resume_text is None):
            raise ValueError("resumeArtifactId and resumeText must be supplied together")
        if (self.job_description_artifact_id is None) != (
            self.job_description_text is None
        ):
            raise ValueError(
                "jobDescriptionArtifactId and jobDescriptionText must be supplied together"
            )

        unique: dict[tuple[str, str], MeetTranscriptEntry] = {}
        total_chars = 0
        for transcript in self.transcript_sessions:
            for entry in transcript.entries:
                identity = (transcript.name, entry.name)
                existing = unique.get(identity)
                if existing is not None:
                    if existing != entry:
                        raise ValueError(
                            "duplicate transcript entry identity has different content"
                        )
                    continue
                unique[identity] = entry
                total_chars += len(entry.text)
        if len(unique) > MAX_UNIQUE_ENTRIES:
            raise ValueError(f"at most {MAX_UNIQUE_ENTRIES} unique entries are allowed")
        if total_chars > MAX_TRANSCRIPT_CHARS:
            raise ValueError(
                f"transcript text exceeds {MAX_TRANSCRIPT_CHARS} characters"
            )
        canonical_size = len(
            json.dumps(
                self.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if canonical_size > MAX_REQUEST_BYTES:
            raise ValueError(f"request JSON exceeds {MAX_REQUEST_BYTES} UTF-8 bytes")
        return self

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> "GoogleMeetImportRequest":
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError(f"request JSON exceeds {MAX_REQUEST_BYTES} UTF-8 bytes")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("request JSON must be valid UTF-8") from exc
        return cls.model_validate_json(raw)


class NormalizedGoogleMeetImport(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_key: str
    source_digest: str
    session: Session
    segments: list[TranscriptSegment]
    segment_provenance: dict[str, dict[str, Any]]
    source_provenance: dict[str, Any]
    report_sources: dict[str, dict[str, Any]]


def _entry_payload(
    transcript_name: str, entry: MeetTranscriptEntry
) -> dict[str, Any]:
    return {
        "transcriptResource": transcript_name,
        "entryResource": entry.name,
        "participant": entry.participant,
        "participantName": entry.participant_name,
        "text": entry.text,
        "startTime": _canonical_time(entry.start_time),
        "endTime": _canonical_time(entry.end_time),
        "languageCode": entry.language_code,
        "confidence": entry.confidence,
    }


def _deduplicated_entries(
    request: GoogleMeetImportRequest,
) -> list[tuple[str, MeetTranscriptEntry]]:
    entries: dict[tuple[str, str], MeetTranscriptEntry] = {}
    for transcript in request.transcript_sessions:
        for entry in transcript.entries:
            entries.setdefault((transcript.name, entry.name), entry)
    return [(key[0], entry) for key, entry in entries.items()]


def _report_sources(request: GoogleMeetImportRequest) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    if request.candidate_name is not None:
        sources["candidate_name"] = {
            "type": "candidate_name",
            "text": request.candidate_name,
            "linkageId": request.candidate_id,
        }
    if request.resume_text is not None:
        sources["resume"] = {
            "type": "resume",
            "text": request.resume_text,
            "artifactId": request.resume_artifact_id,
            "candidateId": request.candidate_id,
        }
    if request.job_description_text is not None:
        sources["jd"] = {
            "type": "jd",
            "text": request.job_description_text,
            "artifactId": request.job_description_artifact_id,
        }
    if request.briefing is not None:
        sources["briefing"] = {
            "type": "briefing",
            "text": request.briefing,
            "candidateId": request.candidate_id,
        }
    return sources


def normalize_google_meet_import(
    request: GoogleMeetImportRequest,
    *,
    owner_id: str,
    org_id: str,
    imported_at: datetime,
) -> NormalizedGoogleMeetImport:
    """Normalize a manual artifact without touching session or capture runtime state."""
    imported_at = _aware(imported_at, label="imported_at")  # type: ignore[assignment]
    assert imported_at is not None
    source_material = f"{request.source_type}\x00{request.source_artifact_id}".encode("utf-8")
    source_key = hashlib.sha256(source_material).hexdigest()
    session_id = f"meet-import-{source_key[:32]}"

    unique_entries = _deduplicated_entries(request)
    sorted_for_digest = sorted(
        (_entry_payload(transcript, entry) for transcript, entry in unique_entries),
        key=lambda item: (item["transcriptResource"], item["entryResource"]),
    )
    digest_payload = {
        "sourceType": request.source_type,
        "sourceArtifactId": request.source_artifact_id,
        "title": request.title,
        "noticeGiven": request.notice_given,
        "noticeProvenance": request.notice_provenance,
        "candidateId": request.candidate_id,
        "candidateName": request.candidate_name,
        "resumeArtifactId": request.resume_artifact_id,
        "resumeText": request.resume_text,
        "jobDescriptionArtifactId": request.job_description_artifact_id,
        "jobDescriptionText": request.job_description_text,
        "briefing": request.briefing,
        "entries": sorted_for_digest,
    }
    digest_json = json.dumps(
        digest_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    source_digest = hashlib.sha256(digest_json).hexdigest()

    present_starts = [
        entry.start_time for _, entry in unique_entries if entry.start_time is not None
    ]
    earliest_start = min(present_starts) if present_starts else None

    ordered = sorted(
        unique_entries,
        key=lambda item: (
            0 if item[1].start_time is not None else 1,
            item[1].start_time.astimezone(timezone.utc).timestamp()
            if item[1].start_time is not None
            else 0.0,
            item[0],
            item[1].name,
        ),
    )

    segments: list[TranscriptSegment] = []
    provenance: dict[str, dict[str, Any]] = {}
    for index, (transcript_name, entry) in enumerate(ordered):
        segment_material = "\x00".join(
            (
                request.source_type,
                request.source_artifact_id,
                transcript_name,
                entry.name,
            )
        ).encode("utf-8")
        segment_id = f"meet-{hashlib.sha256(segment_material).hexdigest()[:24]}"
        if entry.start_time is None or earliest_start is None:
            start_seconds = 0.0
        else:
            start_seconds = max(0.0, (entry.start_time - earliest_start).total_seconds())
        if entry.end_time is None or earliest_start is None:
            end_seconds = start_seconds
        else:
            end_seconds = max(start_seconds, (entry.end_time - earliest_start).total_seconds())
        segment = TranscriptSegment(
            id=segment_id,
            text=entry.text,
            speaker=entry.participant_name or "Unknown speaker",
            start_time=start_seconds,
            end_time=end_seconds,
            confidence=entry.confidence if entry.confidence is not None else 0.0,
            sequence_number=index,
            is_final=True,
        )
        segments.append(segment)
        provenance[segment_id] = {
            "sourceType": request.source_type,
            "sourceArtifactId": request.source_artifact_id,
            "transcriptResource": transcript_name,
            "entryResource": entry.name,
            "participant": entry.participant,
            "participantName": entry.participant_name,
            "originalStartTime": entry.start_time,
            "originalEndTime": entry.end_time,
            "languageCode": entry.language_code,
            "startTimeMissing": entry.start_time is None,
            "endTimeMissing": entry.end_time is None,
            "confidenceMissing": entry.confidence is None,
            "participantNameMissing": entry.participant_name is None,
            "importedAt": imported_at,
            "sourceDigest": source_digest,
        }

    bounds = [
        timestamp
        for _, entry in unique_entries
        for timestamp in (entry.start_time, entry.end_time)
        if timestamp is not None
    ]
    started_at = min(bounds) if bounds else imported_at
    ended_at = max(bounds) if bounds else imported_at
    session = Session(
        id=session_id,
        mode=SessionMode.INTERVIEW,
        title=request.title,
        started_at=started_at,
        ended_at=ended_at,
        last_active=ended_at,
        status=SessionStatus.COMPLETED,
        notice_given=request.notice_given,
        transcript_durability="complete",
        owner_id=owner_id,
        org_id=org_id,
    )
    source_provenance = {
        "sourceType": request.source_type,
        "sourceArtifactId": request.source_artifact_id,
        "sourceKey": source_key,
        "sourceDigest": source_digest,
        "noticeGiven": request.notice_given,
        "noticeProvenance": request.notice_provenance,
        "candidateId": request.candidate_id,
        "resumeArtifactId": request.resume_artifact_id,
        "jobDescriptionArtifactId": request.job_description_artifact_id,
        "importedAt": imported_at,
        "segmentCount": len(segments),
    }
    return NormalizedGoogleMeetImport(
        source_key=source_key,
        source_digest=source_digest,
        session=session,
        segments=segments,
        segment_provenance=provenance,
        source_provenance=source_provenance,
        report_sources=_report_sources(request),
    )
