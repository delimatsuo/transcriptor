"""Firestore read/write operations."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from google.api_core.exceptions import AlreadyExists
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
    update_report_content,
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

        @firestore.async_transactional
        async def save_in_transaction(transaction):
            generation = await generation_ref.get(transaction=transaction)
            current = await current_ref.get(transaction=transaction)
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
                        "reasonCode": None,
                        "updatedAt": datetime.now(timezone.utc),
                        "ownerId": session.owner_id,
                        "orgId": session.org_id,
                    },
                )

        await save_in_transaction(db.transaction())
        logger.info("firestore_interview_report_queued", session_id=session.id)

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
            "ownerId": owner_id,
            "orgId": org_id,
        }
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
                "ownerId": owner_id,
                "orgId": org_id,
            }
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
            "generatedAt": datetime.utcnow(),
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
            "uploadedAt": datetime.utcnow(),
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

    async def save_report_generation_state(
        self,
        session_id: str,
        status: str,
        *,
        reason_code: str | None = None,
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        """Persist a content-free report generation state for visible failures."""
        db = await self._get_db()
        doc_ref = (
            db.collection("sessions")
            .document(session_id)
            .collection("reports")
            .document("generation")
        )
        await doc_ref.set({
            "status": status,
            "reasonCode": reason_code,
            "updatedAt": datetime.now(timezone.utc),
            "ownerId": owner_id,
            "orgId": org_id,
        })

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
        """Create the first draft without overwriting human work or approval."""
        db = await self._get_db()
        doc_ref = (
            db.collection("sessions")
            .document(report.session_id)
            .collection("reports")
            .document("current")
        )
        try:
            await doc_ref.create(report_to_record(report))
            return report
        except AlreadyExists:
            snapshot = await doc_ref.get()
            if not snapshot.exists:
                raise InterviewReportConflict(
                    "report exists but cannot be read"
                ) from None
            return report_from_record(report.session_id, snapshot.to_dict() or {})

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
