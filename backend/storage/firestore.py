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
from backend.workers.meet_transcript_automation import (
    AUTOMATION_FAILURE_REASONS,
    AutomationFailureReason,
    AutomationEventClaim,
    AutomationEventState,
    AutomationEventStatus,
    EligibleMeetEventBinding,
    MeetAutomationConflict,
    MeetAutomationNotFound,
    ReconciliationLease,
    WorkspaceGrant,
    eligible_binding_key,
    grant_identity_key,
    manual_binding_lookup_key,
    push_binding_lookup_key,
)
from backend.workers.interview_report import (
    ReportGenerationClaim,
    ReportGenerationConflict,
    ReportGenerationJob,
    ReportGenerationNotFound,
    ReportGenerationStatus,
)

logger = structlog.get_logger()

_WORKSPACE_GRANT_RECORD_KEYS = frozenset(
    {
        "grantId",
        "ownerId",
        "orgId",
        "workspaceSubject",
        "status",
        "scopes",
        "validFrom",
        "expiresAt",
        "updatedAt",
    }
)
_ELIGIBLE_MEET_BINDING_RECORD_KEYS = frozenset(
    {
        "bindingKey",
        "grantId",
        "ownerId",
        "orgId",
        "workspaceSubject",
        "calendarId",
        "calendarEventId",
        "meetTarget",
        "workspaceSubscriptionSource",
        "pubsubSubscription",
        "title",
        "noticeGiven",
        "noticeProvenance",
        "candidateId",
        "candidateName",
        "resumeArtifactId",
        "resumeText",
        "jobDescriptionArtifactId",
        "jobDescriptionText",
        "briefing",
        "createdAt",
    }
)
_MEET_AUTOMATION_EVENT_RECORD_KEYS = frozenset(
    {
        "eventKey",
        "eventId",
        "trigger",
        "ownerId",
        "orgId",
        "grantId",
        "workspaceSubject",
        "calendarId",
        "calendarEventId",
        "meetTarget",
        "workspaceSubscriptionSource",
        "pubsubSubscription",
        "transcriptName",
        "status",
        "version",
        "attemptCount",
        "leaseToken",
        "leaseExpiresAt",
        "reasonCode",
        "importSessionId",
        "importSourceKey",
        "importSourceDigest",
        "importSegmentCount",
        "importAttemptCount",
        "updatedAt",
    }
)
_RECONCILIATION_LEASE_RECORD_KEYS = frozenset(
    {
        "scopeKey",
        "ownerId",
        "orgId",
        "grantId",
        "version",
        "attemptCount",
        "leaseToken",
        "leaseExpiresAt",
        "updatedAt",
        "cursorBindingKey",
    }
)
_BINDING_INDEX_RECORD_KEYS = frozenset({"bindingKey"})


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

    @staticmethod
    def _workspace_grant_record(grant: WorkspaceGrant) -> dict:
        return {
            "grantId": grant.grant_id,
            "ownerId": grant.owner_id,
            "orgId": grant.org_id,
            "workspaceSubject": grant.workspace_subject,
            "status": grant.status.value,
            "scopes": sorted(grant.scopes),
            "validFrom": grant.valid_from,
            "expiresAt": grant.expires_at,
            "updatedAt": grant.updated_at,
        }

    @staticmethod
    def _workspace_grant(record: dict) -> WorkspaceGrant:
        if set(record) != _WORKSPACE_GRANT_RECORD_KEYS:
            raise MeetAutomationNotFound("workspace authority not found")
        return WorkspaceGrant(
            grant_id=record["grantId"],
            owner_id=record["ownerId"],
            org_id=record["orgId"],
            workspace_subject=record["workspaceSubject"],
            status=record["status"],
            scopes=frozenset(record["scopes"]),
            valid_from=record["validFrom"],
            expires_at=record["expiresAt"],
            updated_at=record["updatedAt"],
        )

    @staticmethod
    def _eligible_meet_binding_record(binding: EligibleMeetEventBinding) -> dict:
        return {
            "bindingKey": eligible_binding_key(binding),
            "grantId": binding.grant_id,
            "ownerId": binding.owner_id,
            "orgId": binding.org_id,
            "workspaceSubject": binding.workspace_subject,
            "calendarId": binding.calendar_id,
            "calendarEventId": binding.calendar_event_id,
            "meetTarget": binding.meet_target,
            "workspaceSubscriptionSource": binding.workspace_subscription_source,
            "pubsubSubscription": binding.pubsub_subscription,
            "title": binding.title,
            "noticeGiven": binding.notice_given,
            "noticeProvenance": binding.notice_provenance,
            "candidateId": binding.candidate_id,
            "candidateName": binding.candidate_name,
            "resumeArtifactId": binding.resume_artifact_id,
            "resumeText": binding.resume_text,
            "jobDescriptionArtifactId": binding.job_description_artifact_id,
            "jobDescriptionText": binding.job_description_text,
            "briefing": binding.briefing,
            "createdAt": binding.created_at,
        }

    @staticmethod
    def _eligible_meet_binding(record: dict) -> EligibleMeetEventBinding:
        if set(record) != _ELIGIBLE_MEET_BINDING_RECORD_KEYS:
            raise MeetAutomationNotFound("eligible Meet event not found")
        return EligibleMeetEventBinding(
            grant_id=record["grantId"],
            owner_id=record["ownerId"],
            org_id=record["orgId"],
            workspace_subject=record["workspaceSubject"],
            calendar_id=record["calendarId"],
            calendar_event_id=record["calendarEventId"],
            meet_target=record["meetTarget"],
            workspace_subscription_source=record["workspaceSubscriptionSource"],
            pubsub_subscription=record["pubsubSubscription"],
            title=record["title"],
            notice_given=record["noticeGiven"],
            notice_provenance=record["noticeProvenance"],
            candidate_id=record.get("candidateId"),
            candidate_name=record.get("candidateName"),
            resume_artifact_id=record.get("resumeArtifactId"),
            resume_text=record.get("resumeText"),
            job_description_artifact_id=record.get("jobDescriptionArtifactId"),
            job_description_text=record.get("jobDescriptionText"),
            briefing=record.get("briefing"),
            created_at=record["createdAt"],
        )

    @staticmethod
    def _meet_automation_event_record(event: AutomationEventState) -> dict:
        return {
            "eventKey": event.event_key,
            "eventId": event.event_id,
            "trigger": event.trigger,
            "ownerId": event.owner_id,
            "orgId": event.org_id,
            "grantId": event.grant_id,
            "workspaceSubject": event.workspace_subject,
            "calendarId": event.calendar_id,
            "calendarEventId": event.calendar_event_id,
            "meetTarget": event.meet_target,
            "workspaceSubscriptionSource": event.workspace_subscription_source,
            "pubsubSubscription": event.pubsub_subscription,
            "transcriptName": event.transcript_name,
            "status": event.status.value,
            "version": event.version,
            "attemptCount": event.attempt_count,
            "leaseToken": event.lease_token,
            "leaseExpiresAt": event.lease_expires_at,
            "reasonCode": event.reason_code,
            "importSessionId": event.import_session_id,
            "importSourceKey": event.import_source_key,
            "importSourceDigest": event.import_source_digest,
            "importSegmentCount": event.import_segment_count,
            "importAttemptCount": event.import_attempt_count,
            "updatedAt": event.updated_at,
        }

    @staticmethod
    def _meet_automation_event(record: dict) -> AutomationEventState:
        if set(record) != _MEET_AUTOMATION_EVENT_RECORD_KEYS:
            raise MeetAutomationConflict("automation event record is invalid")
        return AutomationEventState(
            event_key=record["eventKey"],
            event_id=record["eventId"],
            trigger=record["trigger"],
            owner_id=record["ownerId"],
            org_id=record["orgId"],
            grant_id=record["grantId"],
            workspace_subject=record["workspaceSubject"],
            calendar_id=record["calendarId"],
            calendar_event_id=record["calendarEventId"],
            meet_target=record["meetTarget"],
            workspace_subscription_source=record["workspaceSubscriptionSource"],
            pubsub_subscription=record["pubsubSubscription"],
            transcript_name=record["transcriptName"],
            status=record["status"],
            version=record["version"],
            attempt_count=record["attemptCount"],
            lease_token=record.get("leaseToken"),
            lease_expires_at=record.get("leaseExpiresAt"),
            reason_code=record.get("reasonCode"),
            import_session_id=record.get("importSessionId"),
            import_source_key=record.get("importSourceKey"),
            import_source_digest=record.get("importSourceDigest"),
            import_segment_count=record.get("importSegmentCount", 0),
            import_attempt_count=record.get("importAttemptCount", 0),
            updated_at=record["updatedAt"],
        )

    @staticmethod
    def _binding_index(record: dict) -> str:
        if set(record) != _BINDING_INDEX_RECORD_KEYS:
            raise MeetAutomationNotFound("eligible Meet event not found")
        binding_key = record["bindingKey"]
        if (
            not isinstance(binding_key, str)
            or len(binding_key) != 64
            or any(char not in "0123456789abcdef" for char in binding_key)
        ):
            raise MeetAutomationNotFound("eligible Meet event not found")
        return binding_key

    @classmethod
    def _canonical_binding(cls, record: dict, *, document_id: str) -> EligibleMeetEventBinding:
        binding = cls._eligible_meet_binding(record)
        if (
            record["bindingKey"] != document_id
            or eligible_binding_key(binding) != document_id
        ):
            raise MeetAutomationNotFound("eligible Meet event not found")
        return binding

    @staticmethod
    def _same_automation_identity(
        left: AutomationEventState, right: AutomationEventState
    ) -> bool:
        identity_fields = (
            "event_key",
            "event_id",
            "trigger",
            "owner_id",
            "org_id",
            "grant_id",
            "workspace_subject",
            "calendar_id",
            "calendar_event_id",
            "meet_target",
            "workspace_subscription_source",
            "pubsub_subscription",
            "transcript_name",
        )
        return all(getattr(left, field) == getattr(right, field) for field in identity_fields)

    async def store_workspace_grant(self, grant: WorkspaceGrant) -> WorkspaceGrant:
        """Seed credential-free grant metadata through an exact identity transaction."""
        db = await self._get_db()
        ref = db.collection("workspace_grants").document(
            grant_identity_key(
                owner_id=grant.owner_id,
                org_id=grant.org_id,
                grant_id=grant.grant_id,
            )
        )

        @firestore.async_transactional
        async def store_in_transaction(transaction):
            snapshot = await ref.get(transaction=transaction)
            if snapshot.exists:
                try:
                    existing = self._workspace_grant(snapshot.to_dict() or {})
                except (KeyError, TypeError, ValueError):
                    raise MeetAutomationConflict(
                        "workspace grant identity conflict"
                    ) from None
                if existing != grant:
                    raise MeetAutomationConflict("workspace grant identity conflict")
                return existing
            transaction.create(ref, self._workspace_grant_record(grant))
            return grant

        return await store_in_transaction(db.transaction())

    async def get_workspace_grant(
        self, grant_id: str, *, owner_id: str, org_id: str
    ) -> WorkspaceGrant:
        db = await self._get_db()
        snapshot = await db.collection("workspace_grants").document(
            grant_identity_key(owner_id=owner_id, org_id=org_id, grant_id=grant_id)
        ).get()
        if not snapshot.exists:
            raise MeetAutomationNotFound("workspace authority not found")
        try:
            grant = self._workspace_grant(snapshot.to_dict() or {})
        except (KeyError, TypeError, ValueError):
            raise MeetAutomationNotFound("workspace authority not found") from None
        if (
            grant.grant_id != grant_id
            or grant.owner_id != owner_id
            or grant.org_id != org_id
        ):
            raise MeetAutomationNotFound("workspace authority not found")
        return grant

    async def store_eligible_meet_binding(
        self, binding: EligibleMeetEventBinding
    ) -> EligibleMeetEventBinding:
        """Seed one exact eligible event and both exact lookup indexes."""
        db = await self._get_db()
        binding_id = eligible_binding_key(binding)
        binding_ref = db.collection("workspace_meet_eligible_events").document(binding_id)
        manual_id = manual_binding_lookup_key(
            owner_id=binding.owner_id,
            org_id=binding.org_id,
            grant_id=binding.grant_id,
            calendar_id=binding.calendar_id,
            calendar_event_id=binding.calendar_event_id,
        )
        push_id = push_binding_lookup_key(
            workspace_subscription_source=binding.workspace_subscription_source,
            pubsub_subscription=binding.pubsub_subscription,
            meet_target=binding.meet_target,
        )
        manual_ref = db.collection("workspace_meet_manual_index").document(manual_id)
        push_ref = db.collection("workspace_meet_push_index").document(push_id)
        scope_id = manual_binding_lookup_key(
            owner_id=binding.owner_id,
            org_id=binding.org_id,
            grant_id=binding.grant_id,
            calendar_id="reconciliation-scope",
            calendar_event_id="eligible-events",
        )
        list_ref = (
            db.collection("workspace_meet_grant_scopes")
            .document(scope_id)
            .collection("eligible_events")
            .document(binding_id)
        )

        @firestore.async_transactional
        async def store_in_transaction(transaction):
            binding_snapshot = await binding_ref.get(transaction=transaction)
            manual_snapshot = await manual_ref.get(transaction=transaction)
            push_snapshot = await push_ref.get(transaction=transaction)
            list_snapshot = await list_ref.get(transaction=transaction)
            if binding_snapshot.exists:
                try:
                    existing = self._canonical_binding(
                        binding_snapshot.to_dict() or {}, document_id=binding_id
                    )
                except (KeyError, TypeError, ValueError):
                    raise MeetAutomationConflict(
                        "eligible Meet event identity conflict"
                    ) from None
                if existing != binding:
                    raise MeetAutomationConflict("eligible Meet event identity conflict")
            for snapshot in (manual_snapshot, push_snapshot, list_snapshot):
                if snapshot.exists:
                    try:
                        existing_index = self._binding_index(snapshot.to_dict() or {})
                    except (KeyError, TypeError, ValueError):
                        raise MeetAutomationConflict(
                            "eligible Meet event identity conflict"
                        ) from None
                    if existing_index != binding_id:
                        raise MeetAutomationConflict(
                            "eligible Meet event identity conflict"
                        )
            record = self._eligible_meet_binding_record(binding)
            if not binding_snapshot.exists:
                transaction.create(binding_ref, record)
            if not manual_snapshot.exists:
                transaction.create(manual_ref, {"bindingKey": binding_id})
            if not push_snapshot.exists:
                transaction.create(push_ref, {"bindingKey": binding_id})
            if not list_snapshot.exists:
                transaction.create(list_ref, {"bindingKey": binding_id})
            return binding

        return await store_in_transaction(db.transaction())

    async def get_eligible_meet_binding_manual(
        self,
        *,
        owner_id: str,
        org_id: str,
        grant_id: str,
        calendar_id: str,
        calendar_event_id: str,
    ) -> EligibleMeetEventBinding:
        db = await self._get_db()
        index_id = manual_binding_lookup_key(
            owner_id=owner_id,
            org_id=org_id,
            grant_id=grant_id,
            calendar_id=calendar_id,
            calendar_event_id=calendar_event_id,
        )
        index = await db.collection("workspace_meet_manual_index").document(index_id).get()
        if not index.exists:
            raise MeetAutomationNotFound("eligible Meet event not found")
        try:
            binding_id = self._binding_index(index.to_dict() or {})
        except (KeyError, TypeError, ValueError):
            raise MeetAutomationNotFound("eligible Meet event not found")
        snapshot = await db.collection("workspace_meet_eligible_events").document(binding_id).get()
        if not snapshot.exists:
            raise MeetAutomationNotFound("eligible Meet event not found")
        try:
            binding = self._canonical_binding(
                snapshot.to_dict() or {}, document_id=binding_id
            )
        except (KeyError, TypeError, ValueError):
            raise MeetAutomationNotFound("eligible Meet event not found") from None
        if (
            binding.owner_id != owner_id
            or binding.org_id != org_id
            or binding.grant_id != grant_id
            or binding.calendar_id != calendar_id
            or binding.calendar_event_id != calendar_event_id
        ):
            raise MeetAutomationNotFound("eligible Meet event not found")
        return binding

    async def get_eligible_meet_binding_push(
        self,
        *,
        workspace_subscription_source: str,
        pubsub_subscription: str,
        meet_target: str,
    ) -> EligibleMeetEventBinding:
        db = await self._get_db()
        index_id = push_binding_lookup_key(
            workspace_subscription_source=workspace_subscription_source,
            pubsub_subscription=pubsub_subscription,
            meet_target=meet_target,
        )
        index = await db.collection("workspace_meet_push_index").document(index_id).get()
        if not index.exists:
            raise MeetAutomationNotFound("eligible Meet event not found")
        try:
            binding_id = self._binding_index(index.to_dict() or {})
        except (KeyError, TypeError, ValueError):
            raise MeetAutomationNotFound("eligible Meet event not found")
        snapshot = await db.collection("workspace_meet_eligible_events").document(binding_id).get()
        if not snapshot.exists:
            raise MeetAutomationNotFound("eligible Meet event not found")
        try:
            binding = self._canonical_binding(
                snapshot.to_dict() or {}, document_id=binding_id
            )
        except (KeyError, TypeError, ValueError):
            raise MeetAutomationNotFound("eligible Meet event not found") from None
        if (
            binding.workspace_subscription_source != workspace_subscription_source
            or binding.pubsub_subscription != pubsub_subscription
            or binding.meet_target != meet_target
        ):
            raise MeetAutomationNotFound("eligible Meet event not found")
        return binding

    async def list_eligible_meet_bindings(
        self,
        *,
        owner_id: str,
        org_id: str,
        grant_id: str,
        limit: int,
        after_binding_key: str | None,
    ) -> list[EligibleMeetEventBinding]:
        if after_binding_key is not None and (
            len(after_binding_key) != 64
            or any(char not in "0123456789abcdef" for char in after_binding_key)
        ):
            raise MeetAutomationNotFound("eligible Meet event not found")
        db = await self._get_db()
        scope_id = manual_binding_lookup_key(
            owner_id=owner_id,
            org_id=org_id,
            grant_id=grant_id,
            calendar_id="reconciliation-scope",
            calendar_event_id="eligible-events",
        )
        collection = (
            db.collection("workspace_meet_grant_scopes")
            .document(scope_id)
            .collection("eligible_events")
        )
        ordered = collection.order_by("bindingKey")
        bindings: list[EligibleMeetEventBinding] = []
        seen: set[str] = set()

        async def append_query(query) -> None:
            async for scope_snapshot in query.stream():
                try:
                    binding_id = self._binding_index(
                        scope_snapshot.to_dict() or {}
                    )
                except (KeyError, TypeError, ValueError):
                    raise MeetAutomationNotFound(
                        "eligible Meet event not found"
                    ) from None
                if scope_snapshot.id != binding_id or binding_id in seen:
                    raise MeetAutomationNotFound("eligible Meet event not found")
                canonical_snapshot = await db.collection(
                    "workspace_meet_eligible_events"
                ).document(binding_id).get()
                if not canonical_snapshot.exists:
                    raise MeetAutomationNotFound("eligible Meet event not found")
                try:
                    binding = self._canonical_binding(
                        canonical_snapshot.to_dict() or {}, document_id=binding_id
                    )
                except (KeyError, TypeError, ValueError):
                    raise MeetAutomationNotFound(
                        "eligible Meet event not found"
                    ) from None
                if (
                    binding.owner_id != owner_id
                    or binding.org_id != org_id
                    or binding.grant_id != grant_id
                ):
                    raise MeetAutomationNotFound("eligible Meet event not found")
                seen.add(binding_id)
                bindings.append(binding)

        if after_binding_key is None:
            await append_query(ordered.limit(limit))
        else:
            await append_query(
                ordered.start_after({"bindingKey": after_binding_key}).limit(limit)
            )
            remaining = limit - len(bindings)
            if remaining > 0:
                await append_query(
                    ordered.end_at({"bindingKey": after_binding_key}).limit(
                        remaining
                    )
                )
        return bindings

    async def queue_meet_automation_event(
        self, event: AutomationEventState
    ) -> AutomationEventState:
        if (
            event.status != AutomationEventStatus.QUEUED
            or event.version != 1
            or event.attempt_count != 0
            or event.lease_token is not None
            or event.lease_expires_at is not None
            or event.reason_code is not None
            or event.import_session_id is not None
            or event.import_source_key is not None
            or event.import_source_digest is not None
            or event.import_segment_count != 0
            or event.import_attempt_count != 0
        ):
            raise MeetAutomationConflict("automation event queue state is invalid")
        db = await self._get_db()
        ref = db.collection("workspace_meet_automation_events").document(event.event_key)

        @firestore.async_transactional
        async def queue_in_transaction(transaction):
            snapshot = await ref.get(transaction=transaction)
            if snapshot.exists:
                existing = self._meet_automation_event(snapshot.to_dict() or {})
                if not self._same_automation_identity(existing, event):
                    raise MeetAutomationConflict("automation event identity conflict")
                return existing
            transaction.create(ref, self._meet_automation_event_record(event))
            return event

        return await queue_in_transaction(db.transaction())

    async def claim_meet_automation_event(
        self,
        *,
        event_key: str,
        lease_token: str,
        lease_expires_at: datetime,
        updated_at: datetime,
    ) -> AutomationEventClaim:
        db = await self._get_db()
        ref = db.collection("workspace_meet_automation_events").document(event_key)

        @firestore.async_transactional
        async def claim_in_transaction(transaction):
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                raise MeetAutomationNotFound("automation event not found")
            event = self._meet_automation_event(snapshot.to_dict() or {})
            if event.event_key != event_key:
                raise MeetAutomationConflict("automation event identity conflict")
            if event.status == AutomationEventStatus.COMPLETED:
                return AutomationEventClaim(event=event, idempotent_replay=True)
            if (
                event.status == AutomationEventStatus.LEASED
                and event.lease_expires_at is not None
                and event.lease_expires_at > updated_at
            ):
                raise MeetAutomationConflict("automation event has an active lease")
            claimed = event.model_copy(
                update={
                    "status": AutomationEventStatus.LEASED,
                    "version": event.version + 1,
                    "attempt_count": event.attempt_count + 1,
                    "lease_token": lease_token,
                    "lease_expires_at": lease_expires_at,
                    "reason_code": None,
                    "updated_at": updated_at,
                }
            )
            transaction.set(ref, self._meet_automation_event_record(claimed))
            return AutomationEventClaim(event=claimed)

        return await claim_in_transaction(db.transaction())

    async def complete_meet_automation_event(
        self,
        result,
        *,
        event_key: str,
        lease_token: str,
        version: int,
        updated_at: datetime,
    ) -> AutomationEventState:
        db = await self._get_db()
        ref = db.collection("workspace_meet_automation_events").document(event_key)

        @firestore.async_transactional
        async def complete_in_transaction(transaction):
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                raise MeetAutomationNotFound("automation event not found")
            event = self._meet_automation_event(snapshot.to_dict() or {})
            if (
                event.status != AutomationEventStatus.LEASED
                or event.version != version
                or event.lease_token != lease_token
                or event.lease_expires_at is None
                or event.lease_expires_at <= updated_at
                or result.status != ImportJobStatus.COMPLETED
            ):
                raise MeetAutomationConflict("automation event lease is stale")
            completed = event.model_copy(
                update={
                    "status": AutomationEventStatus.COMPLETED,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "reason_code": None,
                    "import_session_id": result.session_id,
                    "import_source_key": result.source_key,
                    "import_source_digest": result.source_digest,
                    "import_segment_count": result.segment_count,
                    "import_attempt_count": result.attempt_count,
                    "updated_at": updated_at,
                }
            )
            transaction.set(ref, self._meet_automation_event_record(completed))
            return completed

        return await complete_in_transaction(db.transaction())

    async def fail_meet_automation_event(
        self,
        *,
        event_key: str,
        lease_token: str,
        version: int,
        reason_code: AutomationFailureReason,
        updated_at: datetime,
    ) -> AutomationEventState:
        if reason_code not in AUTOMATION_FAILURE_REASONS:
            raise MeetAutomationConflict("automation failure reason is invalid")
        db = await self._get_db()
        ref = db.collection("workspace_meet_automation_events").document(event_key)

        @firestore.async_transactional
        async def fail_in_transaction(transaction):
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                raise MeetAutomationNotFound("automation event not found")
            event = self._meet_automation_event(snapshot.to_dict() or {})
            if (
                event.status != AutomationEventStatus.LEASED
                or event.version != version
                or event.lease_token != lease_token
                or event.lease_expires_at is None
                or event.lease_expires_at <= updated_at
            ):
                raise MeetAutomationConflict("automation event lease is stale")
            failed = event.model_copy(
                update={
                    "status": AutomationEventStatus.FAILED,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "reason_code": reason_code,
                    "updated_at": updated_at,
                }
            )
            transaction.set(ref, self._meet_automation_event_record(failed))
            return failed

        return await fail_in_transaction(db.transaction())

    async def claim_meet_reconciliation(
        self,
        *,
        owner_id: str,
        org_id: str,
        grant_id: str,
        lease_token: str,
        lease_expires_at: datetime,
        updated_at: datetime,
    ) -> ReconciliationLease:
        db = await self._get_db()
        scope_key = manual_binding_lookup_key(
            owner_id=owner_id,
            org_id=org_id,
            grant_id=grant_id,
            calendar_id="reconciliation-lease",
            calendar_event_id="active",
        )
        ref = db.collection("workspace_meet_reconciliation_leases").document(scope_key)

        @firestore.async_transactional
        async def claim_in_transaction(transaction):
            snapshot = await ref.get(transaction=transaction)
            record = snapshot.to_dict() or {}
            if snapshot.exists:
                if set(record) != _RECONCILIATION_LEASE_RECORD_KEYS:
                    raise MeetAutomationNotFound("reconciliation scope not found")
                cursor_binding_key = record["cursorBindingKey"]
                if (
                    record["scopeKey"] != scope_key
                    or record["ownerId"] != owner_id
                    or record["orgId"] != org_id
                    or record["grantId"] != grant_id
                    or (
                        cursor_binding_key is not None
                        and (
                            not isinstance(cursor_binding_key, str)
                            or len(cursor_binding_key) != 64
                            or any(
                                char not in "0123456789abcdef"
                                for char in cursor_binding_key
                            )
                        )
                    )
                ):
                    raise MeetAutomationNotFound("reconciliation scope not found")
                if (
                    record["leaseToken"] is not None
                    and record["leaseExpiresAt"] is not None
                    and record["leaseExpiresAt"] > updated_at
                ):
                    raise MeetAutomationConflict("reconciliation has an active lease")
                if (
                    not isinstance(record["version"], int)
                    or isinstance(record["version"], bool)
                    or record["version"] < 1
                    or not isinstance(record["attemptCount"], int)
                    or isinstance(record["attemptCount"], bool)
                    or record["attemptCount"] < 1
                ):
                    raise MeetAutomationNotFound("reconciliation scope not found")
                version = record["version"] + 1
                attempt_count = record["attemptCount"] + 1
            else:
                version = 1
                attempt_count = 1
                cursor_binding_key = None
            lease = ReconciliationLease(
                scope_key=scope_key,
                owner_id=owner_id,
                org_id=org_id,
                grant_id=grant_id,
                version=version,
                attempt_count=attempt_count,
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
                updated_at=updated_at,
                cursor_binding_key=cursor_binding_key,
            )
            transaction.set(
                ref,
                {
                    "scopeKey": scope_key,
                    "ownerId": owner_id,
                    "orgId": org_id,
                    "grantId": grant_id,
                    "version": version,
                    "attemptCount": attempt_count,
                    "leaseToken": lease_token,
                    "leaseExpiresAt": lease_expires_at,
                    "updatedAt": updated_at,
                    "cursorBindingKey": cursor_binding_key,
                },
            )
            return lease

        return await claim_in_transaction(db.transaction())

    async def release_meet_reconciliation(
        self,
        *,
        scope_key: str,
        lease_token: str,
        version: int,
        updated_at: datetime,
        last_considered_binding_key: str | None,
    ) -> None:
        if last_considered_binding_key is not None and (
            len(last_considered_binding_key) != 64
            or any(
                char not in "0123456789abcdef"
                for char in last_considered_binding_key
            )
        ):
            raise MeetAutomationConflict("reconciliation cursor is invalid")
        db = await self._get_db()
        ref = db.collection("workspace_meet_reconciliation_leases").document(scope_key)

        @firestore.async_transactional
        async def release_in_transaction(transaction):
            snapshot = await ref.get(transaction=transaction)
            record = snapshot.to_dict() or {}
            if (
                not snapshot.exists
                or set(record) != _RECONCILIATION_LEASE_RECORD_KEYS
                or record["scopeKey"] != scope_key
                or record["version"] != version
                or record["leaseToken"] != lease_token
            ):
                raise MeetAutomationConflict("reconciliation lease is stale")
            prior_cursor = record["cursorBindingKey"]
            if prior_cursor is not None and (
                not isinstance(prior_cursor, str)
                or len(prior_cursor) != 64
                or any(char not in "0123456789abcdef" for char in prior_cursor)
            ):
                raise MeetAutomationConflict("reconciliation lease is stale")
            transaction.set(
                ref,
                {
                    **record,
                    "leaseToken": None,
                    "leaseExpiresAt": None,
                    "updatedAt": updated_at,
                    "cursorBindingKey": (
                        last_considered_binding_key
                        if last_considered_binding_key is not None
                        else prior_cursor
                    ),
                },
            )

        await release_in_transaction(db.transaction())

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
