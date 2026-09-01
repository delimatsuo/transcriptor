import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore

from backend import main as backend_main
from backend.config import Settings
from backend.schemas.models import Session, SessionMode, SessionStatus, TranscriptSegment
from backend.sessions.reports import (
    ApproveInterviewReportRequest,
    InterviewReportConflict,
    InterviewReportError,
    ReportStatus,
    UpdateInterviewReportRequest,
    approve_report,
    approved_client_report,
    parse_generated_report,
    report_from_record,
    report_generation_is_stale,
    report_to_record,
    update_report_content,
)
from backend.storage.firestore import FirestoreStorage
from backend.workers.interview_report import (
    ReportGenerationClaim,
    ReportGenerationConflict,
    ReportGenerationJob,
    ReportGenerationStatus,
)


SESSION_ID = "report-session-001"
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def generated_payload():
    return {
        "internal_sections": [
            {
                "id": "leadership",
                "title": "Liderança",
                "body": "Construiu e desenvolveu uma equipe de produto.",
                "rating": 4,
                "evidence": [
                    {"source": "transcript", "evidence_id": "seg-1"},
                ],
            },
            {
                "id": "risks",
                "title": "Riscos e limites",
                "body": "A escala internacional não foi estabelecida.",
                "rating": None,
                "evidence": [
                    {"source": "recruiter_note", "evidence_id": "note-1"},
                ],
            },
        ],
        "client_narrative": {
            "trajectory": (
                "Marina construiu uma trajetória de quinze anos em produto, "
                "liderando equipes de até quarenta pessoas."
            ),
            "assessment": (
                "Minha leitura é positiva, embora eu não tenha conseguido avaliar "
                "a escala internacional; recomendo avançar para conversa com Ana."
            ),
            "trajectory_evidence": [
                {"source": "context", "evidence_id": "resume"},
            ],
            "assessment_evidence": [
                {"source": "transcript", "evidence_id": "seg-1"},
                {"source": "recruiter_note", "evidence_id": "note-1"},
                {"source": "context", "evidence_id": "next_steps"},
            ],
        },
    }


def parsed_report(payload=None):
    return parse_generated_report(
        SESSION_ID,
        json.dumps(payload or generated_payload()),
        transcript_ids={"seg-1"},
        note_ids={"note-1"},
        context_ids={"resume", "next_steps"},
        now=NOW,
    )


def update_request(version=1, **overrides):
    values = {
        "expected_version": version,
        "sections": [
            {"id": "leadership", "body": "Liderança revisada pela recrutadora."},
            {"id": "risks", "body": "Limites revisados pela recrutadora."},
        ],
        "client_narrative": {
            "trajectory": "Marina construiu uma trajetória executiva consistente.",
            "assessment": "Minha leitura é positiva; recomendo avançar com Ana.",
        },
    }
    values.update(overrides)
    return UpdateInterviewReportRequest(**values)


def test_provider_json_is_strict_and_every_artifact_binds_to_durable_sources():
    report = parsed_report()

    assert report.status == ReportStatus.DRAFT
    assert report.version == 1
    assert report.internal_sections[0].rating == 4
    assert report.client_narrative.trajectory.count("\n") == 0
    assert report.approved_at is None

    fenced = "```json\n" + json.dumps(generated_payload()) + "\n```"
    assert parse_generated_report(
        SESSION_ID,
        fenced,
        transcript_ids={"seg-1"},
        note_ids={"note-1"},
        context_ids={"resume", "next_steps"},
        now=NOW,
    ) == report


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["internal_sections"][0]["evidence"][0].update(
            {"evidence_id": "cross-session-seg"}
        ),
        lambda payload: payload["client_narrative"].update(
            {"assessment": "Nota 5 para o perfil."}
        ),
        lambda payload: payload["client_narrative"].update(
            {"assessment": "Recomendado com Ressalvas."}
        ),
        lambda payload: payload["client_narrative"].update(
            {"trajectory": "- item de rubrica"}
        ),
        lambda payload: payload["client_narrative"].update(
            {"assessment": "Segundo a rubrica interna, alcançou nível 5."}
        ),
        lambda payload: payload["client_narrative"].update(
            {"assessment": "Recebeu score elevado."}
        ),
        lambda payload: payload["client_narrative"].update(
            {"assessment": "Recomendo a contratação da candidata."}
        ),
        lambda payload: payload["client_narrative"].update(
            {"assessment": "A contratação é recomendada."}
        ),
    ],
)
def test_invalid_provenance_schema_and_client_leakage_are_rejected(mutation):
    payload = generated_payload()
    mutation(payload)

    with pytest.raises(InterviewReportError):
        parsed_report(payload)


@pytest.mark.parametrize(
    "trajectory",
    [
        "Foi contratada pela Empresa X para liderar a área de produto.",
        "Liderou a contratação de quarenta pessoas ao longo de três anos.",
        "Conduziu uma aquisição aprovada pelo CADE durante sua trajetória.",
    ],
)
def test_valid_career_history_is_not_mistaken_for_a_hire_verdict(trajectory):
    payload = generated_payload()
    payload["client_narrative"]["trajectory"] = trajectory
    assert parsed_report(payload).client_narrative.trajectory == trajectory


def test_versioned_edit_and_approval_state_machine_is_immutable_and_idempotent():
    original = parsed_report()
    updated = update_report_content(original, update_request(), now=NOW)

    assert updated.version == 2
    assert updated.internal_sections[0].body == "Liderança revisada pela recrutadora."
    assert updated.internal_sections[0].rating == 4
    assert updated.internal_sections[0].evidence == original.internal_sections[0].evidence

    with pytest.raises(InterviewReportConflict, match="stale"):
        update_report_content(updated, update_request(version=1))

    approved = approve_report(updated, 2, now=NOW)
    assert approved.status == ReportStatus.APPROVED
    assert approved.approved_version == 2
    assert approve_report(approved, 2) == approved
    with pytest.raises(InterviewReportConflict, match="immutable"):
        update_report_content(approved, update_request(version=2))
    with pytest.raises(InterviewReportConflict, match="does not match"):
        approve_report(approved, 1)

    exported = approved_client_report(approved)
    assert exported.model_dump().keys() == {
        "session_id",
        "version",
        "trajectory",
        "assessment",
        "approved_at",
    }
    with pytest.raises(InterviewReportConflict, match="not approved"):
        approved_client_report(original)


def test_firestore_record_round_trip_preserves_exact_approval_contract():
    approved = approve_report(
        update_report_content(parsed_report(), update_request(), now=NOW),
        2,
        now=NOW,
    )
    assert report_from_record(SESSION_ID, report_to_record(approved)) == approved


def test_generation_lease_requires_a_valid_recent_timestamp():
    assert report_generation_is_stale(
        {"status": "generating", "updatedAt": NOW - timedelta(seconds=76)},
        now=NOW,
    )
    assert not report_generation_is_stale(
        {"status": "queued", "updatedAt": NOW - timedelta(seconds=74)},
        now=NOW,
    )
    assert report_generation_is_stale({"status": "generating"}, now=NOW)


class FakeSnapshot:
    def __init__(self, document):
        self.id = document.id
        self.exists = document.data is not None
        self._data = document.data

    def to_dict(self):
        return copy.deepcopy(self._data or {})


class FakeDocument:
    def __init__(self, document_id):
        self.id = document_id
        self.data = None

    async def get(self, transaction=None):
        return FakeSnapshot(self)

    async def create(self, data):
        if self.data is not None:
            raise AlreadyExists("exists")
        self.data = copy.deepcopy(data)


class FakeCollection:
    def __init__(self):
        self.documents = {}

    def document(self, document_id):
        return self.documents.setdefault(document_id, FakeDocument(document_id))


class FakeSessionDocument:
    def __init__(self, document_id):
        self.id = document_id
        self.data = None
        self.collections = {"reports": FakeCollection()}

    async def get(self, transaction=None):
        return FakeSnapshot(self)

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())


class FakeSessions:
    def __init__(self):
        self.documents = {}

    def document(self, session_id):
        return self.documents.setdefault(session_id, FakeSessionDocument(session_id))


class FakeTransaction:
    def __init__(self):
        self.writes = []

    def set(self, document, data, merge=False):
        next_data = copy.deepcopy(data)
        if merge and document.data:
            next_data = {**copy.deepcopy(document.data), **next_data}
        document.data = next_data
        self.writes.append(document.id)

    def create(self, document, data):
        if document.data is not None:
            raise AlreadyExists("exists")
        document.data = copy.deepcopy(data)
        self.writes.append(document.id)


class FakeDB:
    def __init__(self):
        self.sessions = FakeSessions()
        self.session_tombstones = FakeCollection()
        self.transactions = []

    def collection(self, name):
        if name == "sessions":
            return self.sessions
        if name == "session_tombstones":
            return self.session_tombstones
        raise AssertionError(name)

    def transaction(self):
        transaction = FakeTransaction()
        self.transactions.append(transaction)
        return transaction


def configure_storage(monkeypatch):
    storage = FirestoreStorage(Settings(google_cloud_project="fixture-project"))
    db = FakeDB()

    async def get_db():
        return db

    monkeypatch.setattr(storage, "_get_db", get_db)
    monkeypatch.setattr(firestore, "async_transactional", lambda function: function)
    return storage, db


def test_firestore_transactions_reject_stale_edit_and_approve_edit_race(monkeypatch):
    storage, db = configure_storage(monkeypatch)
    db.sessions.document(SESSION_ID).data = {
        "mode": "interview",
        "status": "completed",
        "ownerId": None,
        "orgId": None,
    }
    first = asyncio.run(storage.save_generated_report(parsed_report()))
    replay = asyncio.run(storage.save_generated_report(parsed_report()))
    assert replay == first

    updated = asyncio.run(storage.update_interview_report(SESSION_ID, update_request()))
    assert updated.version == 2
    with pytest.raises(InterviewReportConflict, match="stale"):
        asyncio.run(storage.update_interview_report(SESSION_ID, update_request()))

    approved = asyncio.run(
        storage.approve_interview_report(
            SESSION_ID,
            ApproveInterviewReportRequest(expected_version=2),
        )
    )
    exact_retry = asyncio.run(
        storage.approve_interview_report(
            SESSION_ID,
            ApproveInterviewReportRequest(expected_version=2),
        )
    )
    assert exact_retry == approved
    with pytest.raises(InterviewReportConflict, match="immutable"):
        asyncio.run(storage.update_interview_report(SESSION_ID, update_request(version=2)))
    stored_approved = copy.deepcopy(
        db.sessions.document(SESSION_ID).collection("reports").document("current").data
    )
    differing_payload = generated_payload()
    differing_payload["internal_sections"][0]["body"] = (
        "A newly generated draft that must not replace reviewed content."
    )
    preserved = asyncio.run(
        storage.save_generated_report(parsed_report(differing_payload))
    )
    assert preserved == approved
    assert (
        db.sessions.document(SESSION_ID).collection("reports").document("current").data
        == stored_approved
    )
    assert report_from_record(
        SESSION_ID,
        db.sessions.document(SESSION_ID).collection("reports").document("current").data,
    ) == approved


def test_report_draft_transaction_cannot_cross_a_deletion_tombstone(monkeypatch):
    storage, db = configure_storage(monkeypatch)
    db.sessions.document(SESSION_ID).data = {
        "mode": "interview",
        "status": "completed",
        "ownerId": None,
        "orgId": None,
    }
    db.session_tombstones.document(SESSION_ID).data = {
        "sessionId": SESSION_ID,
        "ownerId": None,
        "orgId": None,
        "reason": "owner_request",
    }

    with pytest.raises(InterviewReportConflict, match="not found"):
        asyncio.run(storage.save_generated_report(parsed_report()))
    assert (
        db.sessions.document(SESSION_ID).collection("reports").document("current").data
        is None
    )


def test_report_draft_requires_exact_completed_parent_scope(monkeypatch):
    storage, db = configure_storage(monkeypatch)
    parent = db.sessions.document(SESSION_ID)
    with pytest.raises(InterviewReportConflict, match="not found"):
        asyncio.run(storage.save_generated_report(parsed_report()))
    parent.data = {
        "mode": "interview",
        "status": "active",
        "ownerId": None,
        "orgId": None,
    }
    with pytest.raises(InterviewReportConflict, match="not found"):
        asyncio.run(storage.save_generated_report(parsed_report()))
    parent.data["status"] = "completed"
    parent.data["ownerId"] = "different-owner"
    with pytest.raises(InterviewReportConflict, match="not found"):
        asyncio.run(storage.save_generated_report(parsed_report()))


def test_existing_report_scope_must_match_completed_parent(monkeypatch):
    storage, db = configure_storage(monkeypatch)
    parent = db.sessions.document(SESSION_ID)
    parent.data = {
        "mode": "interview",
        "status": "completed",
        "ownerId": None,
        "orgId": None,
    }
    cross_scope = parse_generated_report(
        SESSION_ID,
        json.dumps(generated_payload()),
        transcript_ids={"seg-1"},
        note_ids={"note-1"},
        context_ids={"resume", "next_steps"},
        owner_id="other-owner",
        org_id="other-org",
        now=NOW,
    )
    parent.collection("reports").document("current").data = report_to_record(cross_scope)

    with pytest.raises(InterviewReportConflict, match="not found"):
        asyncio.run(storage.save_generated_report(parsed_report()))


def test_concurrent_edits_retry_on_storage_conflict_and_only_one_cas_wins(monkeypatch):
    class StorageConflict(RuntimeError):
        pass

    class TwoReaderGate:
        def __init__(self):
            self.readers = 0
            self.ready = asyncio.Event()

        async def wait(self):
            self.readers += 1
            if self.readers >= 2:
                self.ready.set()
            await self.ready.wait()

    class ConflictSnapshot:
        def __init__(self, data, version):
            self.exists = data is not None
            self._data = copy.deepcopy(data)
            self.version = version

        def to_dict(self):
            return copy.deepcopy(self._data or {})

    class ConflictDocument:
        def __init__(self, data, gate):
            self.data = copy.deepcopy(data)
            self.version = 1
            self.gate = gate

        async def get(self, transaction=None):
            transaction.reads[self] = self.version
            snapshot = ConflictSnapshot(self.data, self.version)
            await self.gate.wait()
            return snapshot

    class ConflictTransaction:
        def __init__(self):
            self.reads = {}
            self.writes = []

        def set(self, document, data, merge=False):
            self.writes.append((document, copy.deepcopy(data), merge))

        def commit(self):
            if any(document.version != version for document, version in self.reads.items()):
                raise StorageConflict()
            for document, data, _merge in self.writes:
                document.data = data
                document.version += 1

    gate = TwoReaderGate()
    current = ConflictDocument(report_to_record(parsed_report()), gate)

    class ReportsCollection:
        def document(self, document_id):
            assert document_id == "current"
            return current

    class SessionDocument:
        def collection(self, name):
            assert name == "reports"
            return ReportsCollection()

    class SessionsCollection:
        def document(self, session_id):
            assert session_id == SESSION_ID
            return SessionDocument()

    class ConflictDB:
        def collection(self, name):
            assert name == "sessions"
            return SessionsCollection()

        def transaction(self):
            return ConflictTransaction()

    async def get_db():
        return ConflictDB()

    def transactional(function):
        async def run_with_retry(_transaction):
            for _attempt in range(3):
                transaction = ConflictTransaction()
                try:
                    result = await function(transaction)
                    transaction.commit()
                    return result
                except StorageConflict:
                    continue
            raise AssertionError("transaction retry budget exhausted")

        return run_with_retry

    storage = FirestoreStorage(Settings(google_cloud_project="fixture-project"))
    monkeypatch.setattr(storage, "_get_db", get_db)
    monkeypatch.setattr(firestore, "async_transactional", transactional)

    async def run():
        return await asyncio.gather(
            storage.update_interview_report(SESSION_ID, update_request()),
            storage.update_interview_report(SESSION_ID, update_request()),
            return_exceptions=True,
        )

    outcomes = asyncio.run(run())
    assert sum(isinstance(outcome, InterviewReportConflict) for outcome in outcomes) == 1
    winner = next(outcome for outcome in outcomes if not isinstance(outcome, Exception))
    assert winner.version == 2
    assert report_from_record(SESSION_ID, current.data).version == 2


def test_terminal_report_sources_are_immutable_and_queue_is_atomic(monkeypatch):
    storage, db = configure_storage(monkeypatch)
    session_ref = db.sessions.document(SESSION_ID)
    session_ref.data = {"mode": "interview", "status": "active"}

    asyncio.run(storage.save_interview_context(SESSION_ID, "resume", "CV original"))
    source_ref = session_ref.collection("report_sources").document("resume")
    assert source_ref.data["text"] == "CV original"

    approved = approve_report(parsed_report(), 1, now=NOW)
    session_ref.collection("reports").document("current").data = report_to_record(
        approved
    )
    with pytest.raises(InterviewReportConflict, match="immutable"):
        asyncio.run(
            storage.save_interview_context(SESSION_ID, "resume", "CV substituído")
        )
    assert source_ref.data["text"] == "CV original"

    other_session = Session(
        id="report-session-queued",
        mode=SessionMode.INTERVIEW,
        status=SessionStatus.COMPLETED,
        ended_at=NOW,
    )
    asyncio.run(storage.save_session_and_queue_report(other_session))
    queued_ref = db.sessions.document(other_session.id)
    assert queued_ref.data["status"] == "completed"
    assert (
        queued_ref.collection("reports").document("generation").data["status"]
        == "queued"
    )

    deleted_session = Session(
        id="report-session-deleted",
        mode=SessionMode.INTERVIEW,
        status=SessionStatus.COMPLETED,
        ended_at=NOW,
    )
    db.session_tombstones.document(deleted_session.id).data = {
        "sessionId": deleted_session.id,
        "ownerId": None,
        "orgId": None,
    }
    deleted_ref = db.sessions.document(deleted_session.id)
    with pytest.raises(InterviewReportConflict, match="not found"):
        asyncio.run(storage.save_session_and_queue_report(deleted_session))
    assert deleted_ref.data is None
    assert deleted_ref.collection("reports").document("generation").data is None


def session_record():
    return {
        "mode": "interview",
        "title": "Diretoria de Produto",
        "startedAt": NOW,
        "endedAt": NOW,
        "lastActive": NOW,
        "status": "completed",
        "noticeGiven": True,
        "summary": "## Rascunho gerado por IA",
    }


class EmptyManager:
    def get_session(self, _session_id):
        return None

    def get_transcript(self, _session_id):
        return []


class ReadReportStorage:
    def __init__(self, report, reason_code=None):
        self.report = report
        self.reason_code = reason_code
        self.reads = []

    async def get_session_record(self, session_id):
        self.reads.append(("session", session_id))
        return session_record()

    async def get_interview_report(self, session_id):
        self.reads.append(("report", session_id))
        return self.report

    async def get_report_generation_state(self, session_id):
        self.reads.append(("state", session_id))
        return {"status": "failed", "reasonCode": self.reason_code}


def test_restart_safe_report_read_and_approved_only_export_use_no_provider(monkeypatch):
    draft = parsed_report()
    storage = ReadReportStorage(draft)
    monkeypatch.setattr(backend_main, "session_mgr", EmptyManager())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "gemini_client", None)

    response = asyncio.run(backend_main.get_interview_report(SESSION_ID))
    assert response["status"] == "draft"
    assert storage.reads == [("session", SESSION_ID), ("report", SESSION_ID)]

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(backend_main.get_approved_client_report(SESSION_ID))
    assert exc_info.value.status_code == 409

    storage.report = approve_report(draft, 1, now=NOW)
    exported = asyncio.run(backend_main.get_approved_client_report(SESSION_ID))
    assert set(exported) == {
        "session_id",
        "version",
        "trajectory",
        "assessment",
        "approved_at",
    }
    assert "internal_sections" not in exported


def test_persisted_generation_failure_is_visible_without_provider_retry(monkeypatch):
    storage = ReadReportStorage(None)
    monkeypatch.setattr(backend_main, "session_mgr", EmptyManager())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "gemini_client", None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(backend_main.get_interview_report(SESSION_ID))

    assert exc_info.value.status_code == 409
    assert "não será repetida automaticamente" in exc_info.value.detail


def test_oversized_report_failure_explains_the_cost_guard(monkeypatch):
    storage = ReadReportStorage(None, reason_code="report_input_too_large")
    monkeypatch.setattr(backend_main, "session_mgr", EmptyManager())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "gemini_client", None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(backend_main.get_interview_report(SESSION_ID))

    assert exc_info.value.status_code == 409
    assert "excede o limite de geração" in exc_info.value.detail


def test_stale_generation_is_reconciled_to_a_durable_visible_failure(monkeypatch):
    class StaleStorage(ReadReportStorage):
        def __init__(self):
            super().__init__(None)
            self.state = {
                "status": "generating",
                "updatedAt": datetime.now(timezone.utc) - timedelta(seconds=90),
            }
            self.saved = []

        async def get_report_generation_state(self, session_id):
            self.reads.append(("state", session_id))
            return self.state

        async def save_report_generation_state(
            self,
            session_id,
            status,
            reason_code=None,
        ):
            self.saved.append((session_id, status, reason_code))

    storage = StaleStorage()
    monkeypatch.setattr(backend_main, "session_mgr", EmptyManager())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(backend_main.get_interview_report(SESSION_ID))

    assert exc_info.value.status_code == 409
    assert "interrompida" in exc_info.value.detail
    assert storage.saved == [
        (SESSION_ID, "failed", "generation_interrupted")
    ]


def test_report_source_context_is_persisted_before_use(monkeypatch):
    session = Session(
        id=SESSION_ID,
        mode=SessionMode.INTERVIEW,
        status=SessionStatus.ACTIVE,
    )

    class Manager:
        def get_session(self, _session_id):
            return session

    storage = type("Storage", (), {"save_interview_context": AsyncMock()})()
    monkeypatch.setattr(backend_main, "session_mgr", Manager())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "interview_documents", {})

    response = asyncio.run(
        backend_main.set_interview_context(
            SESSION_ID,
            backend_main.SetContextRequest(
                doc_type="next_steps",
                text="Entrevista com Ana",
            ),
        )
    )

    assert response == {"ok": True, "chars": 18}
    storage.save_interview_context.assert_awaited_once_with(
        SESSION_ID,
        "next_steps",
        "Entrevista com Ana",
    )
    assert backend_main.interview_documents[SESSION_ID]["next_steps"] == (
        "Entrevista com Ana"
    )


def transcript_record():
    return {
        "id": "seg-1",
        "text": "Liderei quarenta pessoas.",
        "speaker": "Candidato",
        "startTime": 1.0,
        "endTime": 3.0,
        "confidence": 0.95,
        "sequenceNumber": 1,
    }


class GenerationManager:
    def __init__(self):
        self.session = Session(
            id=SESSION_ID,
            mode=SessionMode.INTERVIEW,
            status=SessionStatus.COMPLETED,
            ended_at=NOW,
        )
        self.segment = TranscriptSegment(
            id="seg-1",
            text="Liderei quarenta pessoas.",
            speaker="Candidato",
            start_time=1.0,
            end_time=3.0,
            sequence_number=1,
            is_final=True,
        )

    def get_session(self, _session_id):
        return self.session

    def get_transcript(self, _session_id):
        return [self.segment]

    def set_summary(self, _session_id, summary):
        self.session.summary = summary


class GenerationStorage:
    def __init__(self):
        self.report = None
        self.state = {
            "status": "queued",
            "version": 0,
            "attemptCount": 0,
            "leaseToken": None,
            "leaseExpiresAt": None,
            "reasonCode": None,
            "ownerId": None,
            "orgId": None,
            "updatedAt": NOW,
        }
        self.states = []

    def _job(self):
        return ReportGenerationJob(
            session_id=SESSION_ID,
            owner_id=self.state.get("ownerId"),
            org_id=self.state.get("orgId"),
            status=self.state["status"],
            version=self.state.get("version", 0),
            attempt_count=self.state.get("attemptCount", 0),
            lease_token=self.state.get("leaseToken"),
            lease_expires_at=self.state.get("leaseExpiresAt"),
            reason_code=self.state.get("reasonCode"),
            updated_at=self.state.get("updatedAt", NOW),
        )

    async def claim_report_generation(
        self,
        _session_id,
        *,
        owner_id,
        org_id,
        lease_token,
        lease_expires_at,
        updated_at,
    ):
        job = self._job()
        if job.status == ReportGenerationStatus.FAILED:
            raise ReportGenerationConflict("previously failed")
        if job.status == ReportGenerationStatus.READY:
            return ReportGenerationClaim(
                job=job, current_report=self.report, idempotent_ready=True
            )
        if (
            job.status == ReportGenerationStatus.GENERATING
            and job.lease_expires_at is not None
            and job.lease_expires_at > updated_at
        ):
            raise ReportGenerationConflict("active lease")
        job = job.model_copy(
            update={
                "status": ReportGenerationStatus.GENERATING,
                "version": job.version + 1,
                "attempt_count": job.attempt_count + 1,
                "lease_token": lease_token,
                "lease_expires_at": lease_expires_at,
                "updated_at": updated_at,
            }
        )
        self.state = {
            "status": job.status.value,
            "version": job.version,
            "attemptCount": job.attempt_count,
            "leaseToken": job.lease_token,
            "leaseExpiresAt": job.lease_expires_at,
            "reasonCode": job.reason_code,
            "ownerId": owner_id,
            "orgId": org_id,
            "updatedAt": updated_at,
        }
        self.states.append(("generating", None))
        return ReportGenerationClaim(job=job, current_report=self.report)

    async def fail_report_generation(
        self,
        _session_id,
        *,
        lease_token,
        version,
        reason_code,
        updated_at,
        **_scope,
    ):
        job = self._job()
        if job.lease_token != lease_token or job.version != version:
            raise ReportGenerationConflict("stale")
        job = job.model_copy(
            update={
                "status": ReportGenerationStatus.FAILED,
                "lease_token": None,
                "lease_expires_at": None,
                "reason_code": reason_code,
                "updated_at": updated_at,
            }
        )
        self.state.update(
            status="failed",
            leaseToken=None,
            leaseExpiresAt=None,
            reasonCode=reason_code,
            updatedAt=updated_at,
        )
        self.states.append(("failed", reason_code))
        return job

    async def complete_report_generation(
        self, report, *, lease_token, version, updated_at
    ):
        job = self._job()
        if job.lease_token != lease_token or job.version != version:
            raise ReportGenerationConflict("stale")
        self.report = self.report or report
        self.state.update(
            status="ready",
            leaseToken=None,
            leaseExpiresAt=None,
            reasonCode=None,
            updatedAt=updated_at,
        )
        self.states.append(("ready", None))
        return self.report

    async def get_session_record(self, _session_id):
        return {**session_record(), "ownerId": None, "orgId": None}

    async def get_session_transcript(self, _session_id):
        return [transcript_record()]

    async def get_session_notes(self, _session_id):
        return [
            {
                "id": "note-1",
                "kind": "concern",
                "text": "",
                "transcriptSegmentId": "seg-1",
                "transcriptOffsetMs": 3000,
                "source": "recruiter",
                "createdAt": NOW,
            }
        ]

    async def get_interview_context(self, _session_id):
        return [
            {"id": "resume", "type": "resume", "text": "CV durável"},
            {
                "id": "next_steps",
                "type": "next_steps",
                "text": "Conversa com Ana",
            },
        ]

    save_session = AsyncMock()
    save_summary = AsyncMock()


def test_compatibility_meeting_summary_uses_rolling_context_and_bounded_tail(monkeypatch):
    class MeetingManager:
        def __init__(self):
            self.session = Session(
                id=SESSION_ID,
                mode=SessionMode.MEETING,
                status=SessionStatus.COMPLETED,
                ended_at=NOW,
            )
            self.segments = [
                TranscriptSegment(
                    id="old",
                    text="old coverage",
                    speaker="Entrevistador",
                    sequence_number=1,
                    is_final=True,
                ),
                TranscriptSegment(
                    id="tail",
                    text="final exchange",
                    speaker="Candidato",
                    sequence_number=2,
                    is_final=True,
                ),
            ] + [
                TranscriptSegment(
                    id=f"segment-{index}",
                    text=f"segment {index}",
                    speaker="Candidato",
                    sequence_number=index,
                    is_final=True,
                )
                for index in range(3, 61)
            ]
            self.recent_args = None

        def get_session(self, _session_id):
            return self.session

        def get_recent_transcript_text(self, _session_id, *, max_segments):
            self.recent_args = max_segments
            return "[Candidato]: final exchange"

        def get_transcript(self, _session_id):
            return self.segments

        def set_summary(self, _session_id, summary):
            self.session.summary = summary

    class MeetingStorage:
        save_session = AsyncMock()
        save_summary = AsyncMock()

    manager = MeetingManager()
    storage = MeetingStorage()
    gemini = type(
        "Gemini",
        (),
        {"generate": AsyncMock(return_value="final summary")},
    )()
    rolling = type(
        "Rolling",
        (),
        {"current_summary": "earlier coverage", "last_summary_seq": 1},
    )()
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "gemini_client", gemini)
    monkeypatch.setattr(backend_main.ws_manager, "broadcast", AsyncMock())
    previous_context = backend_main.context_window
    backend_main.context_windows.clear()
    backend_main.context_windows[SESSION_ID] = rolling

    try:
        asyncio.run(backend_main._generate_final_summary(SESSION_ID))
    finally:
        backend_main.context_windows.clear()
        backend_main.context_window = previous_context

    assert manager.recent_args == 50
    user_message = gemini.generate.await_args.kwargs["user_message"]
    assert "## Rolling Summary\nearlier coverage" in user_message
    assert "## Recent Transcript\n[Candidato]: final exchange" in user_message
    assert storage.save_summary.await_args.kwargs["covering_from"] == 10
    assert storage.save_summary.await_args.kwargs["covering_to"] == 60


def test_final_generation_uses_durable_sources_json_mode_and_persists_failure(monkeypatch):
    manager = GenerationManager()
    storage = GenerationStorage()
    gemini = type("Gemini", (), {"generate": AsyncMock(return_value=json.dumps(generated_payload()))})()
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "gemini_client", gemini)
    monkeypatch.setattr(backend_main.ws_manager, "broadcast", AsyncMock())

    asyncio.run(backend_main._generate_final_summary(SESSION_ID))

    assert storage.report is not None
    assert storage.states == [("generating", None), ("ready", None)]
    assert manager.session.summary.startswith("## Rascunho gerado por IA")
    assert gemini.generate.await_args.kwargs["response_mime_type"] == "application/json"
    assert gemini.generate.await_args.kwargs["response_schema"]["required"] == [
        "internal_sections",
        "client_narrative",
    ]
    assert "[source=context evidence_id=resume]" in gemini.generate.await_args.kwargs[
        "user_message"
    ]

    failed_storage = GenerationStorage()
    failed_gemini = type(
        "Gemini",
        (),
        {"generate": AsyncMock(side_effect=RuntimeError("provider down"))},
    )()
    monkeypatch.setattr(backend_main, "firestore_storage", failed_storage)
    monkeypatch.setattr(backend_main, "gemini_client", failed_gemini)

    asyncio.run(backend_main._generate_final_summary(SESSION_ID))
    asyncio.run(backend_main._generate_final_summary(SESSION_ID))

    assert failed_storage.state["status"] == "failed"
    assert failed_storage.state["reasonCode"] == "provider_or_validation_failure"
    assert failed_gemini.generate.await_count == 1


def test_final_generation_fails_closed_before_oversized_provider_request(monkeypatch):
    manager = GenerationManager()
    storage = GenerationStorage()
    storage.get_interview_context = AsyncMock(
        return_value=[
            {"id": "resume", "type": "resume", "text": "R" * 200},
        ]
    )
    gemini = type("Gemini", (), {"generate": AsyncMock()})()
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "gemini_client", gemini)
    monkeypatch.setattr(
        backend_main,
        "settings",
        Settings(
            google_cloud_project="test-project",
            llm_final_report_max_input_chars=100,
        ),
    )
    monkeypatch.setattr(backend_main.ws_manager, "broadcast", AsyncMock())

    asyncio.run(backend_main._generate_final_summary(SESSION_ID))

    assert storage.states == [
        ("generating", None),
        ("failed", "report_input_too_large"),
    ]
    gemini.generate.assert_not_awaited()


def test_queued_generation_remains_pending_then_accepts_a_slow_success(monkeypatch):
    manager = GenerationManager()
    storage = GenerationStorage()
    storage.state = {
        "status": "queued",
        "updatedAt": datetime.now(timezone.utc),
    }
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()

    async def slow_generate(**_kwargs):
        provider_started.set()
        await release_provider.wait()
        return json.dumps(generated_payload())

    gemini = type("Gemini", (), {"generate": AsyncMock(side_effect=slow_generate)})()
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "gemini_client", gemini)
    monkeypatch.setattr(backend_main.ws_manager, "broadcast", AsyncMock())

    async def run():
        generation = asyncio.create_task(
            backend_main._generate_final_summary(SESSION_ID)
        )
        await provider_started.wait()
        assert storage.state["status"] == "generating"
        release_provider.set()
        await generation

    asyncio.run(run())

    assert storage.report is not None
    assert storage.state["status"] == "ready"
    assert gemini.generate.await_count == 1
