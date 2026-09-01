"""Firestore read/write operations."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from google.cloud import firestore

from backend.config import Settings
from backend.schemas.models import (
    ActionItem,
    Session,
    SessionStatus,
    TranscriptSegment,
)
from backend.sessions.notes import (
    CreateRecruiterNoteRequest,
    RecruiterNote,
    RecruiterNoteConflict,
    RecruiterNoteError,
    build_recruiter_note,
    deserialize_recruiter_notes,
    same_note_payload,
)
from backend.sessions.reports import (
    ApproveInterviewReportRequest,
    InterviewReport,
    InterviewReportConflict,
    UpdateInterviewReportRequest,
    approve_report,
    report_from_record,
    report_to_record,
    render_internal_summary,
    update_report_content,
)
from backend.sessions.workspace_imports import NormalizedGoogleMeetImport
from backend.workers.google_meet_import import (
    ImportClaim,
    ImportJobState,
    ImportJobStatus,
    TranscriptImportConflict,
    TranscriptImportDeleted,
    TranscriptImportNotFound,
)
from backend.workers.interview_report import (
    ReportGenerationClaim,
    ReportGenerationConflict,
    ReportGenerationJob,
    ReportGenerationNotFound,
    ReportGenerationStatus,
)

logger = structlog.get_logger()


class FirestoreStorage:
    """Firestore persistence for sessions and transcripts."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._db: firestore.AsyncClient | None = None

    async def _get_db(self) -> firestore.AsyncClient:
        if self._db is None:
            self._db = firestore.AsyncClient(project=self.settings.google_cloud_project)
        return self._db

    @staticmethod
    def _session_record(session: Session) -> dict:
        return {
            "mode": session.mode.value,
            "title": session.title,
            "startedAt": session.started_at,
            "endedAt": session.ended_at,
            "lastActive": session.last_active,
            "status": session.status.value,
            "noticeGiven": session.notice_given,
            "speakerMap": session.speaker_map,
            "summary": session.summary,
            "actionItems": [item.model_dump() for item in session.action_items],
            "transcriptDurability": session.transcript_durability,
            "transcriptFailureCount": session.transcript_failure_count,
            "ownerId": session.owner_id,
            "orgId": session.org_id,
        }

    async def save_session(self, session: Session) -> None:
        """Save or update a session document."""
        db = await self._get_db()
        doc_ref = db.collection("sessions").document(session.id)

        await doc_ref.set(self._session_record(session), merge=True)
        logger.info("firestore_session_saved", session_id=session.id)

    async def save_session_and_queue_report(self, session: Session) -> None:
        """Atomically persist terminal interview state and its report obligation."""
        db = await self._get_db()
        session_ref = db.collection("sessions").document(session.id)
        generation_ref = session_ref.collection("reports").document("generation")
        current_ref = session_ref.collection("reports").document("current")
        tombstone_ref = db.collection("session_tombstones").document(session.id)

        @firestore.async_transactional
        async def save_in_transaction(transaction):
            existing_session = await session_ref.get(transaction=transaction)
            generation = await generation_ref.get(transaction=transaction)
            current = await current_ref.get(transaction=transaction)
            tombstone = await tombstone_ref.get(transaction=transaction)
            existing_data = existing_session.to_dict() or {}
            if tombstone.exists or (
                existing_session.exists
                and (
                    existing_data.get("ownerId") != session.owner_id
                    or existing_data.get("orgId") != session.org_id
                )
            ):
                raise InterviewReportConflict("completed interview not found")
            transaction.set(
                session_ref,
                self._session_record(session),
                merge=True,
            )
            if not generation.exists and not current.exists:
                transaction.set(
                    generation_ref,
                    {
                        "status": "queued",
                        "version": 0,
                        "attemptCount": 0,
                        "leaseToken": None,
                        "leaseExpiresAt": None,
                        "reasonCode": None,
                        "updatedAt": datetime.now(timezone.utc),
                        "ownerId": session.owner_id,
                        "orgId": session.org_id,
                    },
                )

        await save_in_transaction(db.transaction())
        logger.info("firestore_interview_report_queued", session_id=session.id)

    @staticmethod
    def _import_job(record: dict) -> ImportJobState:
        return ImportJobState(
            source_key=record["sourceKey"],
            source_digest=record["sourceDigest"],
            session_id=record["sessionId"],
            owner_id=record["ownerId"],
            org_id=record["orgId"],
            status=record["status"],
            version=record["version"],
            attempt_count=record["attemptCount"],
            lease_token=record.get("leaseToken"),
            lease_expires_at=record.get("leaseExpiresAt"),
            reason_code=record.get("reasonCode"),
            segment_count=record.get("segmentCount", 0),
            updated_at=record["updatedAt"],
        )

    @staticmethod
    def _import_job_record(job: ImportJobState) -> dict:
        return {
            "sourceKey": job.source_key,
            "sourceDigest": job.source_digest,
            "sessionId": job.session_id,
            "ownerId": job.owner_id,
            "orgId": job.org_id,
            "status": job.status.value,
            "version": job.version,
            "attemptCount": job.attempt_count,
            "leaseToken": job.lease_token,
            "leaseExpiresAt": job.lease_expires_at,
            "reasonCode": job.reason_code,
            "segmentCount": job.segment_count,
            "updatedAt": job.updated_at,
        }

    @staticmethod
    def _tombstone_matches_scope(snapshot, *, owner_id: str, org_id: str) -> bool:
        if not snapshot.exists:
            return False
        record = snapshot.to_dict() or {}
        if record.get("ownerId") != owner_id or record.get("orgId") != org_id:
            raise TranscriptImportNotFound("transcript import not found")
        return True

    async def queue_transcript_import(
        self,
        normalized: NormalizedGoogleMeetImport,
        *,
        updated_at: datetime,
    ) -> ImportJobState:
        """Durably expose a queued import before any lease or session write."""
        db = await self._get_db()
        job_ref = db.collection("transcript_import_jobs").document(normalized.source_key)
        tombstone_ref = db.collection("session_tombstones").document(
            normalized.session.id
        )

        @firestore.async_transactional
        async def queue_in_transaction(transaction):
            snapshot = await job_ref.get(transaction=transaction)
            tombstone = await tombstone_ref.get(transaction=transaction)
            if snapshot.exists:
                job = self._import_job(snapshot.to_dict() or {})
                if (
                    job.owner_id != normalized.session.owner_id
                    or job.org_id != normalized.session.org_id
                ):
                    raise TranscriptImportNotFound("transcript import not found")
                if job.source_digest != normalized.source_digest:
                    raise TranscriptImportConflict(
                        "source artifact identity is already bound to different content"
                    )
                if self._tombstone_matches_scope(
                    tombstone,
                    owner_id=job.owner_id,
                    org_id=job.org_id,
                ):
                    raise TranscriptImportDeleted(
                        "deleted transcript import is fenced"
                    )
                return job
            if self._tombstone_matches_scope(
                tombstone,
                owner_id=normalized.session.owner_id or "",
                org_id=normalized.session.org_id or "",
            ):
                raise TranscriptImportDeleted("deleted transcript import is fenced")
            job = ImportJobState(
                source_key=normalized.source_key,
                source_digest=normalized.source_digest,
                session_id=normalized.session.id,
                owner_id=normalized.session.owner_id or "",
                org_id=normalized.session.org_id or "",
                status=ImportJobStatus.QUEUED,
                version=1,
                attempt_count=0,
                updated_at=updated_at,
            )
            transaction.create(job_ref, self._import_job_record(job))
            return job

        return await queue_in_transaction(db.transaction())

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
    ) -> ImportClaim:
        """Claim queued, failed, or expired work with a versioned durable lease."""
        db = await self._get_db()
        job_ref = db.collection("transcript_import_jobs").document(source_key)
        tombstone_ref = db.collection("session_tombstones").document(session_id)

        @firestore.async_transactional
        async def claim_in_transaction(transaction):
            snapshot = await job_ref.get(transaction=transaction)
            tombstone = await tombstone_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise TranscriptImportNotFound("transcript import not found")
            job = self._import_job(snapshot.to_dict() or {})
            if job.owner_id != owner_id or job.org_id != org_id:
                raise TranscriptImportNotFound("transcript import not found")
            if self._tombstone_matches_scope(
                tombstone,
                owner_id=owner_id,
                org_id=org_id,
            ):
                raise TranscriptImportDeleted("deleted transcript import is fenced")
            if job.source_digest != source_digest or job.session_id != session_id:
                raise TranscriptImportConflict(
                    "source artifact identity is already bound to different content"
                )
            if job.status == ImportJobStatus.COMPLETED:
                return ImportClaim(job=job, idempotent_replay=True)
            if (
                job.status == ImportJobStatus.LEASED
                and job.lease_expires_at is not None
                and job.lease_expires_at > updated_at
            ):
                raise TranscriptImportConflict("transcript import has an active lease")
            claimed = job.model_copy(
                update={
                    "status": ImportJobStatus.LEASED,
                    "version": job.version + 1,
                    "attempt_count": job.attempt_count + 1,
                    "lease_token": lease_token,
                    "lease_expires_at": lease_expires_at,
                    "reason_code": None,
                    "updated_at": updated_at,
                }
            )
            transaction.set(job_ref, self._import_job_record(claimed))
            return ImportClaim(job=claimed, idempotent_replay=False)

        return await claim_in_transaction(db.transaction())

    async def commit_transcript_import(
        self,
        normalized: NormalizedGoogleMeetImport,
        *,
        lease_token: str,
        version: int,
        updated_at: datetime,
    ) -> ImportJobState:
        """Publish the completed interview and its report obligation atomically."""
        db = await self._get_db()
        job_ref = db.collection("transcript_import_jobs").document(normalized.source_key)
        session_ref = db.collection("sessions").document(normalized.session.id)
        tombstone_ref = db.collection("session_tombstones").document(normalized.session.id)
        current_report_ref = session_ref.collection("reports").document("current")
        generation_ref = session_ref.collection("reports").document("generation")
        import_ref = session_ref.collection("imports").document("source")

        @firestore.async_transactional
        async def commit_in_transaction(transaction):
            job_snapshot = await job_ref.get(transaction=transaction)
            tombstone = await tombstone_ref.get(transaction=transaction)
            session_snapshot = await session_ref.get(transaction=transaction)
            current_report = await current_report_ref.get(transaction=transaction)
            if not job_snapshot.exists:
                raise TranscriptImportNotFound("transcript import not found")
            job = self._import_job(job_snapshot.to_dict() or {})
            if (
                job.owner_id != normalized.session.owner_id
                or job.org_id != normalized.session.org_id
            ):
                raise TranscriptImportNotFound("transcript import not found")
            if job.source_digest != normalized.source_digest:
                raise TranscriptImportConflict("transcript import digest changed")
            if job.status == ImportJobStatus.COMPLETED:
                return job
            if self._tombstone_matches_scope(
                tombstone,
                owner_id=job.owner_id,
                org_id=job.org_id,
            ):
                raise TranscriptImportDeleted("deleted transcript import is fenced")
            if (
                job.status != ImportJobStatus.LEASED
                or job.version != version
                or job.lease_token != lease_token
                or job.lease_expires_at is None
                or job.lease_expires_at <= updated_at
            ):
                raise TranscriptImportConflict("transcript import lease is stale")
            if session_snapshot.exists or current_report.exists:
                raise TranscriptImportConflict(
                    "deterministic transcript import session already exists"
                )

            transaction.create(session_ref, self._session_record(normalized.session))
            for segment in normalized.segments:
                segment_ref = session_ref.collection("transcript").document(segment.id)
                segment_record = {
                    "text": segment.text,
                    "speaker": segment.speaker,
                    "startTime": segment.start_time,
                    "endTime": segment.end_time,
                    "confidence": segment.confidence,
                    "sequenceNumber": segment.sequence_number,
                    "sequenceScope": "session",
                    "ownerId": normalized.session.owner_id,
                    "orgId": normalized.session.org_id,
                    "importProvenance": normalized.segment_provenance[segment.id],
                }
                transaction.create(segment_ref, segment_record)
            transaction.create(
                import_ref,
                {
                    **normalized.source_provenance,
                    "ownerId": normalized.session.owner_id,
                    "orgId": normalized.session.org_id,
                },
            )
            for source_id, source in normalized.report_sources.items():
                transaction.create(
                    session_ref.collection("report_sources").document(source_id),
                    {
                        **source,
                        "updatedAt": updated_at,
                        "ownerId": normalized.session.owner_id,
                        "orgId": normalized.session.org_id,
                    },
                )
            transaction.create(
                generation_ref,
                {
                    "status": "queued",
                    "version": 0,
                    "attemptCount": 0,
                    "leaseToken": None,
                    "leaseExpiresAt": None,
                    "reasonCode": None,
                    "updatedAt": updated_at,
                    "ownerId": normalized.session.owner_id,
                    "orgId": normalized.session.org_id,
                },
            )
            completed = job.model_copy(
                update={
                    "status": ImportJobStatus.COMPLETED,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "reason_code": None,
                    "segment_count": len(normalized.segments),
                    "updated_at": updated_at,
                }
            )
            transaction.set(job_ref, self._import_job_record(completed))
            return completed

        return await commit_in_transaction(db.transaction())

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
    ) -> ImportJobState:
        """Record a recoverable content-free failure for the active lease."""
        db = await self._get_db()
        job_ref = db.collection("transcript_import_jobs").document(source_key)

        @firestore.async_transactional
        async def fail_in_transaction(transaction):
            snapshot = await job_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise TranscriptImportNotFound("transcript import not found")
            job = self._import_job(snapshot.to_dict() or {})
            if job.owner_id != owner_id or job.org_id != org_id:
                raise TranscriptImportNotFound("transcript import not found")
            if job.status == ImportJobStatus.COMPLETED:
                return job
            if job.version != version or job.lease_token != lease_token:
                raise TranscriptImportConflict("transcript import lease is stale")
            failed = job.model_copy(
                update={
                    "status": ImportJobStatus.FAILED,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "reason_code": reason_code,
                    "updated_at": updated_at,
                }
            )
            transaction.set(job_ref, self._import_job_record(failed))
            return failed

        return await fail_in_transaction(db.transaction())

    async def get_transcript_import_job(
        self, source_key: str, *, owner_id: str, org_id: str
    ) -> ImportJobState:
        """Read content-free import status for its exact principal only."""
        db = await self._get_db()
        snapshot = await db.collection("transcript_import_jobs").document(source_key).get()
        if not snapshot.exists:
            raise TranscriptImportNotFound("transcript import not found")
        job = self._import_job(snapshot.to_dict() or {})
        if job.owner_id != owner_id or job.org_id != org_id:
            raise TranscriptImportNotFound("transcript import not found")
        return job

    async def save_transcript_segment(
        self,
        session_id: str,
        segment: TranscriptSegment,
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        """Save a single transcript segment to the subcollection."""
        db = await self._get_db()
        doc_ref = (
            db.collection("sessions")
            .document(session_id)
            .collection("transcript")
            .document(segment.id)
        )

        data = {
            "text": segment.text,
            "speaker": segment.speaker,
            "startTime": segment.start_time,
            "endTime": segment.end_time,
            "confidence": segment.confidence,
            "sequenceNumber": segment.sequence_number,
            "sequenceScope": "session",
            "ownerId": owner_id,
            "orgId": org_id,
        }
        if segment.source_sequence_number is not None:
            data["sourceSequenceNumber"] = segment.source_sequence_number
        if segment.speaker_override:
            data["speakerOverride"] = segment.speaker_override
        await doc_ref.set(data)

    async def save_transcript_batch(
        self,
        session_id: str,
        segments: list[TranscriptSegment],
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        """Save multiple transcript segments in a batch write."""
        if not segments:
            return

        db = await self._get_db()
        batch = db.batch()
        session_ref = db.collection("sessions").document(session_id)

        for segment in segments:
            doc_ref = session_ref.collection("transcript").document(segment.id)
            data = {
                "text": segment.text,
                "speaker": segment.speaker,
                "startTime": segment.start_time,
                "endTime": segment.end_time,
                "confidence": segment.confidence,
                "sequenceNumber": segment.sequence_number,
                "sequenceScope": "session",
                "ownerId": owner_id,
                "orgId": org_id,
            }
            if segment.source_sequence_number is not None:
                data["sourceSequenceNumber"] = segment.source_sequence_number
            if segment.speaker_override:
                data["speakerOverride"] = segment.speaker_override
            batch.set(doc_ref, data)

        await batch.commit()
        logger.info(
            "firestore_transcript_batch_saved",
            session_id=session_id,
            count=len(segments),
        )

    async def save_summary(
        self,
        session_id: str,
        text: str,
        covering_from: int,
        covering_to: int,
        is_final: bool = False,
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        """Save a rolling or final summary."""
        db = await self._get_db()
        session_ref = db.collection("sessions").document(session_id)

        summary_ref = session_ref.collection("summaries").document()
        await summary_ref.set({
            "text": text,
            "generatedAt": datetime.now(timezone.utc),
            "coveringFrom": covering_from,
            "coveringTo": covering_to,
            "isFinal": is_final,
            "ownerId": owner_id,
            "orgId": org_id,
        })

        # Also update the session's summary field if final
        if is_final:
            await session_ref.update({"summary": text})

        logger.info(
            "firestore_summary_saved",
            session_id=session_id,
            is_final=is_final,
            covering=f"{covering_from}-{covering_to}",
        )

    async def save_document_metadata(
        self,
        session_id: str,
        doc_type: str,
        file_name: str,
        extracted_text: str,
        gcs_path: str,
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        """Save uploaded document metadata (resume/JD)."""
        db = await self._get_db()
        doc_ref = (
            db.collection("sessions")
            .document(session_id)
            .collection("documents")
            .document()
        )

        await doc_ref.set({
            "type": doc_type,
            "fileName": file_name,
            "extractedText": extracted_text,
            "gcsPath": gcs_path,
            "uploadedAt": datetime.now(timezone.utc),
            "ownerId": owner_id,
            "orgId": org_id,
        })

    async def save_interview_context(
        self,
        session_id: str,
        context_type: str,
        text: str,
    ) -> None:
        """Persist report source context under the session that owns it."""
        db = await self._get_db()
        session_ref = db.collection("sessions").document(session_id)
        source_ref = session_ref.collection("report_sources").document(context_type)
        generation_ref = session_ref.collection("reports").document("generation")
        current_ref = session_ref.collection("reports").document("current")

        @firestore.async_transactional
        async def save_in_transaction(transaction):
            session = await session_ref.get(transaction=transaction)
            generation = await generation_ref.get(transaction=transaction)
            current = await current_ref.get(transaction=transaction)
            session_data = session.to_dict() or {}
            if (
                not session.exists
                or session_data.get("mode") != "interview"
                or session_data.get("status") != "active"
                or generation.exists
                or current.exists
            ):
                raise InterviewReportConflict(
                    "report sources are immutable after the interview ends"
                )
            transaction.set(source_ref, {
                "type": context_type,
                "text": text,
                "updatedAt": datetime.now(timezone.utc),
                "ownerId": session_data.get("ownerId"),
                "orgId": session_data.get("orgId"),
            })

        await save_in_transaction(db.transaction())

    async def get_interview_context(self, session_id: str) -> list[dict]:
        """Read durable CV/JD/briefing context for report provenance."""
        db = await self._get_db()
        collection = (
            db.collection("sessions")
            .document(session_id)
            .collection("report_sources")
        )
        records = []
        async for doc in collection.stream():
            data = doc.to_dict() or {}
            data["id"] = doc.id
            records.append(data)
        return records

    async def list_sessions(
        self,
        limit: int = 50,
        *,
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> list[dict]:
        """List recent sessions ordered by start time."""
        db = await self._get_db()
        query = db.collection("sessions")
        if owner_id is not None:
            query = query.where("ownerId", "==", owner_id)
        if org_id is not None:
            query = query.where("orgId", "==", org_id)
        query = query.order_by("startedAt", direction=firestore.Query.DESCENDING).limit(limit)

        sessions = []
        async for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            sessions.append(data)

        return sessions

    async def get_session_record(self, session_id: str) -> dict | None:
        """Read one durable session document without mutating it."""
        db = await self._get_db()
        snapshot = await db.collection("sessions").document(session_id).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        return data

    async def get_session_transcript(self, session_id: str) -> list[dict]:
        """Get all transcript segments for a session."""
        db = await self._get_db()
        query = (
            db.collection("sessions")
            .document(session_id)
            .collection("transcript")
            .order_by("sequenceNumber")
        )

        segments = []
        async for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            segments.append(data)

        return segments

    async def save_recruiter_note(
        self,
        session: Session,
        request: CreateRecruiterNoteRequest,
    ) -> RecruiterNote:
        """Create a note only when its transcript anchor is durable."""
        db = await self._get_db()
        session_ref = db.collection("sessions").document(session.id)
        segment_ref = (
            session_ref.collection("transcript")
            .document(request.transcript_segment_id)
        )
        note_ref = (
            session_ref
            .collection("notes")
            .document(request.client_note_id)
        )

        @firestore.async_transactional
        async def create_in_transaction(transaction):
            segment_snapshot = await segment_ref.get(transaction=transaction)
            if not segment_snapshot.exists:
                raise RecruiterNoteError(
                    "final transcript segment is not durable; retry shortly"
                )
            segment_data = segment_snapshot.to_dict() or {}
            try:
                segment = TranscriptSegment(
                    id=segment_snapshot.id,
                    text=segment_data.get("text", ""),
                    speaker=segment_data.get("speaker", "Speaker 1"),
                    start_time=segment_data.get("startTime", 0.0),
                    end_time=segment_data["endTime"],
                    confidence=segment_data.get("confidence", 0.0),
                    sequence_number=segment_data.get("sequenceNumber", 0),
                    is_final=True,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RecruiterNoteError(
                    "durable transcript segment is invalid"
                ) from exc

            note = build_recruiter_note(session, segment, request)
            note_snapshot = await note_ref.get(transaction=transaction)
            if note_snapshot.exists:
                existing = note_snapshot.to_dict() or {}
                existing["id"] = note_snapshot.id
                durable_note = deserialize_recruiter_notes(
                    note.session_id,
                    [existing],
                )[0]
                if not same_note_payload(durable_note, note):
                    raise RecruiterNoteConflict(
                        "note identity was already used for different evidence"
                    )
                return durable_note

            transaction.create(
                note_ref,
                {
                    "kind": note.kind.value,
                    "text": note.text,
                    "transcriptSegmentId": note.transcript_segment_id,
                    "transcriptOffsetMs": note.transcript_offset_ms,
                    "source": note.source,
                    "createdAt": note.created_at,
                    "ownerId": session.owner_id,
                    "orgId": session.org_id,
                },
            )
            return note

        durable_note = await create_in_transaction(db.transaction())
        logger.info(
            "recruiter_note_saved",
            session_id=durable_note.session_id,
            note_id=durable_note.id,
            kind=durable_note.kind.value,
        )
        return durable_note

    async def get_session_notes(self, session_id: str) -> list[dict]:
        """Read durable recruiter notes for restart-safe downstream review."""
        db = await self._get_db()
        query = (
            db.collection("sessions")
            .document(session_id)
            .collection("notes")
            .order_by("createdAt")
        )

        notes = []
        async for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            notes.append(data)
        return notes

    @staticmethod
    def _report_generation_job(
        session_id: str, record: dict
    ) -> ReportGenerationJob:
        return ReportGenerationJob(
            session_id=session_id,
            owner_id=record.get("ownerId"),
            org_id=record.get("orgId"),
            status=record["status"],
            version=record.get("version", 0),
            attempt_count=record.get("attemptCount", 0),
            lease_token=record.get("leaseToken"),
            lease_expires_at=record.get("leaseExpiresAt"),
            reason_code=record.get("reasonCode"),
            updated_at=record["updatedAt"],
        )

    @staticmethod
    def _report_generation_record(job: ReportGenerationJob) -> dict:
        return {
            "status": job.status.value,
            "version": job.version,
            "attemptCount": job.attempt_count,
            "leaseToken": job.lease_token,
            "leaseExpiresAt": job.lease_expires_at,
            "reasonCode": job.reason_code,
            "ownerId": job.owner_id,
            "orgId": job.org_id,
            "updatedAt": job.updated_at,
        }

    @staticmethod
    def _validate_report_transaction_scope(
        session_id: str,
        *,
        session_snapshot,
        current_snapshot,
        generation_snapshot,
        tombstone_snapshot,
        owner_id: str | None,
        org_id: str | None,
    ) -> tuple[ReportGenerationJob, InterviewReport | None, dict]:
        if tombstone_snapshot.exists or not session_snapshot.exists:
            raise ReportGenerationNotFound("completed interview not found")
        session_data = session_snapshot.to_dict() or {}
        if (
            session_data.get("mode") != "interview"
            or session_data.get("status") != "completed"
            or session_data.get("ownerId") != owner_id
            or session_data.get("orgId") != org_id
        ):
            raise ReportGenerationNotFound("completed interview not found")
        if not generation_snapshot.exists:
            raise ReportGenerationNotFound("report generation job not found")
        job = FirestoreStorage._report_generation_job(
            session_id, generation_snapshot.to_dict() or {}
        )
        if job.owner_id != owner_id or job.org_id != org_id:
            raise ReportGenerationNotFound("report generation job not found")
        current = None
        if current_snapshot.exists:
            current = report_from_record(
                session_id, current_snapshot.to_dict() or {}
            )
            if current.owner_id != owner_id or current.org_id != org_id:
                raise ReportGenerationNotFound("completed interview not found")
        return job, current, session_data

    async def claim_report_generation(
        self,
        session_id: str,
        *,
        owner_id: str | None,
        org_id: str | None,
        lease_token: str,
        lease_expires_at: datetime,
        updated_at: datetime,
    ) -> ReportGenerationClaim:
        """Claim queued or expired report work without process-local state."""
        db = await self._get_db()
        session_ref = db.collection("sessions").document(session_id)
        current_ref = session_ref.collection("reports").document("current")
        generation_ref = session_ref.collection("reports").document("generation")
        tombstone_ref = db.collection("session_tombstones").document(session_id)

        @firestore.async_transactional
        async def claim_in_transaction(transaction):
            session_snapshot = await session_ref.get(transaction=transaction)
            current_snapshot = await current_ref.get(transaction=transaction)
            generation_snapshot = await generation_ref.get(transaction=transaction)
            tombstone_snapshot = await tombstone_ref.get(transaction=transaction)
            job, current, _ = self._validate_report_transaction_scope(
                session_id,
                session_snapshot=session_snapshot,
                current_snapshot=current_snapshot,
                generation_snapshot=generation_snapshot,
                tombstone_snapshot=tombstone_snapshot,
                owner_id=owner_id,
                org_id=org_id,
            )
            if job.status == ReportGenerationStatus.READY:
                if current is None:
                    raise ReportGenerationConflict("ready report is missing")
                return ReportGenerationClaim(
                    job=job,
                    current_report=current,
                    idempotent_ready=True,
                )
            if job.status == ReportGenerationStatus.FAILED:
                raise ReportGenerationConflict("report generation previously failed")
            if (
                job.status == ReportGenerationStatus.GENERATING
                and job.lease_expires_at is not None
                and job.lease_expires_at > updated_at
            ):
                raise ReportGenerationConflict("report generation has an active lease")
            if job.status not in {
                ReportGenerationStatus.QUEUED,
                ReportGenerationStatus.GENERATING,
            }:
                raise ReportGenerationConflict("report generation state is invalid")
            claimed = job.model_copy(
                update={
                    "status": ReportGenerationStatus.GENERATING,
                    "version": job.version + 1,
                    "attempt_count": job.attempt_count + 1,
                    "lease_token": lease_token,
                    "lease_expires_at": lease_expires_at,
                    "reason_code": None,
                    "updated_at": updated_at,
                }
            )
            transaction.set(
                generation_ref, self._report_generation_record(claimed)
            )
            return ReportGenerationClaim(
                job=claimed,
                current_report=current,
                idempotent_ready=False,
            )

        return await claim_in_transaction(db.transaction())

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
    ) -> ReportGenerationJob:
        """Fail only the exact active report lease while the parent still exists."""
        db = await self._get_db()
        session_ref = db.collection("sessions").document(session_id)
        current_ref = session_ref.collection("reports").document("current")
        generation_ref = session_ref.collection("reports").document("generation")
        tombstone_ref = db.collection("session_tombstones").document(session_id)

        @firestore.async_transactional
        async def fail_in_transaction(transaction):
            session_snapshot = await session_ref.get(transaction=transaction)
            current_snapshot = await current_ref.get(transaction=transaction)
            generation_snapshot = await generation_ref.get(transaction=transaction)
            tombstone_snapshot = await tombstone_ref.get(transaction=transaction)
            job, _, _ = self._validate_report_transaction_scope(
                session_id,
                session_snapshot=session_snapshot,
                current_snapshot=current_snapshot,
                generation_snapshot=generation_snapshot,
                tombstone_snapshot=tombstone_snapshot,
                owner_id=owner_id,
                org_id=org_id,
            )
            if (
                job.status != ReportGenerationStatus.GENERATING
                or job.version != version
                or job.lease_token != lease_token
                or job.lease_expires_at is None
                or job.lease_expires_at <= updated_at
            ):
                raise ReportGenerationConflict("report generation lease is stale")
            failed = job.model_copy(
                update={
                    "status": ReportGenerationStatus.FAILED,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "reason_code": reason_code,
                    "updated_at": updated_at,
                }
            )
            transaction.set(
                generation_ref, self._report_generation_record(failed)
            )
            return failed

        return await fail_in_transaction(db.transaction())

    async def complete_report_generation(
        self,
        report: InterviewReport,
        *,
        lease_token: str,
        version: int,
        updated_at: datetime,
    ) -> InterviewReport:
        """Atomically publish one draft, parent summary, and ready state."""
        db = await self._get_db()
        session_ref = db.collection("sessions").document(report.session_id)
        current_ref = session_ref.collection("reports").document("current")
        generation_ref = session_ref.collection("reports").document("generation")
        tombstone_ref = db.collection("session_tombstones").document(report.session_id)

        @firestore.async_transactional
        async def complete_in_transaction(transaction):
            session_snapshot = await session_ref.get(transaction=transaction)
            current_snapshot = await current_ref.get(transaction=transaction)
            generation_snapshot = await generation_ref.get(transaction=transaction)
            tombstone_snapshot = await tombstone_ref.get(transaction=transaction)
            job, current, _ = self._validate_report_transaction_scope(
                report.session_id,
                session_snapshot=session_snapshot,
                current_snapshot=current_snapshot,
                generation_snapshot=generation_snapshot,
                tombstone_snapshot=tombstone_snapshot,
                owner_id=report.owner_id,
                org_id=report.org_id,
            )
            if (
                job.status != ReportGenerationStatus.GENERATING
                or job.version != version
                or job.lease_token != lease_token
                or job.lease_expires_at is None
                or job.lease_expires_at <= updated_at
            ):
                raise ReportGenerationConflict("report generation lease is stale")
            durable_report = current or report
            if current is None:
                transaction.create(current_ref, report_to_record(report))
            summary = render_internal_summary(durable_report)
            transaction.set(session_ref, {"summary": summary}, merge=True)
            ready = job.model_copy(
                update={
                    "status": ReportGenerationStatus.READY,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "reason_code": None,
                    "updated_at": updated_at,
                }
            )
            transaction.set(
                generation_ref, self._report_generation_record(ready)
            )
            return durable_report

        return await complete_in_transaction(db.transaction())

    async def save_report_generation_state(
        self,
        session_id: str,
        status: str,
        *,
        reason_code: str | None = None,
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        """Guard legacy reconciliation writes with parent and deletion state."""
        db = await self._get_db()
        session_ref = db.collection("sessions").document(session_id)
        current_ref = session_ref.collection("reports").document("current")
        generation_ref = session_ref.collection("reports").document("generation")
        tombstone_ref = db.collection("session_tombstones").document(session_id)
        updated_at = datetime.now(timezone.utc)

        @firestore.async_transactional
        async def save_in_transaction(transaction):
            session_snapshot = await session_ref.get(transaction=transaction)
            current_snapshot = await current_ref.get(transaction=transaction)
            generation_snapshot = await generation_ref.get(transaction=transaction)
            tombstone_snapshot = await tombstone_ref.get(transaction=transaction)
            job, _, _ = self._validate_report_transaction_scope(
                session_id,
                session_snapshot=session_snapshot,
                current_snapshot=current_snapshot,
                generation_snapshot=generation_snapshot,
                tombstone_snapshot=tombstone_snapshot,
                owner_id=owner_id,
                org_id=org_id,
            )
            updated = job.model_copy(
                update={
                    "status": ReportGenerationStatus(status),
                    "lease_token": None,
                    "lease_expires_at": None,
                    "reason_code": reason_code,
                    "updated_at": updated_at,
                }
            )
            transaction.set(
                generation_ref, self._report_generation_record(updated)
            )

        await save_in_transaction(db.transaction())

    async def get_report_generation_state(self, session_id: str) -> dict | None:
        """Read the content-free report generation state."""
        db = await self._get_db()
        snapshot = await (
            db.collection("sessions")
            .document(session_id)
            .collection("reports")
            .document("generation")
            .get()
        )
        if not snapshot.exists:
            return None
        return snapshot.to_dict() or {}

    async def save_generated_report(self, report: InterviewReport) -> InterviewReport:
        """Create one draft only while its completed parent is not deleted."""
        db = await self._get_db()
        session_ref = db.collection("sessions").document(report.session_id)
        doc_ref = (
            session_ref.collection("reports").document("current")
        )
        tombstone_ref = db.collection("session_tombstones").document(report.session_id)

        @firestore.async_transactional
        async def create_in_transaction(transaction):
            session = await session_ref.get(transaction=transaction)
            existing_snapshot = await doc_ref.get(transaction=transaction)
            tombstone = await tombstone_ref.get(transaction=transaction)
            if tombstone.exists or not session.exists:
                raise InterviewReportConflict("completed interview not found")
            session_data = session.to_dict() or {}
            if (
                session_data.get("mode") != "interview"
                or session_data.get("status") != "completed"
                or session_data.get("ownerId") != report.owner_id
                or session_data.get("orgId") != report.org_id
            ):
                raise InterviewReportConflict("completed interview not found")
            if existing_snapshot.exists:
                existing = report_from_record(
                    report.session_id, existing_snapshot.to_dict() or {}
                )
                if (
                    existing.owner_id != session_data.get("ownerId")
                    or existing.org_id != session_data.get("orgId")
                ):
                    raise InterviewReportConflict("completed interview not found")
                return existing
            transaction.create(doc_ref, report_to_record(report))
            return report

        return await create_in_transaction(db.transaction())

    async def get_interview_report(self, session_id: str) -> InterviewReport | None:
        """Read the typed report after restart without invoking a provider."""
        db = await self._get_db()
        snapshot = await (
            db.collection("sessions")
            .document(session_id)
            .collection("reports")
            .document("current")
            .get()
        )
        if not snapshot.exists:
            return None
        return report_from_record(session_id, snapshot.to_dict() or {})

    async def update_interview_report(
        self,
        session_id: str,
        request: UpdateInterviewReportRequest,
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> InterviewReport:
        """CAS-update editable prose while preserving evidence and ratings."""
        db = await self._get_db()
        doc_ref = (
            db.collection("sessions")
            .document(session_id)
            .collection("reports")
            .document("current")
        )

        @firestore.async_transactional
        async def update_in_transaction(transaction):
            snapshot = await doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise InterviewReportConflict("report not found")
            record = snapshot.to_dict() or {}
            if owner_id is not None and org_id is not None and (
                record.get("ownerId") != owner_id or record.get("orgId") != org_id
            ):
                raise InterviewReportConflict("report not found")
            report = report_from_record(session_id, record)
            updated = update_report_content(report, request)
            transaction.set(doc_ref, report_to_record(updated))
            return updated

        return await update_in_transaction(db.transaction())

    async def approve_interview_report(
        self,
        session_id: str,
        request: ApproveInterviewReportRequest,
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> InterviewReport:
        """Transactionally pin one immutable, idempotently replayable version."""
        db = await self._get_db()
        doc_ref = (
            db.collection("sessions")
            .document(session_id)
            .collection("reports")
            .document("current")
        )

        @firestore.async_transactional
        async def approve_in_transaction(transaction):
            snapshot = await doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise InterviewReportConflict("report not found")
            record = snapshot.to_dict() or {}
            if owner_id is not None and org_id is not None and (
                record.get("ownerId") != owner_id or record.get("orgId") != org_id
            ):
                raise InterviewReportConflict("report not found")
            report = report_from_record(session_id, record)
            approved = approve_report(report, request.expected_version)
            if approved != report:
                transaction.set(doc_ref, report_to_record(approved))
            return approved

        return await approve_in_transaction(db.transaction())
