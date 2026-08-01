"""Firestore read/write operations."""

from __future__ import annotations

from datetime import datetime

import structlog
from google.cloud import firestore

from backend.config import Settings
from backend.schemas.models import (
    ActionItem,
    Session,
    SessionStatus,
    TranscriptSegment,
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

    async def save_session(self, session: Session) -> None:
        """Save or update a session document."""
        db = await self._get_db()
        doc_ref = db.collection("sessions").document(session.id)

        data = {
            "mode": session.mode.value,
            "title": session.title,
            "startedAt": session.started_at,
            "endedAt": session.ended_at,
            "lastActive": session.last_active,
            "status": session.status.value,
            "speakerMap": session.speaker_map,
            "summary": session.summary,
            "actionItems": [item.model_dump() for item in session.action_items],
        }

        await doc_ref.set(data, merge=True)
        logger.info("firestore_session_saved", session_id=session.id)

    async def save_transcript_segment(
        self, session_id: str, segment: TranscriptSegment
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
        }
        if segment.speaker_override:
            data["speakerOverride"] = segment.speaker_override
        await doc_ref.set(data)

    async def save_transcript_batch(
        self, session_id: str, segments: list[TranscriptSegment]
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
        })

    async def list_sessions(self, limit: int = 50) -> list[dict]:
        """List recent sessions ordered by start time."""
        db = await self._get_db()
        query = (
            db.collection("sessions")
            .order_by("startedAt", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )

        sessions = []
        async for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            sessions.append(data)

        return sessions

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
