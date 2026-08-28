"""FastAPI application — T.A.R.S. backend server."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from uuid import uuid4

import structlog
import uvicorn
from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from backend.audio.buffer import AudioBuffer
from backend.audio.capture import AudioCapture
from backend.auth import (
    AuthenticationError,
    AuthContext,
    current_auth,
    auth_is_enforced,
    initialize_firebase_admin,
    reset_current_auth,
    reset_auth_enforced,
    set_auth_enforced,
    set_current_auth,
    verify_bearer_token,
    verify_iap_token,
)
from backend.auth_runtime import AuthRuntimeGate, ConnectionLease
from backend.config import (
    CorsSettings,
    Settings,
    get_settings,
    select_cors_allowed_origins,
)
from backend.documents.parser import MAX_FILE_SIZE, DocumentParseError, parse_document
from backend.iap_auth import iap_rejection_reason
from backend.llm.context_window import ContextWindowManager
from backend.llm.gemini import GeminiClient
from backend.llm.interview_prompts import (
    INTERVIEW_SYSTEM_PROMPT,
    INTERVIEW_REPORT_PROMPT,
    INTERVIEW_REPORT_RESPONSE_SCHEMA,
    MAX_ANALYSIS_INPUT_CHARS,
    PRE_INTERVIEW_ANALYSIS_PROMPT,
    bound_analysis_inputs,
    build_interview_user_message,
)
from backend.llm.meeting_prompts import FINAL_SUMMARY_PROMPT
from backend.schemas.models import (
    ActionItem,
    ActiveSpeakerBatch,
    ClockSyncRequest,
    ClockSyncResponse,
    CompanionHealthPayload,
    ConnectionHealth,
    ConnectionStatusPayload,
    CoverageGapPayload,
    CoverageGapSegment,
    ErrorPayload,
    ErrorSeverity,
    HeartbeatRequest,
    ParticipantsList,
    SessionMode,
    SessionStatus,
    SetContextRequest,
    SourceHealthReport,
    SpeakerRelabelBatch,
    SpeakerRelabelUpdate,
    Suggestion,
    SummaryUpdate,
    TranscriptDelta,
    TranscriptSegment,
    WSMessage,
    WSMessageType,
)
from backend.speaker_correlation import SpeakerCorrelator
from backend.startup_credentials import probe_application_default_credentials
from backend.utils.sanitize import sanitize_participant_name
from backend.sessions.manager import SessionManager
from backend.sessions.notes import (
    CreateRecruiterNoteRequest,
    RecruiterNoteConflict,
    RecruiterNoteError,
    deserialize_recruiter_notes,
)
from backend.sessions.review import (
    PersistedReviewError,
    build_recent_interview,
    build_session_review,
    corrupt_recent_interview,
    deserialize_session,
    deserialize_transcript,
)
from backend.sessions.reports import (
    ApproveInterviewReportRequest,
    InterviewReportConflict,
    InterviewReportError,
    UpdateInterviewReportRequest,
    approved_client_report,
    parse_generated_report,
    render_internal_summary,
    report_generation_is_stale,
)
from backend.stt.stream_manager import StreamManager
from backend.storage.firestore import FirestoreStorage
from backend.storage.gcs import GCSStorage
from backend.ws.handler import ws_manager

logger = structlog.get_logger()

# --- Global state ---
settings: Settings | None = None
session_mgr: SessionManager | None = None
firestore_storage: FirestoreStorage | None = None
gcs_storage: GCSStorage | None = None
gemini_client: GeminiClient | None = None
# Kept only as a compatibility seam for direct unit callers that bypass the
# session creation route. Production sessions use the per-session map below.
context_window: ContextWindowManager | None = None
context_windows: dict[str, ContextWindowManager] = {}

# Active audio pipeline per session (lists for dual capture: [system, mic])
audio_captures: dict[str, list[AudioCapture]] = {}
audio_buffers: dict[str, list[AudioBuffer]] = {}
stream_managers: dict[str, list[StreamManager]] = {}
# Per-session secret offered as the second tars-stream WebSocket subprotocol entry.
stream_keys: dict[str, str] = {}
stream_key_owners: dict[str, str] = {}
# Session-scoped StreamManagers for the native companion gateway (session_id ->
# source_label -> StreamManager). Survives individual WebSocket reconnects; only
# _stop_pipeline tears these down.
native_stream_managers: dict[str, dict[str, StreamManager]] = {}
native_sm_lock = asyncio.Lock()
# Merged companion_health view per session. native_stream_endpoint serves
# MULTIPLE concurrent WS connections per session (browser mic sends
# source="microphone"; companion sends source="system_audio"); each
# connection updates ONLY the source(s) it has actually observed a frame/gap/
# stall-transition for, and every broadcast carries this merged two-source
# view — so one connection's connect/disconnect never clobbers the other's
# already-reported health. "connections" is the live WS count for the
# session: physical_capture is "active" while it is >0, "stopped" only once
# the LAST connection closes. Popped whole in _stop_pipeline.
native_session_health: dict[str, dict] = {}
# Windowed per-(session_id, source) duplicate-frame guard: the companion may
# legitimately resend a frame whose send() timed out but actually landed,
# which would otherwise feed one 50ms chunk to STT twice. Nested
# session_id -> source -> last accepted sequence number, mirroring
# native_stream_managers' shape so _stop_pipeline can pop a session's entry
# in one call.
native_frame_last_seq: dict[str, dict[str, int]] = {}
NATIVE_FRAME_DEDUP_WINDOW = 200  # ~10s at 50ms/frame; see get_or_create_sm-adjacent dedup check below
# Native companion stall watchdog: a source that has produced >=1 audio frame
# but none for longer than the timeout (while the companion socket is still
# connected) is flagged device_unavailable in companion_health until its next
# frame recovers it. Read as module globals (not captured into locals) so
# tests can monkeypatch them to drive the loop deterministically.
NATIVE_STALL_CHECK_INTERVAL_SECONDS = 5.0
NATIVE_STALL_TIMEOUT_SECONDS = 10.0
NATIVE_NEVER_PRODUCED_TIMEOUT_SECONDS = 15.0
NEVER_PRODUCED_MESSAGES = {
    "system_audio": "Nenhum frame recebido de Áudio do Sistema em 15 s. Verifique se há áudio em reprodução e se a permissão do companion está ativa.",
    "microphone": "Nenhum frame recebido do Microfone em 15 s. Verifique a permissão e o dispositivo selecionado.",
}
pipeline_tasks: dict[str, list[asyncio.Task]] = {}
session_stop_locks: dict[str, asyncio.Lock] = {}
final_summary_scheduled: set[str] = set()
final_summary_tasks: dict[str, asyncio.Task] = {}
# The auth generation that admitted each process-local session.  Session
# records intentionally do not expose this provider-control value; it fences
# late work after logout while allowing a newer re-authentication to create a
# new session and schedule its own work.
session_auth_generations: dict[str, tuple[str, int]] = {}
# Provider calls are registered before their first await.  The registry is
# deliberately count-oriented: terminal auth can cancel and await every task
# without exposing operation identifiers through readiness or HTTP responses.
active_provider_operations: dict[str, dict[str, object]] = {}
PROVIDER_CANCEL_SETTLE_TIMEOUT_SECONDS = 0.25
# A final child write failure means process memory may be the only copy until
# an owner-approved retry/recovery path runs. Never release that transcript on
# the strength of a later parent-session write alone.
transcript_persistence_failures: set[str] = set()
# Protect the live worker while a terminal deletion is in progress or has
# completed. The durable deletion tombstone remains the cross-process source of
# truth; this fence closes late callback/report races in this process.
session_deletion_fences: set[str] = set()
deleted_sessions: set[str] = set()
# At most one rolling-summary generation per session.  The context-window lock
# serializes provider calls but does not prevent stale tasks from queueing.
rolling_summary_tasks: dict[str, asyncio.Task] = {}
rolling_summary_followups: set[str] = set()
single_source_check_tasks: dict[str, asyncio.Task] = {}
emergency_sessions: set[str] = set()

# Interview context
interview_documents: dict[str, dict[str, str]] = {}  # session_id -> {resume, jd}
interview_final_segment_counts: dict[str, int] = {}  # source-warning cadence
interview_suggestion_counters: dict[str, int] = {}  # candidate final segments
# At most one in-flight suggestion per session.  Suggestions are ephemeral UI
# hints; queueing stale generations only adds provider cost and can surface an
# answer for an older transcript after the interview has moved on.
interview_suggestion_tasks: dict[str, asyncio.Task] = {}
single_source_warned: set[str] = set()  # sessions already warned about single audio source

# Speaker correlation (Chrome extension)
speaker_correlators: dict[str, SpeakerCorrelator] = {}
extension_tokens: dict[str, str] = {}  # session_id -> token
extension_capability_expiry: dict[str, datetime] = {}
ws_tickets: dict[str, tuple[AuthContext, str, datetime]] = {}
stream_tickets: dict[str, tuple[AuthContext, str, datetime]] = {}
stop_capabilities: dict[str, tuple[AuthContext, str, datetime]] = {}
auth_runtime = AuthRuntimeGate()
_clock_sync_timestamps: dict[str, float] = {}  # session_id -> last sync time (rate limit)


def _context_window_for(session_id: str) -> ContextWindowManager | None:
    """Return isolated rolling-summary state for one session."""
    return context_windows.get(session_id) or context_window


def _sync_provider_operation_count() -> None:
    auth_runtime.set_active_provider_operations(len(active_provider_operations))


def _provider_uid_current(uid: str | None) -> bool:
    return uid is None or auth_runtime.is_uid_current(uid)


def _provider_operation_admitted(operation: dict[str, object]) -> bool:
    if auth_runtime.kill_latched:
        return False
    lease = operation.get("lease")
    if isinstance(lease, ConnectionLease):
        if lease.closed:
            return False
        uid = operation.get("uid")
        auth_time = operation.get("auth_time")
        if not isinstance(uid, str) or not isinstance(auth_time, int):
            return False
        if not auth_runtime.is_principal_admissible(uid, auth_time):
            return False
    elif not _provider_uid_current(operation.get("uid")):
        return False
    session_id = operation.get("session_id")
    if isinstance(session_id, str):
        if session_id in session_deletion_fences:
            return False
        session = _local_session(session_id)
        owner = operation.get("uid")
        if session is not None and isinstance(owner, str) and session.owner_id != owner:
            return False
        if (
            settings is not None
            and settings.auth_mode == "iap"
            and session is not None
            and session.owner_id is not None
        ):
            generation = session_auth_generations.get(session_id)
            if (
                generation is None
                or generation[0] != session.owner_id
                or operation.get("auth_time") != generation[1]
            ):
                return False
            if session.status == SessionStatus.INCOMPLETE:
                return False
    return True


def _session_provider_principal(session) -> AuthContext | None:
    """Return the exact auth generation that admitted a session's provider work."""
    owner_id = getattr(session, "owner_id", None)
    if not isinstance(owner_id, str) or not owner_id:
        return None
    generation = session_auth_generations.get(getattr(session, "id", ""))
    if generation is None:
        current = current_auth()
        if (
            settings is not None
            and settings.auth_mode == "iap"
            and current is not None
            and current.uid == owner_id
            and isinstance(current.auth_time, int)
        ):
            generation = (owner_id, current.auth_time)
            session_auth_generations[getattr(session, "id", "")] = generation
    if generation is None:
        if settings is not None and settings.auth_mode == "iap":
            return None
        return AuthContext(
            uid=owner_id,
            email="",
            org_id=getattr(session, "org_id", None) or "",
            auth_time=None,
        )
    return AuthContext(
        uid=generation[0],
        email="",
        org_id=getattr(session, "org_id", None) or "",
        auth_time=generation[1],
    )


def _session_runtime_admitted(session_id: str, *, require_active: bool = True) -> bool:
    """Fence callbacks before and after awaits at the session boundary."""
    if session_id in emergency_sessions or session_id in session_deletion_fences:
        return False
    session = _local_session(session_id)
    if session is None:
        # Legacy direct-call tests and local compatibility callers may not have
        # a session object.  Real authenticated sessions always do.
        return True
    if require_active and getattr(session, "status", SessionStatus.ACTIVE) != SessionStatus.ACTIVE:
        return False
    if settings is not None and settings.auth_mode == "iap" and session.owner_id is not None:
        generation = session_auth_generations.get(session_id)
        if generation is None or generation[0] != session.owner_id:
            return False
        return auth_runtime.is_principal_admissible(generation[0], generation[1])
    return True


def _register_provider_operation(
    *,
    user: AuthContext | None = None,
    session_id: str | None = None,
) -> str | None:
    """Reserve provider work before entering a cancellable provider await."""
    if auth_runtime.kill_latched:
        return None
    task = asyncio.current_task()
    if task is None:
        return None
    uid = user.uid if user is not None else None
    auth_time = user.auth_time if user is not None else None
    if (
        settings is not None
        and settings.auth_mode == "iap"
        and (
            user is None
            or not isinstance(uid, str)
            or not uid
            or type(auth_time) is not int
        )
    ):
        # Every IAP operation is bound to a nonempty uid and exact provider
        # generation; never let an unbound/malformed operation spend provider
        # work without a revocable lease.
        return None
    lease: ConnectionLease | None = None
    if uid is not None and auth_time is not None:
        lease = auth_runtime.register_operation(uid, auth_time)
        if lease is None:
            return None
    operation_id = f"provider-{id(task)}-{len(active_provider_operations) + 1}"
    active_provider_operations[operation_id] = {
        "task": task,
        "uid": uid,
        "auth_time": auth_time,
        "session_id": session_id,
        "lease": lease,
    }
    task.add_done_callback(
        lambda completed, operation_id=operation_id: _provider_task_done(
            operation_id, completed
        )
    )
    _sync_provider_operation_count()
    return operation_id


def _release_provider_operation(operation_id: str | None) -> None:
    if operation_id is None:
        return
    operation = active_provider_operations.pop(operation_id, None)
    if operation is None:
        return
    lease = operation.get("lease")
    if isinstance(lease, ConnectionLease):
        auth_runtime.release_operation(lease)
    _sync_provider_operation_count()


def _provider_task_done(operation_id: str, task: asyncio.Task) -> None:
    """Release operation accounting only once the worker is actually done."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass
    operation = active_provider_operations.get(operation_id)
    if operation is not None and operation.get("task") is task:
        _release_provider_operation(operation_id)


def _register_stt_task(
    task: asyncio.Task,
    *,
    session_id: str | None = None,
) -> str | None:
    """Keep live STT/capture workers in the count-only operation registry.

    Emergency abort is intentionally bounded.  A worker that resists Python
    cancellation must therefore stay visible in readiness until its own task
    actually finishes.  The task identity check makes repeated registration
    (for example, a response worker also seen by pipeline cleanup) harmless.
    """
    if task.done():
        return None
    session = _local_session(session_id) if session_id is not None else None
    uid = getattr(session, "owner_id", None)
    auth_time = None
    generation = None
    if session_id is not None:
        generation = session_auth_generations.get(session_id)
        if generation is not None:
            uid, auth_time = generation
    if settings is not None and settings.auth_mode == "iap":
        valid_generation = (
            session_id is not None
            and session is not None
            and isinstance(uid, str)
            and bool(uid)
            and uid == getattr(session, "owner_id", None)
            and isinstance(generation, tuple)
            and len(generation) == 2
            and generation[0] == uid
            and type(auth_time) is int
            and auth_runtime.is_principal_admissible(uid, auth_time)
            and getattr(session, "status", SessionStatus.ACTIVE)
            == SessionStatus.ACTIVE
        )
        if not valid_generation:
            # This task was created before its session generation was proven.
            # Cancel it before it can enter a provider/STT side effect and do
            # not publish count-only accounting for an invalid worker.
            task.cancel()
            return None
    for operation_id, operation in active_provider_operations.items():
        if operation.get("task") is task:
            return operation_id
    operation_id = f"stt-{id(task)}"
    active_provider_operations[operation_id] = {
        "task": task,
        "uid": uid,
        "auth_time": auth_time,
        "session_id": session_id,
        "lease": None,
        "kind": "stt",
    }
    task.add_done_callback(
        lambda completed, operation_id=operation_id: _provider_task_done(
            operation_id, completed
        )
    )
    _sync_provider_operation_count()
    return operation_id


async def _cancel_tasks_bounded(
    tasks: list[asyncio.Task],
    *,
    timeout: float = PROVIDER_CANCEL_SETTLE_TIMEOUT_SECONDS,
) -> set[asyncio.Task]:
    """Cancel workers without waiting forever or releasing live accounting."""
    pending = {
        task
        for task in tasks
        if isinstance(task, asyncio.Task)
        and task is not asyncio.current_task()
        and not task.done()
    }
    for task in pending:
        task.cancel()
    if pending:
        _done, pending = await asyncio.wait(pending, timeout=max(0.0, timeout))
        for task in _done:
            try:
                task.exception()
            except (asyncio.CancelledError, Exception):
                pass
    return pending


async def _cancel_provider_operations(uid: str | None = None) -> None:
    """Fence, cancel, and fully settle provider calls before terminal return."""
    selected = [
        operation
        for operation in list(active_provider_operations.values())
        if uid is None or operation.get("uid") == uid
    ]
    tasks = [
        task for operation in selected
        if isinstance(task := operation.get("task"), asyncio.Task)
        and task is not asyncio.current_task()
        and not task.done()
    ]
    # A task can be scheduled but not yet have reached its registration line.
    # Include those detached provider workers by their session owner so logout
    # cannot return while a completed-session report is about to start.
    for registry in (final_summary_tasks, rolling_summary_tasks, interview_suggestion_tasks):
        for session_id, task in list(registry.items()):
            session = _local_session(session_id)
            owner = getattr(session, "owner_id", None)
            if (
                (uid is None or owner == uid)
                and task is not asyncio.current_task()
                and not task.done()
                and task not in tasks
            ):
                tasks.append(task)
    # Cancellation-resistant workers remain in active_provider_operations; the
    # task done callback releases their count only when they truly settle.
    await _cancel_tasks_bounded(tasks)
    # A cancellation-resistant task is expected to finish cooperatively.  If
    # a test double only exposes a registry entry, still release its lease once
    # its task is done; never leave readiness accounting stale.
    for operation_id, operation in list(active_provider_operations.items()):
        if operation in selected:
            task = operation.get("task")
            if not isinstance(task, asyncio.Task) or task.done():
                _release_provider_operation(operation_id)
    for registry in (final_summary_tasks, rolling_summary_tasks, interview_suggestion_tasks):
        for session_id, task in list(registry.items()):
            if task.done():
                registry.pop(session_id, None)
                if registry is final_summary_tasks:
                    final_summary_scheduled.discard(session_id)


# Concurrency guard for pre-interview analysis
_analyze_semaphore = asyncio.Semaphore(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize and cleanup."""
    global settings, session_mgr, firestore_storage, gcs_storage, gemini_client, context_window

    app.state.ready = False
    settings = get_settings()
    # ADC readiness is an infrastructure precondition for every hosted mode;
    # IAP changes inbound authentication only and must not skip this gate.
    await probe_application_default_credentials()
    if settings.auth_mode == "firebase":
        initialize_firebase_admin(settings)
    session_mgr = SessionManager(settings)
    firestore_storage = FirestoreStorage(settings)
    gcs_storage = GCSStorage(settings)
    gemini_client = GeminiClient(settings)
    context_window = None
    context_windows.clear()

    # Detect orphaned sessions from previous crash
    orphaned = session_mgr.detect_orphaned_sessions()
    if orphaned:
        logger.warning("orphaned_sessions_found", count=len(orphaned))

    logger.info("server_started", host=settings.fastapi_host, port=settings.fastapi_port)
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False

        # Cleanup: stop all active pipelines
        for session_id in list(pipeline_tasks.keys()):
            await _stop_pipeline(session_id)
        await _cancel_provider_operations()
        context_windows.clear()

        logger.info("server_stopped")


app = FastAPI(title="T.A.R.S.", lifespan=lifespan)
app.state.ready = False


def _log_iap_rejection(
    error: BaseException | str | None,
    *,
    request_settings: Settings | None = None,
) -> None:
    """Emit only the closed, content-free IAP rejection reason code."""
    try:
        active_settings = request_settings or settings
        if active_settings is None or active_settings.auth_mode != "iap":
            return
        logger.warning(
            "iap_auth_rejected",
            reason_code=iap_rejection_reason(error),
        )
    except Exception:
        # Telemetry is strictly best-effort.  Never expose the source error or
        # allow a broken reason mapper/sink to alter fail-closed auth behavior.
        return

@app.middleware("http")
async def authenticate_api_requests(request: Request, call_next):
    """Authenticate every API request before route code can touch data."""
    is_preflight = (
        request.method == "OPTIONS"
        and bool(request.headers.get("origin"))
        and bool(request.headers.get("access-control-request-method"))
    )
    if is_preflight or not request.url.path.startswith("/api/"):
        return await call_next(request)
    request_settings = settings or get_settings()
    if request_settings.auth_mode == "iap":
        operator_control = (
            (request.method, request.url.path)
            in {
                ("GET", "/api/admin/task08/transition-readiness"),
                ("POST", "/api/admin/task08/kill-switch"),
            }
        )
        if request_settings.auth_kill_switch:
            auth_runtime.kill()
        try:
            assertion_values = request.headers.getlist("x-goog-iap-jwt-assertion")
            user = verify_iap_token(assertion_values, request_settings)
            if user.auth_time is None:
                raise AuthenticationError("principal is revoked")
            admitted = auth_runtime.admit_principal(user.uid, user.auth_time)
            # The two count-only operator controls remain reachable after the
            # monotonic global kill latch so an operator can observe readiness
            # and repeat the idempotent kill.  A principal revoked by logout
            # cannot use this exception unless that global latch is already
            # active; ordinary API/business paths never receive it.
            operator_after_kill = (
                operator_control
                and auth_runtime.kill_latched
                and user.email.casefold() in request_settings.task08_operator_email_set
                and auth_runtime.is_principal_current(user.uid, user.auth_time)
            )
            if not admitted and not operator_after_kill:
                raise AuthenticationError("principal is revoked")
        except (AuthenticationError, ValueError) as error:
            _log_iap_rejection(error, request_settings=request_settings)
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
    elif request_settings.auth_bypass:
        user = AuthContext(
            uid="local-recruiter-dev",
            email="recruiter-pilot@example.com",
            org_id=request_settings.auth_org_id,
        )
    else:
        try:
            user = verify_bearer_token(request.headers.get("authorization"), request_settings)
        except (AuthenticationError, ValueError):
            # A short-lived stop capability is the only non-bearer exception. It is
            # scoped to one session and cannot authorize reads or other mutations.
            capability = request.headers.get("x-tars-stop-capability")
            path_parts = request.url.path.rstrip("/").split("/")
            session_id = path_parts[-1] if path_parts[-1] == "stop" and len(path_parts) >= 2 else None
            if session_id == "stop":
                session_id = path_parts[-2]
            if request.url.path.endswith("/stop") and capability and session_id:
                entry = stop_capabilities.get(capability)
                now = datetime.now(timezone.utc)
                if entry and entry[1] == session_id and entry[2] > now:
                    user = entry[0]
                else:
                    user = None
            else:
                user = None
            if user is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

    token = set_current_auth(user)
    enforced_token = set_auth_enforced()
    request.state.auth_user = user
    try:
        return await call_next(request)
    finally:
        reset_current_auth(token)
        reset_auth_enforced(enforced_token)


# Keep CORS outermost so even auth rejection responses carry browser-readable
# CORS headers instead of surfacing as opaque network failures.
_cors_settings = CorsSettings()
_allowed_origins = select_cors_allowed_origins(
    _cors_settings.auth_mode,
    _cors_settings.cors_allowed_origins,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    """Unauthenticated process health; readiness still depends on lifespan."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request, response: Response):
    """Readiness probe checking full service initialization."""
    if getattr(request.app.state, "ready", False):
        return {"status": "ready"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "not_ready"}


@app.get("/api/me")
async def current_user_profile():
    """Authenticated admission preflight; no interview data is returned."""
    user = _principal()
    return {"uid": user.uid, "email": user.email, "org_id": user.org_id}


@app.get("/api/auth/bootstrap")
async def auth_bootstrap():
    """Start provider-managed IAP sign-in at the fixed frontend origin."""
    assert settings
    if settings.auth_mode != "iap":
        raise HTTPException(status_code=404, detail="IAP bootstrap is unavailable")
    return RedirectResponse(settings.auth_iap_frontend_origin, status_code=307)


def _revoke_browser_tickets_for_user(user: AuthContext) -> None:
    for store in (ws_tickets, stream_tickets):
        for token, (ticket_user, _session_id, _expires_at) in list(store.items()):
            if ticket_user.uid == user.uid:
                store.pop(token, None)
    auth_runtime.revoke_tickets(user.uid)


def _revoke_stream_keys_for_user(user: AuthContext) -> None:
    for session_id, owner_uid in list(stream_key_owners.items()):
        if owner_uid == user.uid:
            stream_key_owners.pop(session_id, None)
            stream_keys.pop(session_id, None)
    auth_runtime.revoke_stream_keys(user.uid)


async def _cancel_session_heartbeat(session_id: str) -> None:
    """Cancel a heartbeat even when the normal stop path itself fails."""
    if session_mgr is None:
        return
    tasks = getattr(session_mgr, "_heartbeat_tasks", {})
    task = tasks.pop(session_id, None)
    if task is None:
        return
    if await _cancel_tasks_bounded([task]):
        logger.error("session_heartbeat_cancel_stuck", session_id=session_id)


async def _force_clear_session_runtime(session_id: str) -> None:
    """Best-effort local fence when the normal pipeline stop raises."""
    emergency_sessions.add(session_id)
    tasks = pipeline_tasks.pop(session_id, None) or []
    for task in tasks:
        _register_stt_task(task, session_id=session_id)
    await _cancel_tasks_bounded(tasks)
    async with native_sm_lock:
        stream_keys.pop(session_id, None)
        stream_key_owners.pop(session_id, None)
        auth_runtime.consume_stream_key(session_id)
        native_sms = native_stream_managers.pop(session_id, {})
        native_session_health.pop(session_id, None)
        native_frame_last_seq.pop(session_id, None)
    all_sms = list(native_sms.values()) + list(stream_managers.pop(session_id, []))
    seen_managers: set[int] = set()
    for stream_manager in all_sms:
        if id(stream_manager) in seen_managers:
            continue
        seen_managers.add(id(stream_manager))
        try:
            abort = getattr(stream_manager, "abort_emergency", None)
            if callable(abort):
                await abort()
            else:
                await stream_manager.stop()
        except Exception:
            logger.exception("forced_native_sm_stop_error", session_id=session_id)
    emergency_sessions.discard(session_id)


@app.post("/api/auth/logout", status_code=204)
async def auth_logout() -> Response:
    """Synchronously revoke the signed principal before returning."""
    assert settings
    if settings.auth_mode != "iap":
        return Response(status_code=204)
    user = _principal()
    if user is None or user.auth_time is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    auth_runtime.revoke_principal(user.uid, user.auth_time)
    _revoke_browser_tickets_for_user(user)
    _revoke_stream_keys_for_user(user)

    # Revoking the principal closes browser leases, but it does not by itself
    # terminalize a business session. Do that synchronously before returning so
    # logout cannot strand capture, heartbeat, provider work, or capabilities.
    owned_active = []
    if session_mgr is not None:
        owned_active = [
            session.id
            for session in list(getattr(session_mgr, "_sessions", {}).values())
            if session.status == SessionStatus.ACTIVE and session.owner_id == user.uid
        ]
    for session_id in owned_active:
        try:
            await _stop_pipeline(session_id, emergency=True)
        except Exception:
            logger.exception("logout_pipeline_stop_unproven", session_id=session_id)
            await _force_clear_session_runtime(session_id)
        session = _local_session(session_id)
        if session is not None and session.status == SessionStatus.ACTIVE:
            try:
                await session_mgr.stop_session(
                    session_id,
                    transcription_complete=False,
                )
            except Exception:
                logger.exception("logout_session_stop_error", session_id=session_id)
                session.status = SessionStatus.INCOMPLETE
                session.ended_at = datetime.now(timezone.utc)
                await _cancel_session_heartbeat(session_id)
        await _cleanup_session_context_async(session_id)
        if session is not None and firestore_storage is not None:
            try:
                await firestore_storage.save_session(session)
            except Exception:
                # 204 is intentionally content-free and makes no durability
                # claim. Local admission is already terminal; this log marks
                # the persistence ceiling for an owner-authorized retry path.
                logger.exception(
                    "logout_terminal_session_persistence_unproven",
                    session_id=session_id,
                )
    # Emergency-abort active capture before waiting on unrelated provider
    # cancellation, minimizing the post-logout provider window.
    await _cancel_provider_operations(user.uid)
    return Response(status_code=204)


def _task08_operator() -> AuthContext:
    assert settings
    user = _principal()
    if settings.auth_mode != "iap" or user is None:
        raise HTTPException(status_code=403, detail="Operator authorization required")
    if user.email.casefold() not in settings.task08_operator_email_set:
        raise HTTPException(status_code=403, detail="Operator authorization required")
    return user


def _active_business_session_count() -> int:
    if session_mgr is None:
        return 0
    count_active = getattr(session_mgr, "count_active_sessions", None)
    if callable(count_active):
        return int(count_active())
    sessions = getattr(session_mgr, "_sessions", {})
    return sum(
        1 for session in sessions.values() if session.status == SessionStatus.ACTIVE
    )


def _local_session(session_id: str):
    if session_mgr is None:
        return None
    getter = getattr(session_mgr, "get_session", None)
    if callable(getter):
        return getter(session_id)
    return getattr(session_mgr, "_sessions", {}).get(session_id)


def _active_business_session_ids() -> list[str]:
    if session_mgr is None:
        return []
    sessions = getattr(session_mgr, "_sessions", {})
    return [
        getattr(session, "id", session_id)
        for session_id, session in list(sessions.items())
        if session.status == SessionStatus.ACTIVE
    ]


async def _terminalize_active_sessions_for_kill() -> None:
    """Stop every locally active business session after the kill latch."""
    for session_id in _active_business_session_ids():
        try:
            await _terminalize_incomplete_session(session_id)
        except BaseException:
            # The kill route remains content-free and fail-closed even if a
            # test double or provider persistence boundary raises unexpectedly.
            logger.exception("kill_session_terminalization_unproven", session_id=session_id)
            session = _local_session(session_id)
            if session is not None and session.status == SessionStatus.ACTIVE:
                session.status = SessionStatus.INCOMPLETE
                session.ended_at = datetime.now(timezone.utc)
                await _cancel_session_heartbeat(session_id)
            try:
                await _force_clear_session_runtime(session_id)
            except BaseException:
                logger.exception("kill_session_runtime_cleanup_unproven", session_id=session_id)


def _task08_readiness_counts() -> dict[str, int]:
    if settings is not None and settings.auth_kill_switch:
        auth_runtime.kill()
    _prune_expired_iap_tickets()
    active = _active_business_session_count()
    auth_runtime.set_active_business_sessions(active)
    auth_runtime.set_active_provider_operations(len(active_provider_operations))
    return {
        "active_business_sessions": active,
        "registered_browser_sockets": auth_runtime.live_connection_count,
        "outstanding_browser_tickets": len(ws_tickets) + len(stream_tickets),
        "active_stream_keys": len(stream_keys),
        "active_provider_operations": len(active_provider_operations),
    }


@app.get("/api/admin/task08/transition-readiness")
async def task08_transition_readiness():
    """Return count-only, read-only transition readiness."""
    _task08_operator()
    counts = _task08_readiness_counts()
    return {
        **counts,
        "ready": all(value == 0 for value in counts.values())
        and not auth_runtime.kill_latched,
        "kill_switch_active": auth_runtime.kill_latched,
    }


@app.post("/api/admin/task08/kill-switch")
async def task08_kill_switch():
    """Latch application admission closed before revoking capabilities."""
    _task08_operator()
    # The latch is deliberately first and idempotent.  A concurrent request
    # therefore cannot mint a new capability after this point.
    auth_runtime.kill()
    await _terminalize_active_sessions_for_kill()
    # Active STT pipelines are emergency-aborted before waiting on unrelated
    # provider operations, so no capture work remains while cancellation drains.
    await _cancel_provider_operations()
    ws_tickets.clear()
    stream_tickets.clear()
    stream_keys.clear()
    stream_key_owners.clear()
    counts = _task08_readiness_counts()
    return {**counts, "ready": False, "kill_switch_active": True}


async def _close_ws_at_expiry(
    websocket: WebSocket,
    expires_at: datetime,
    lease: ConnectionLease | None = None,
    *,
    sleep: Callable[[float], Awaitable[object]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Close at absolute expiry or immediately when the runtime lease signals."""
    sleep_fn = sleep or asyncio.sleep
    clock_fn = clock or (lambda: datetime.now(timezone.utc))
    delay = max(0.0, (expires_at - clock_fn()).total_seconds())
    expiry_task = asyncio.create_task(sleep_fn(delay))
    lease_task = asyncio.create_task(lease.event.wait()) if lease is not None else None
    try:
        if lease_task is None:
            await expiry_task
            code, reason = 4001, "auth_expired"
        else:
            done, _pending = await asyncio.wait(
                {expiry_task, lease_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            code, reason = (
                (4003, "auth_revoked") if lease_task in done else (4001, "auth_expired")
            )
        try:
            await websocket.close(code=code, reason=reason)
        except TypeError:
            await websocket.close(code=code)
        except Exception:
            pass
    finally:
        pending_tasks = []
        for task in (expiry_task, lease_task):
            if task is not None and not task.done():
                task.cancel()
                pending_tasks.append(task)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)


def _iap_websocket_user(websocket: WebSocket) -> AuthContext | None:
    """Verify the browser's signed IAP assertion before any socket side effect."""
    # Direct legacy unit callers do not initialize application settings.  Keep
    # those calls on the existing Firebase-compatible path; the live lifespan
    # always sets ``settings`` before serving a request.
    request_settings = settings
    if request_settings is None:
        return None
    if request_settings.auth_mode != "iap":
        return None
    if request_settings.auth_kill_switch:
        auth_runtime.kill()
    headers = websocket.headers
    getlist = getattr(headers, "getlist", None)
    values = getlist("x-goog-iap-jwt-assertion") if callable(getlist) else []
    if not values:
        value = headers.get("x-goog-iap-jwt-assertion")
        values = [value] if value is not None else []
    try:
        user = verify_iap_token(values, request_settings)
    except (AuthenticationError, ValueError) as error:
        _log_iap_rejection(error, request_settings=request_settings)
        return None
    try:
        if user.auth_time is None or not auth_runtime.admit_principal(
            user.uid, user.auth_time
        ):
            _log_iap_rejection("principal is revoked", request_settings=request_settings)
            return None
    except (TypeError, ValueError) as error:
        _log_iap_rejection(error, request_settings=request_settings)
        return None
    return user


def _principal() -> AuthContext | None:
    """Route principal; unlike old direct-call tests, production fails closed."""
    user = current_auth()
    if user is None:
        if not auth_is_enforced():
            # Direct function calls in the legacy unit suite do not traverse
            # ASGI middleware. Real HTTP requests always set this flag first.
            return None
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _assert_session_access(session) -> AuthContext | None:
    user = _principal()
    if user is None:
        return None
    if session is None or session.owner_id != user.uid or session.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return user


def _assert_persisted_session_access(record: dict) -> AuthContext | None:
    """Authorize raw persisted scope before parsing untrusted session fields."""
    user = _principal()
    if user is None:
        return None
    if record.get("ownerId") != user.uid or record.get("orgId") != user.org_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return user


def _assert_report_scope(report, session) -> None:
    """Require report ownership to agree with its authorized parent session."""
    user = current_auth()
    if user is None and not auth_is_enforced():
        return
    if (
        report is None
        or report.owner_id != session.owner_id
        or report.org_id != session.org_id
    ):
        raise HTTPException(status_code=404, detail="Report not found")


def _assert_child_scope(records: list[dict], session) -> None:
    """Reject missing/mismatched durable child scope for owned sessions."""
    if session.owner_id is None or session.org_id is None:
        return
    for record in records:
        if record.get("ownerId") != session.owner_id or record.get("orgId") != session.org_id:
            raise HTTPException(status_code=404, detail="Session not found")


def _mint_capability(
    store: dict[str, tuple[AuthContext, str, datetime]],
    user: AuthContext,
    session_id: str,
    ttl_seconds: int,
) -> str:
    now = datetime.now(timezone.utc)
    # A reconnect must replace its previous unconsumed ticket.  This bounds
    # both in-memory storage and AuthRuntimeGate accounting even when a client
    # repeatedly asks for tickets without opening a socket.
    is_ticket_store = store is ws_tickets or store is stream_tickets
    for old_token, (old_user, old_session_id, expires_at) in list(store.items()):
        if expires_at <= now or (
            old_user.uid == user.uid and old_session_id == session_id
        ):
            store.pop(old_token, None)
            if is_ticket_store:
                auth_runtime.consume_ticket(old_token)
    token = secrets.token_urlsafe(32)
    store[token] = (
        user,
        session_id,
        now + timedelta(seconds=ttl_seconds),
    )
    return token


def _prune_expired_iap_tickets(now: datetime | None = None) -> int:
    """Bound ticket-store growth and keep runtime accounting in sync."""
    current = now or datetime.now(timezone.utc)
    pruned = 0
    for store in (ws_tickets, stream_tickets):
        for token, (_user, _session_id, expires_at) in list(store.items()):
            if expires_at <= current:
                store.pop(token, None)
                auth_runtime.consume_ticket(token)
                pruned += 1
    return pruned


async def _save_report_generation_state(
    session_id: str,
    status: str,
    *,
    reason_code: str | None = None,
    session=None,
) -> None:
    """Persist scope when supported while keeping old fakes source-compatible."""
    assert firestore_storage
    kwargs = {"reason_code": reason_code}
    if session is not None and session.owner_id is not None and session.org_id is not None:
        kwargs.update(owner_id=session.owner_id, org_id=session.org_id)
    await firestore_storage.save_report_generation_state(session_id, status, **kwargs)


async def _save_transcript_segment(session_id: str, segment: TranscriptSegment) -> None:
    """Stamp transcript children when the owning session is available."""
    assert firestore_storage and session_mgr
    session = session_mgr.get_session(session_id)
    kwargs = {}
    if session and session.owner_id is not None and session.org_id is not None:
        kwargs.update(owner_id=session.owner_id, org_id=session.org_id)
    await firestore_storage.save_transcript_segment(session_id, segment, **kwargs)


async def _retry_failed_transcript_persistence(session_id: str) -> bool:
    """Replay final transcript children after a transient durable-write failure.

    The retry is intentionally bounded to the in-memory final segments for
    this session and prefers the storage batch path, making a repeated stop
    both idempotent and cheaper than issuing one write per segment.  The
    failure marker is cleared only after every final segment is accepted.
    """
    if session_id not in transcript_persistence_failures:
        return True
    assert session_mgr and firestore_storage
    session = session_mgr.get_session(session_id)
    get_transcript = getattr(session_mgr, "get_transcript", None)
    if not callable(get_transcript):
        logger.warning(
            "transcript_persistence_retry_unavailable",
            session_id=session_id,
        )
        return False
    segments = [
        segment
        for segment in get_transcript(session_id)
        if segment.is_final
    ]
    kwargs = {}
    if session and session.owner_id is not None and session.org_id is not None:
        kwargs.update(owner_id=session.owner_id, org_id=session.org_id)

    try:
        save_batch = getattr(firestore_storage, "save_transcript_batch", None)
        if callable(save_batch):
            await save_batch(session_id, segments, **kwargs)
        else:
            for segment in segments:
                await _save_transcript_segment(session_id, segment)
    except Exception:
        logger.exception(
            "transcript_persistence_retry_failed",
            session_id=session_id,
            segment_count=len(segments),
        )
        return False

    transcript_persistence_failures.discard(session_id)
    if session is not None:
        session.transcript_durability = "complete"
    logger.info(
        "transcript_persistence_recovered",
        session_id=session_id,
        segment_count=len(segments),
    )
    return True


# --- Audio Pipeline ---

async def _run_single_audio_stream(
    session_id: str,
    device_name: str,
    source_label: str,
    capture_list: list[AudioCapture],
    buffer_list: list[AudioBuffer],
    sm_list: list[StreamManager],
    input_channel: int = 0,
) -> None:
    """Single audio stream: capture → STT → broadcast.

    All audio (including silence) is sent continuously to keep the STT
    stream alive. Google STT V2 voice_activity_timeout handles endpointing.
    """
    assert settings and session_mgr

    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=settings.buffer_max_chunks)
    capture = AudioCapture(
        settings,
        audio_queue,
        device_name=device_name,
        label=source_label,
        input_channel=input_channel,
    )
    buffer = AudioBuffer(settings, audio_queue)

    sm = StreamManager(
        settings=settings,
        on_transcript=lambda seg: _on_transcript(session_id, seg),
        source_label=source_label,
        admission_check=lambda: _session_runtime_admitted(session_id),
        task_tracker=lambda task: _register_stt_task(task, session_id=session_id),
    )

    capture_list.append(capture)
    buffer_list.append(buffer)
    sm_list.append(sm)

    try:
        await capture.start()
        await buffer.start()
        await sm.start()

        async for chunk in buffer.chunks():
            session = session_mgr.get_session(session_id)
            if session is None or session.status != SessionStatus.ACTIVE:
                break

            # Send all audio continuously — silence keeps the STT stream alive
            audio_bytes = buffer.float32_to_int16(chunk)
            await sm.send_audio(audio_bytes)

    except asyncio.CancelledError:
        pass
    except Exception:
        sm.mark_failed("audio_pipeline_error")
        logger.exception(
            "audio_pipeline_error", session_id=session_id, source=source_label,
        )
    finally:
        try:
            # Stop capture first so no callback can enqueue behind the drain.
            await capture.stop()
            if session_id in emergency_sessions:
                abort = getattr(sm, "abort_emergency", None)
                if callable(abort):
                    await abort()
                else:
                    await sm.stop()
            else:
                # A PortAudio callback may already have queued _enqueue onto
                # this event loop. Give that callback one turn before
                # emptying the tail. Emergency termination intentionally
                # skips this graceful-drain branch.
                await asyncio.sleep(0)
                pending_chunks = buffer.drain_pending_chunks()
                if pending_chunks:
                    logger.info(
                        "audio_pipeline_draining_pending_chunks",
                        session_id=session_id,
                        source=source_label,
                        count=len(pending_chunks),
                    )
                for chunk in pending_chunks:
                    await sm.send_audio(buffer.float32_to_int16(chunk))
        finally:
            try:
                if session_id not in emergency_sessions:
                    await sm.stop()
            finally:
                await buffer.stop()


async def _run_audio_pipeline(session_id: str) -> None:
    """Start dual audio capture: system audio (BlackHole) + microphone."""
    assert settings and session_mgr

    captures: list[AudioCapture] = []
    buffers: list[AudioBuffer] = []
    sms: list[StreamManager] = []

    audio_captures[session_id] = captures
    audio_buffers[session_id] = buffers
    stream_managers[session_id] = sms

    try:
        # Run both streams in parallel
        await asyncio.gather(
            _run_single_audio_stream(
                session_id=session_id,
                device_name=settings.blackhole_device_name,
                source_label=settings.stt_speaker_label_other,
                capture_list=captures,
                buffer_list=buffers,
                sm_list=sms,
                input_channel=0,
            ),
            _run_single_audio_stream(
                session_id=session_id,
                device_name=settings.microphone_device_name,
                source_label=settings.stt_speaker_label_self,
                capture_list=captures,
                buffer_list=buffers,
                sm_list=sms,
                input_channel=settings.microphone_input_channel,
            ),
        )
    finally:
        audio_captures.pop(session_id, None)
        audio_buffers.pop(session_id, None)
        stream_managers.pop(session_id, None)


async def _on_transcript(session_id: str, segment: TranscriptSegment) -> None:
    """Handle a new transcript segment."""
    if not _session_runtime_admitted(session_id):
        logger.warning(
            "transcript_callback_after_terminal_ignored",
            session_id=session_id,
        )
        return
    context = _context_window_for(session_id)
    assert session_mgr and firestore_storage and context

    # 1. Store in session manager (in-memory, with original speaker)
    session_mgr.add_transcript_segment(session_id, segment)

    # 2. Correlate speaker (if extension is connected)
    correlator = speaker_correlators.get(session_id)
    if correlator and correlator.healthy and segment.is_final:
        new_speaker, confidence = correlator.correlate(segment)
        if new_speaker and confidence >= correlator.min_confidence:
            segment.speaker_override = new_speaker

    # 3. Broadcast via WebSocket (frontend sees override)
    seq = ws_manager.next_sequence(session_id)
    msg = WSMessage.transcript_delta(session_id, seq, segment)
    await ws_manager.broadcast(session_id, msg)

    # Deletion can fence the session while the broadcast is in flight. Never
    # recreate a child record after that terminal fence wins.
    if not _session_runtime_admitted(session_id):
        return

    # 4. Persist to Firestore (WITH override, after correlation)
    if segment.is_final:
        try:
            if not _session_runtime_admitted(session_id):
                return
            await _save_transcript_segment(session_id, segment)
            if not _session_runtime_admitted(session_id):
                return
        except Exception:
            if not _session_runtime_admitted(session_id):
                return
            transcript_persistence_failures.add(session_id)
            session = session_mgr.get_session(session_id)
            if session is not None:
                session.transcript_durability = "pending"
                session.transcript_failure_count += 1
                try:
                    # Best-effort durable marker. If Firestore is the failing
                    # dependency this may fail too, but a successful parent
                    # write lets a later worker surface the uncertainty.
                    await firestore_storage.save_session(session)
                except Exception:
                    logger.exception(
                        "transcript_durability_marker_save_failed",
                        session_id=session_id,
                    )
            logger.exception("firestore_save_error", session_id=session_id)
            # A final is not complete until its durable write succeeds. Let the
            # stream manager make this failure sticky and suppress the report.
            raise

    # Check if we should generate a rolling summary
    if segment.is_final:
        if session_id in session_deletion_fences:
            return
        count_since_index = getattr(
            session_mgr, "get_transcript_word_count_since_index", None
        )
        if count_since_index is not None:
            word_count = count_since_index(
                session_id, from_index=context.last_summary_seq
            )
        else:
            # Keep direct-call test doubles compatible with the pre-index API.
            word_count = session_mgr.get_transcript_word_count(
                session_id, from_seq=context.last_summary_seq
            )
        if context.should_summarize(word_count):
            _schedule_rolling_summary(session_id)

    # Interview mode: source warnings count all final segments, while paid live
    # suggestions count only candidate responses.
    session = session_mgr.get_session(session_id)
    if (
        session
        and session.mode == SessionMode.INTERVIEW
        and segment.is_final
    ):
        interview_final_segment_counts.setdefault(session_id, 0)
        interview_final_segment_counts[session_id] += 1

        # Detect single audio source after 3 final segments
        if (
            interview_final_segment_counts[session_id] == 3
            and session_id not in single_source_warned
        ):
            _schedule_single_source_check(session_id)

        is_candidate = _is_candidate_final_segment(segment)
        if is_candidate:
            interview_suggestion_counters.setdefault(session_id, 0)
            interview_suggestion_counters[session_id] += 1

        if is_candidate and interview_suggestion_counters[session_id] % 5 == 0:
            _schedule_interview_suggestions(session_id)


def _is_candidate_final_segment(segment: TranscriptSegment) -> bool:
    """Return whether a final segment came from the configured candidate channel."""
    if settings is None or not segment.is_final:
        return False
    # The extension may override the display label with a participant name
    # (for example, "Candidata").  Source labels remain the stable channel
    # identity for spend control.
    return segment.speaker == settings.stt_speaker_label_other


async def _generate_rolling_summary(session_id: str) -> None:
    """Generate a rolling summary from recent transcript."""
    if session_id in session_deletion_fences:
        return
    context = _context_window_for(session_id)
    assert session_mgr and context and gemini_client
    session_for_op = _local_session(session_id)
    owner_context = (
        _session_provider_principal(session_for_op)
        if session_for_op is not None
        else None
    )
    provider_operation_id = _register_provider_operation(
        user=owner_context,
        session_id=session_id,
    )
    if session_for_op is not None and session_for_op.owner_id is not None and provider_operation_id is None:
        return

    try:
        current_seq = len(session_mgr.get_transcript(session_id))
        previous_seq = context.last_summary_seq
        get_batch = getattr(
            session_mgr,
            "get_transcript_batch_since_index",
            None,
        )
        if callable(get_batch):
            summary_overhead = len(
                f"## Previous Summary\n{context.current_summary or '(start of session)'}\n\n"
                "## New Transcript Content\n"
            )
            transcript_text, batch_end = get_batch(
                session_id,
                from_index=previous_seq,
                max_segments=50,
                max_chars=min(
                    context.settings.llm_rolling_context_max_chars,
                    max(
                        1,
                        context.settings.llm_max_input_chars - summary_overhead,
                    ),
                ),
            )
        else:
            # Keep direct-call test doubles compatible with the pre-batch API.
            transcript_text = session_mgr.get_transcript_text_since_index(
                session_id,
                from_index=previous_seq,
                max_segments=50,
            )
            batch_end = current_seq
        if not transcript_text:
            return

        summary = await context.update_summary(transcript_text, batch_end)

        operation = active_provider_operations.get(provider_operation_id or "")
        if session_id in session_deletion_fences or operation is None or not _provider_operation_admitted(operation):
            return

        # A failed provider call intentionally leaves the source watermark
        # unchanged.  Do not broadcast or persist the prior/empty summary as if
        # it covered this transcript; the cooldown will permit a bounded retry.
        if context.last_summary_seq != batch_end:
            return

        # Broadcast summary update
        seq = ws_manager.next_sequence(session_id)
        update = SummaryUpdate(
            text=summary,
            covering_from=previous_seq,
            covering_to=batch_end,
        )
        msg = WSMessage.summary_update(session_id, seq, update)
        await ws_manager.broadcast(session_id, msg)

        # Save to Firestore
        operation = active_provider_operations.get(provider_operation_id or "")
        if operation is None or not _provider_operation_admitted(operation):
            return
        if firestore_storage:
            session = session_mgr.get_session(session_id)
            await firestore_storage.save_summary(
                session_id, summary,
                covering_from=previous_seq,
                covering_to=batch_end,
                owner_id=session.owner_id if session else None,
                org_id=session.org_id if session else None,
            )
            operation = active_provider_operations.get(provider_operation_id or "")
            if operation is None or not _provider_operation_admitted(operation):
                return

        if batch_end < current_seq:
            # The task completion callback starts the next contiguous batch
            # after this task is removed from the in-flight map.
            rolling_summary_followups.add(session_id)

    except Exception:
        logger.exception("rolling_summary_error", session_id=session_id)
    finally:
        _release_provider_operation(provider_operation_id)


def _schedule_rolling_summary(session_id: str) -> None:
    """Start one rolling summary and drop duplicate in-flight work."""
    existing = rolling_summary_tasks.get(session_id)
    if existing is not None:
        if not existing.done():
            return
        rolling_summary_tasks.pop(session_id, None)

    task = asyncio.create_task(_generate_rolling_summary(session_id))
    rolling_summary_tasks[session_id] = task

    def clear_task(completed: asyncio.Task) -> None:
        if rolling_summary_tasks.get(session_id) is completed:
            rolling_summary_tasks.pop(session_id, None)
        if session_id in rolling_summary_followups:
            rolling_summary_followups.discard(session_id)
            _schedule_rolling_summary(session_id)

    task.add_done_callback(clear_task)


async def _check_single_audio_source(session_id: str) -> None:
    """Check if all final segments have the same speaker — warn about single audio source."""
    if session_id in session_deletion_fences:
        return
    assert session_mgr

    segments = session_mgr.get_transcript(session_id)
    final_segments = [s for s in segments if s.is_final]
    if len(final_segments) < 3:
        return

    speakers = {s.speaker for s in final_segments}
    if len(speakers) <= 1:
        if session_id in session_deletion_fences:
            return
        single_source_warned.add(session_id)
        logger.warning("single_audio_source_detected", session_id=session_id, speaker=speakers)
        seq = ws_manager.next_sequence(session_id)
        error = ErrorPayload(
            severity=ErrorSeverity.WARNING,
            message="Apenas um canal de áudio está produzindo transcrição. Verifique se o companion (Áudio do Sistema) está em execução e com permissão concedida.",
            code="single_audio_source",
        )
        msg = WSMessage.error_msg(session_id, seq, error)
        await ws_manager.broadcast(session_id, msg)


def _schedule_single_source_check(session_id: str) -> None:
    """Track the warning task so terminal cleanup can cancel it."""
    existing = single_source_check_tasks.get(session_id)
    if existing is not None:
        if not existing.done():
            return
        single_source_check_tasks.pop(session_id, None)

    task = asyncio.create_task(_check_single_audio_source(session_id))
    single_source_check_tasks[session_id] = task

    def clear_task(completed: asyncio.Task) -> None:
        if single_source_check_tasks.get(session_id) is completed:
            single_source_check_tasks.pop(session_id, None)

    task.add_done_callback(clear_task)


async def _generate_interview_suggestions(session_id: str) -> None:
    """Generate interview follow-up question suggestions."""
    if session_id in session_deletion_fences:
        return
    assert session_mgr and gemini_client
    session_for_op = _local_session(session_id)
    owner_context = (
        _session_provider_principal(session_for_op)
        if session_for_op is not None
        else None
    )
    provider_operation_id = _register_provider_operation(
        user=owner_context,
        session_id=session_id,
    )
    if session_for_op is not None and session_for_op.owner_id is not None and provider_operation_id is None:
        return

    docs = interview_documents.get(session_id, {})

    try:
        recent = session_mgr.get_recent_transcript_text(session_id, max_segments=10)

        user_msg = build_interview_user_message(
            resume_text=docs.get("resume", "") if docs else "",
            jd_text=docs.get("jd", "") if docs else "",
            recent_transcript=recent,
            briefing_text=docs.get("briefing", "") if docs else "",
            candidate_name=docs.get("candidate_name", "") if docs else "",
        )

        response = await gemini_client.generate(
            system_instruction=INTERVIEW_SYSTEM_PROMPT,
            user_message=user_msg,
            temperature=0.4,
            # Suggestions are an in-call UI aid, not a report.  1,024 tokens
            # comfortably covers the requested questions/follow-ups while
            # preventing an unexpectedly verbose response from consuming
            # report-scale output budget on every fifth segment.
            max_output_tokens=min(
                1024,
                getattr(settings, "llm_max_output_tokens", 1024),
            ),
        )

        operation = active_provider_operations.get(provider_operation_id or "")
        if session_id in session_deletion_fences or operation is None or not _provider_operation_admitted(operation):
            return

        if response.strip():
            # Extract numbered questions for backward compat
            questions = []
            for line in response.split("\n"):
                line = line.strip()
                if line and len(line) > 2 and line[0].isdigit() and "." in line[:4]:
                    questions.append(line.split(".", 1)[-1].strip())

            seq = ws_manager.next_sequence(session_id)
            suggestion = Suggestion(
                questions=questions,
                markdown=response.strip(),
                context="Based on candidate's latest response",
            )
            msg = WSMessage.suggestion_msg(session_id, seq, suggestion)
            await ws_manager.broadcast(session_id, msg)
            operation = active_provider_operations.get(provider_operation_id or "")
            if operation is None or not _provider_operation_admitted(operation):
                return

    except Exception:
        logger.exception("interview_suggestion_error", session_id=session_id)
    finally:
        _release_provider_operation(provider_operation_id)


def _schedule_interview_suggestions(session_id: str) -> None:
    """Start one suggestion generation, dropping stale duplicate work."""
    existing = interview_suggestion_tasks.get(session_id)
    if existing is not None:
        if not existing.done():
            return
        interview_suggestion_tasks.pop(session_id, None)

    task = asyncio.create_task(_generate_interview_suggestions(session_id))
    interview_suggestion_tasks[session_id] = task

    def clear_task(completed: asyncio.Task) -> None:
        if interview_suggestion_tasks.get(session_id) is completed:
            interview_suggestion_tasks.pop(session_id, None)

    task.add_done_callback(clear_task)


async def _generate_final_summary(session_id: str) -> None:
    """Generate comprehensive final summary at end of session."""
    if session_id in session_deletion_fences:
        return
    assert session_mgr and gemini_client and firestore_storage
    session = session_mgr.get_session(session_id)
    if session is None:
        _cleanup_session_context(session_id)
        return
    is_interview = session and session.mode == SessionMode.INTERVIEW
    summary_covering_from = 0
    provider_operation_id = _register_provider_operation(
        user=_session_provider_principal(session),
        session_id=session_id,
    )
    if session.owner_id is not None and provider_operation_id is None:
        _cleanup_session_context(session_id)
        return

    def operation_admitted() -> bool:
        operation = active_provider_operations.get(provider_operation_id or "")
        return operation is not None and _provider_operation_admitted(operation)

    async def persist_generation_state(
        state: str,
        *,
        reason_code: str | None = None,
    ) -> bool:
        """Fence report-state writes before and after their awaited boundary."""
        if not operation_admitted():
            return False
        await _save_report_generation_state(
            session_id,
            state,
            reason_code=reason_code,
            session=session,
        )
        return operation_admitted()

    try:
        if is_interview:
            existing = await firestore_storage.get_interview_report(session_id)
            if not operation_admitted():
                return
            generation_state = await firestore_storage.get_report_generation_state(
                session_id
            )
            if not operation_admitted():
                return
            if existing is not None:
                report = existing.model_copy(
                    update={
                        "owner_id": session.owner_id,
                        "org_id": session.org_id,
                    }
                )
                if not await persist_generation_state("ready"):
                    return
            elif generation_state and generation_state.get("status") == "failed":
                logger.warning(
                    "interview_report_generation_previously_failed",
                    session_id=session_id,
                )
                return
            elif generation_state and generation_state.get("status") == "generating":
                if not await persist_generation_state(
                    "failed", reason_code="generation_interrupted"
                ):
                    return
                logger.error(
                    "interview_report_generation_interrupted",
                    session_id=session_id,
                )
                return
            elif generation_state and generation_state.get("status") == "ready":
                if not await persist_generation_state(
                    "failed", reason_code="ready_without_report"
                ):
                    return
                logger.error(
                    "interview_report_ready_without_report",
                    session_id=session_id,
                )
                return
            else:
                if not await persist_generation_state("generating"):
                    return
                transcript_records = await firestore_storage.get_session_transcript(session_id)
                if not operation_admitted():
                    return
                if session.owner_id is not None:
                    for record in transcript_records:
                        if record.get("ownerId") != session.owner_id or record.get("orgId") != session.org_id:
                            raise InterviewReportError("durable transcript scope is invalid")
                transcript = deserialize_transcript(transcript_records)
                if not transcript:
                    raise InterviewReportError(
                        "durable transcript is required for report generation"
                    )
                note_records = await firestore_storage.get_session_notes(session_id)
                if not operation_admitted():
                    return
                if session.owner_id is not None:
                    for record in note_records:
                        if record.get("ownerId") != session.owner_id or record.get("orgId") != session.org_id:
                            raise InterviewReportError("durable note scope is invalid")
                notes = deserialize_recruiter_notes(
                    session_id,
                    note_records,
                )
                context_records = await firestore_storage.get_interview_context(session_id)
                if not operation_admitted():
                    return
                if session.owner_id is not None:
                    for record in context_records:
                        if record.get("ownerId") != session.owner_id or record.get("orgId") != session.org_id:
                            raise InterviewReportError("durable context scope is invalid")
                context: dict[str, str] = {}
                for record in context_records:
                    context_id = record.get("id")
                    if (
                        not isinstance(context_id, str)
                        or record.get("type") != context_id
                        or not isinstance(record.get("text"), str)
                        or not record["text"].strip()
                    ):
                        raise InterviewReportError(
                            "durable report context is invalid"
                        )
                    context[context_id] = record["text"]

                user_parts = ["## Fontes de contexto duráveis"]
                for context_id in sorted(context):
                    user_parts.append(
                        f"[source=context evidence_id={context_id}]\n"
                        f"{context[context_id]}"
                    )
                user_parts.append("## Transcrição final durável")
                for segment in transcript:
                    speaker = segment.speaker_override or segment.speaker
                    user_parts.append(
                        f"[source=transcript evidence_id={segment.id} "
                        f"offset_ms={round(segment.end_time * 1000)} "
                        f"speaker={speaker}]\n{segment.text}"
                    )
                user_parts.append("## Julgamentos da recrutadora")
                if notes:
                    for note in notes:
                        user_parts.append(
                            f"[source=recruiter_note evidence_id={note.id} "
                            f"kind={note.kind.value} "
                            f"transcript_segment_id={note.transcript_segment_id}]"
                        )
                else:
                    user_parts.append("(Nenhuma nota da recrutadora foi registrada.)")

                user_message = "\n\n".join(user_parts)
                max_report_input_chars = getattr(
                    settings,
                    "llm_final_report_max_input_chars",
                    120_000,
                )
                if len(user_message) > max_report_input_chars:
                    logger.warning(
                        "report_input_too_large",
                        session_id=session_id,
                        input_chars=len(user_message),
                        max_chars=max_report_input_chars,
                    )
                    # Do not silently truncate: the report's evidence IDs must
                    # describe the complete durable source set. Fail visibly
                    # before any provider call so an oversized request cannot
                    # create an unbounded model bill.
                    if not await persist_generation_state(
                        "failed", reason_code="report_input_too_large"
                    ):
                        return
                    return

                raw_report = await asyncio.wait_for(
                    gemini_client.generate(
                        system_instruction=INTERVIEW_REPORT_PROMPT,
                        user_message=user_message,
                        temperature=0.2,
                        max_output_tokens=min(
                            8192,
                            getattr(settings, "llm_max_output_tokens", 8192),
                        ),
                        response_mime_type="application/json",
                        response_schema=INTERVIEW_REPORT_RESPONSE_SCHEMA,
                    ),
                    timeout=60,
                )
                operation = active_provider_operations.get(provider_operation_id or "")
                if operation is None or not _provider_operation_admitted(operation):
                    return
                report = parse_generated_report(
                    session_id,
                    raw_report,
                    transcript_ids={segment.id for segment in transcript},
                    note_ids={note.id for note in notes},
                    context_ids=set(context),
                    owner_id=session.owner_id,
                    org_id=session.org_id,
                )
                report = await firestore_storage.save_generated_report(report)
                operation = active_provider_operations.get(provider_operation_id or "")
                if operation is None or not _provider_operation_admitted(operation):
                    return
                if not await persist_generation_state("ready"):
                    return
            summary = render_internal_summary(report)
            transcript = session_mgr.get_transcript(session_id)
        else:
            # Meeting mode is retained for direct/backend compatibility, but
            # its final summary must not rebuild an entire long transcript.
            # The rolling summary carries older coverage; only a bounded tail
            # is needed to capture the final exchange without an unbounded
            # provider request or event-loop allocation.
            transcript_text = session_mgr.get_recent_transcript_text(
                session_id,
                max_segments=50,
            )
            if not transcript_text:
                return
            transcript = session_mgr.get_transcript(session_id)
            prompt = FINAL_SUMMARY_PROMPT
            rolling = _context_window_for(session_id)
            if rolling and rolling.current_summary:
                user_message = (
                    f"## Rolling Summary\n{rolling.current_summary}\n\n"
                    f"## Recent Transcript\n{transcript_text}"
                )
            else:
                user_message = f"## Recent Transcript\n{transcript_text}"
            tail_start = max(0, len(transcript) - 50)
            # Only claim full coverage when the rolling watermark reaches the
            # beginning of the bounded tail.  A stale/empty rolling summary
            # must not make a partial final prompt look like full evidence.
            if not (
                rolling
                and rolling.current_summary
                and getattr(rolling, "last_summary_seq", 0) >= tail_start
            ):
                summary_covering_from = tail_start
            summary = await gemini_client.generate(
                system_instruction=prompt,
                user_message=user_message,
                temperature=0.2,
                max_output_tokens=min(
                    4096,
                    getattr(settings, "llm_max_output_tokens", 4096),
                ),
            )

            operation = active_provider_operations.get(provider_operation_id or "")
            if operation is None or not _provider_operation_admitted(operation):
                return

        operation = active_provider_operations.get(provider_operation_id or "")
        if operation is None or not _provider_operation_admitted(operation):
            return
        session_mgr.set_summary(session_id, summary)

        # Broadcast
        seq = ws_manager.next_sequence(session_id)
        update = SummaryUpdate(
            text=summary,
            is_final=True,
            covering_from=summary_covering_from,
            covering_to=len(transcript),
        )
        msg = WSMessage.summary_update(session_id, seq, update)
        await ws_manager.broadcast(session_id, msg)

        # Save
        operation = active_provider_operations.get(provider_operation_id or "")
        if operation is None or not _provider_operation_admitted(operation):
            return
        session = session_mgr.get_session(session_id)
        if session:
            await firestore_storage.save_session(session)
            operation = active_provider_operations.get(provider_operation_id or "")
            if operation is None or not _provider_operation_admitted(operation):
                return
            await firestore_storage.save_summary(
                session_id, summary,
                covering_from=summary_covering_from,
                covering_to=len(transcript),
                is_final=True,
                owner_id=session.owner_id,
                org_id=session.org_id,
            )
            operation = active_provider_operations.get(provider_operation_id or "")
            if operation is None or not _provider_operation_admitted(operation):
                return

    except Exception:
        logger.exception("final_summary_error", session_id=session_id)
        if is_interview and not (
            settings is not None and settings.auth_mode == "iap"
        ):
            try:
                # Preserve the legacy non-IAP retry marker.  IAP deliberately
                # leaves a durable "generating" marker untouched: a fresh,
                # newly authorized run reconciles interrupted generation.  A
                # cancellation-resistant provider failure must not perform a
                # late state write after terminal logout/kill.
                if operation_admitted():
                    await _save_report_generation_state(
                        session_id,
                        "failed",
                        reason_code="provider_or_validation_failure",
                        session=session,
                    )
            except Exception:
                logger.exception(
                    "report_generation_state_save_error",
                    session_id=session_id,
                )
    finally:
        _release_provider_operation(provider_operation_id)
        _cleanup_session_context(session_id)


def _release_terminal_transcript_memory(session_id: str) -> None:
    """Drop terminal transcript payloads after durable session persistence."""
    if session_mgr is None:
        return
    if session_id in transcript_persistence_failures:
        logger.error(
            "terminal_transcript_memory_retained",
            session_id=session_id,
            reason="child_persistence_failure",
        )
        return
    get_session = getattr(session_mgr, "get_session", None)
    session = get_session(session_id) if callable(get_session) else None
    release = getattr(session_mgr, "release_transcript_memory", None)
    if session is not None and session.status != SessionStatus.ACTIVE and release:
        release(session_id)


def _collect_session_background_tasks(session_id: str) -> list[asyncio.Task]:
    """Detach all cancellable per-session tasks for sync or async cleanup."""
    tasks: list[asyncio.Task] = []
    for registry in (
        rolling_summary_tasks,
        interview_suggestion_tasks,
        single_source_check_tasks,
    ):
        task = registry.pop(session_id, None)
        if task is not None and not task.done() and task not in tasks:
            tasks.append(task)
    return tasks


async def _settle_session_background_tasks(tasks: list[asyncio.Task]) -> None:
    """Cancel detached work with the same bounded, fail-closed policy."""
    await _cancel_tasks_bounded(tasks)


def _cleanup_session_context(
    session_id: str,
    *,
    release_transcript_memory: bool = True,
    preserve_stop_capability: bool = False,
) -> list[asyncio.Task]:
    """Remove sensitive per-interview context after terminal handling."""
    interview_documents.pop(session_id, None)
    interview_final_segment_counts.pop(session_id, None)
    interview_suggestion_counters.pop(session_id, None)
    single_source_warned.discard(session_id)
    context_windows.pop(session_id, None)
    session_auth_generations.pop(session_id, None)
    background_tasks = _collect_session_background_tasks(session_id)
    for task in background_tasks:
        task.cancel()
    rolling_summary_followups.discard(session_id)
    speaker_correlators.pop(session_id, None)
    extension_tokens.pop(session_id, None)
    extension_capability_expiry.pop(session_id, None)
    for token, (_, token_session_id, _) in list(ws_tickets.items()):
        if token_session_id == session_id:
            ws_tickets.pop(token, None)
            auth_runtime.consume_ticket(token)
    for token, (_, token_session_id, _) in list(stream_tickets.items()):
        if token_session_id == session_id:
            stream_tickets.pop(token, None)
            auth_runtime.consume_ticket(token)
    if not preserve_stop_capability:
        _revoke_stop_capabilities(session_id)
    _clock_sync_timestamps.pop(session_id, None)

    # Terminal transcript payloads are already durable by the time this
    # cleanup runs. Release the large in-process cache while retaining the
    # session metadata needed for status/authorization checks. Direct test
    # doubles may not expose this optional memory-release seam.
    if release_transcript_memory:
        _release_terminal_transcript_memory(session_id)
        # The replay ring contains transcript and report payloads too. Keep it
        # while an incomplete stop remains visibly retryable, but release it
        # with all other terminal interview context once durability is known.
        ws_manager.cleanup_session(session_id)
    final_summary_scheduled.discard(session_id)
    return background_tasks


async def _cleanup_session_context_async(
    session_id: str,
    *,
    release_transcript_memory: bool = True,
    preserve_stop_capability: bool = False,
) -> None:
    """Terminal cleanup that waits for every detached session task to settle."""
    tasks = _cleanup_session_context(
        session_id,
        release_transcript_memory=release_transcript_memory,
        preserve_stop_capability=preserve_stop_capability,
    )
    await _settle_session_background_tasks(tasks)


def _revoke_stop_capabilities(session_id: str) -> None:
    """Revoke recovery capabilities after terminal durability succeeds."""
    for token, (_, token_session_id, _) in list(stop_capabilities.items()):
        if token_session_id == session_id:
            stop_capabilities.pop(token, None)


async def _cancel_final_summary_task(session_id: str) -> None:
    """Fence and await a queued final report before deleting its session."""
    task = final_summary_tasks.pop(session_id, None)
    final_summary_scheduled.discard(session_id)
    if task is None or task.done():
        return
    pending = await _cancel_tasks_bounded([task])
    if pending:
        logger.error("final_summary_cancel_stuck", session_id=session_id)


def _schedule_final_summary_once(session_id: str) -> None:
    """Schedule at most one final report for a session in this process."""
    session = _local_session(session_id)
    if auth_runtime.kill_latched:
        return
    if (
        settings is not None
        and settings.auth_mode == "iap"
        and session is not None
        and session.owner_id is not None
    ):
        principal = _session_provider_principal(session)
        if (
            principal is None
            or principal.auth_time is None
            or not auth_runtime.is_principal_admissible(
                principal.uid, principal.auth_time
            )
        ):
            return
    elif (
        session is not None
        and session.owner_id is not None
        and not auth_runtime.is_uid_current(session.owner_id)
    ):
        return
    if (
        session_id in final_summary_scheduled
        or session_id in session_deletion_fences
    ):
        return
    final_summary_scheduled.add(session_id)
    try:
        task = asyncio.create_task(_generate_final_summary(session_id))
        final_summary_tasks[session_id] = task
    except Exception:
        final_summary_scheduled.discard(session_id)
        raise

    def clear_task(completed: asyncio.Task) -> None:
        if final_summary_tasks.get(session_id) is completed:
            final_summary_tasks.pop(session_id, None)
        final_summary_scheduled.discard(session_id)

    task.add_done_callback(clear_task)


async def _stop_pipeline(session_id: str, *, emergency: bool = False) -> bool:
    """Stop the audio pipeline, optionally aborting without a final drain."""
    # Captured before any cancellation/pop below. This is a list of object
    # references, not a snapshot of their state: drain_completed is read from
    # these same StreamManager instances at the very end of this function,
    # after they've been stopped, so capturing the list early is safe. It must
    # happen first because a legacy _run_audio_pipeline task (host capture
    # path), when cancelled just below, pops stream_managers[session_id] out
    # from under a later read as part of its own finally block.
    if emergency:
        # Set the fence before cancelling capture tasks. Their finally blocks
        # must skip the normal pending-buffer flush during kill/logout.
        emergency_sessions.add(session_id)
    managers = list(stream_managers.get(session_id, []))
    tasks = pipeline_tasks.pop(session_id, None)
    if tasks:
        for task in tasks:
            _register_stt_task(task, session_id=session_id)
        # A cancellation-resistant capture worker must not block terminal auth.
        # Its STT operation remains counted until the task's done callback runs.
        await _cancel_tasks_bounded(tasks)

    # Close the native gateway to this session atomically under native_sm_lock:
    # pop the stream key first so both a fresh connection's accept-time guard
    # and get_or_create_sm's in-flight check see the session as gone, then
    # detach the SM registry. Stopping the SMs themselves happens outside the
    # lock so a slow drain doesn't block unrelated sessions' connections.
    async with native_sm_lock:
        stream_keys.pop(session_id, None)
        stream_key_owners.pop(session_id, None)
        auth_runtime.consume_stream_key(session_id)
        native_sms = native_stream_managers.pop(session_id, {})
        native_session_health.pop(session_id, None)
        native_frame_last_seq.pop(session_id, None)
    all_managers = list(native_sms.values()) + managers
    seen_managers: set[int] = set()
    for sm in all_managers:
        if id(sm) in seen_managers:
            continue
        seen_managers.add(id(sm))
        try:
            if emergency:
                abort = getattr(sm, "abort_emergency", None)
                if callable(abort):
                    await abort()
                else:
                    await sm.stop()
            else:
                await sm.stop()
        except Exception:
            logger.exception("native_sm_stop_error", session_id=session_id)

    # Clean up interview runtime state (documents cleaned after final summary)
    interview_final_segment_counts.pop(session_id, None)
    interview_suggestion_counters.pop(session_id, None)
    await _settle_session_background_tasks(
        _collect_session_background_tasks(session_id)
    )
    single_source_warned.discard(session_id)
    if not managers:
        emergency_sessions.discard(session_id)
        if tasks:
            logger.error(
                "audio_pipeline_missing_stream_manager",
                session_id=session_id,
            )
            return False
        return True
    result = all(manager.drain_completed is True for manager in managers)
    emergency_sessions.discard(session_id)
    return result


async def _terminalize_incomplete_session(
    session_id: str,
    *,
    persist: bool = True,
    emergency: bool = True,
) -> None:
    """Fence a session that lost admission while an operation was awaiting.

    This helper is deliberately best-effort at the durable boundary: local
    state is terminalized first, while a failed Firestore write is logged as an
    evidence limitation rather than leaving an ACTIVE in-memory session alive.
    """
    try:
        await _stop_pipeline(session_id, emergency=emergency)
    except Exception:
        logger.exception("incomplete_session_pipeline_stop_error", session_id=session_id)
        await _force_clear_session_runtime(session_id)

    session = _local_session(session_id)
    if session is not None and session.status == SessionStatus.ACTIVE:
        try:
            await session_mgr.stop_session(session_id, transcription_complete=False)
        except Exception:
            logger.exception("incomplete_session_stop_error", session_id=session_id)
            # The local safety invariant is stronger than a persistence error:
            # never leave a business session ACTIVE after admission is lost.
            session.status = SessionStatus.INCOMPLETE
            session.ended_at = datetime.now(timezone.utc)
            await _cancel_session_heartbeat(session_id)

    await _cleanup_session_context_async(session_id)
    if persist and session is not None and firestore_storage is not None:
        try:
            await firestore_storage.save_session(session)
        except Exception:
            logger.exception(
                "incomplete_session_persistence_unproven",
                session_id=session_id,
            )


# --- REST Endpoints ---

@app.post("/api/sessions")
async def create_session(
    mode: str = "meeting",
    title: str = "",
    notice_given: bool = False,
):
    """Create a new session and start the audio pipeline."""
    assert settings and session_mgr and firestore_storage
    user = _principal()
    if settings.auth_mode == "iap" and (
        user is None
        or user.auth_time is None
        or not auth_runtime.admit_principal(user.uid, user.auth_time)
    ):
        raise HTTPException(status_code=401, detail="Authentication required")

    operation_lease: ConnectionLease | None = None
    if user is not None and user.auth_time is not None:
        operation_lease = auth_runtime.register_operation(user.uid, user.auth_time)
        if operation_lease is None:
            raise HTTPException(status_code=401, detail="Authentication required")

    session = None
    try:
        session_mode = SessionMode(mode)
        # Launch Week 4 is interview-only; meeting remains a backend
        # compatibility mode for older direct callers but is not admitted by
        # the web UI.
        session = session_mgr.create_session(
            mode=session_mode,
            title=title,
            owner_id=user.uid if user else None,
            org_id=user.org_id if user else None,
        )
        session.notice_given = notice_given
        if user is not None and user.auth_time is not None:
            session_auth_generations[session.id] = (user.uid, user.auth_time)

        # The operation lease spans this awaited write. A kill/revocation may
        # signal it while Firestore is suspended; no capability is published
        # until the lease is checked again.
        await firestore_storage.save_session(session)
        if operation_lease is not None and (
            operation_lease.closed
            or not auth_runtime.admit_principal(user.uid, user.auth_time)
        ):
            await _terminalize_incomplete_session(session.id)
            raise HTTPException(status_code=401, detail="Authentication required")

        # Per-session secret the native companion must present on the audio
        # gateway WebSocket; without it any client could stream audio into any
        # session_id it guessed.
        stream_key = secrets.token_urlsafe(32)
        stream_keys[session.id] = stream_key
        if user is not None and user.auth_time is not None:
            stream_key_owners[session.id] = user.uid
            if not auth_runtime.register_stream_key(session.id, user.uid):
                await _terminalize_incomplete_session(session.id)
                raise HTTPException(status_code=401, detail="Authentication required")

        # Rolling summaries carry prior model context and counters; never share
        # that state between authenticated sessions or organizations.
        assert gemini_client
        context_windows[session.id] = ContextWindowManager(settings, gemini_client)

        # Mint the stop-only recovery capability before capture starts. Its TTL
        # is configured above the maximum session duration plus the bounded
        # drain.
        stop_capability = (
            _mint_capability(
                stop_capabilities,
                user,
                session.id,
                settings.auth_stop_capability_ttl_seconds,
            )
            if user is not None
            else None
        )
        if operation_lease is not None and operation_lease.closed:
            await _terminalize_incomplete_session(session.id)
            raise HTTPException(status_code=401, detail="Authentication required")

        # Start heartbeat
        await session_mgr.start_heartbeat(session.id)
        if operation_lease is not None and operation_lease.closed:
            await _terminalize_incomplete_session(session.id)
            raise HTTPException(status_code=401, detail="Authentication required")

        # Start audio pipeline only if legacy host audio capture is explicitly
        # enabled.
        if settings.host_audio_capture_enabled:
            task = asyncio.create_task(_run_audio_pipeline(session.id))
            pipeline_tasks[session.id] = [task]
            _register_stt_task(task, session_id=session.id)
        else:
            pipeline_tasks[session.id] = []

        if operation_lease is not None and operation_lease.closed:
            await _terminalize_incomplete_session(session.id)
            raise HTTPException(status_code=401, detail="Authentication required")

        return {
            "session_id": session.id,
            "status": "active",
            "mode": mode,
            "stop_capability": stop_capability,
            "stop_capability_expires_in": settings.auth_stop_capability_ttl_seconds,
            # IAP browsers obtain fresh HTTP stream tickets.  Keep the native
            # key server-side for lifecycle/readiness compatibility, but never
            # expose that deep-link capability to the browser.
            "stream_key": None if settings.auth_mode == "iap" else stream_key,
        }
    except HTTPException:
        raise
    except BaseException:
        if session is not None:
            await _terminalize_incomplete_session(session.id)
        raise
    finally:
        if operation_lease is not None:
            auth_runtime.release_operation(operation_lease)


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    """Stop a session and generate final summary."""
    assert session_mgr and firestore_storage

    stop_lock = session_stop_locks.setdefault(session_id, asyncio.Lock())
    async with stop_lock:
        if (
            session_id in deleted_sessions
            or session_id in session_deletion_fences
        ):
            raise HTTPException(status_code=404, detail="Session not found")
        session = session_mgr.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        _assert_session_access(session)

        newly_stopped = session.status == SessionStatus.ACTIVE
        if newly_stopped:
            drain_completed = await _stop_pipeline(session_id)
            session = await session_mgr.stop_session(
                session_id,
                transcription_complete=drain_completed,
            )
            if session is None:  # It existed immediately before shutdown.
                raise HTTPException(status_code=404, detail="Session not found")

        # A pending child durability marker is stronger than the prior status:
        # never queue a report or release memory while a final segment may be
        # missing from Firestore.
        transcription_complete = (
            session.status == SessionStatus.COMPLETED
            and session_id not in transcript_persistence_failures
        )
        if not transcription_complete:
            # Clear model/UI runtime state now, but retain transcript memory
            # until the terminal session write succeeds below. A final segment
            # may still exist only in memory if its Firestore write failed.
            _cleanup_session_context(
                session_id,
                release_transcript_memory=False,
                preserve_stop_capability=True,
            )

        # A failed final child write is replayed before the parent terminal
        # record. If the provider is still unavailable, retain memory and the
        # stop capability so a later stop retry can recover it.
        await _retry_failed_transcript_persistence(session_id)

        if session_id in transcript_persistence_failures:
            if session.status == SessionStatus.COMPLETED:
                session.status = SessionStatus.INCOMPLETE
            transcription_complete = False
        elif session.status == SessionStatus.COMPLETED:
            # A completed session that needed replay can safely resume its
            # report obligation once every child is durable.
            transcription_complete = True

        # For completed interviews, the durable terminal state and the report
        # obligation are one transaction. A process crash can therefore leave
        # neither or both, but never a completed interview with no expectation.
        if (
            transcription_complete
            and session.mode == SessionMode.INTERVIEW
        ):
            await firestore_storage.save_session_and_queue_report(session)
        else:
            # A terminal retry replays this durable write. This covers a lost
            # HTTP response or a first write that failed after the transition.
            await firestore_storage.save_session(session)

        # Keep the bearer-loss recovery credential usable if the terminal
        # write failed. Revoke it only after durable persistence succeeds.
        _revoke_stop_capabilities(session_id)

        if not transcription_complete:
            _release_terminal_transcript_memory(session_id)

        if transcription_complete:
            _schedule_final_summary_once(session_id)
        elif newly_stopped:
            seq = ws_manager.next_sequence(session_id)
            error = ErrorPayload(
                severity=ErrorSeverity.FATAL,
                message=(
                    "Transcrição incompleta: o STT não confirmou todos os "
                    "resultados finais dentro do limite. Revise o final da "
                    "entrevista; nenhum relatório final foi gerado."
                ),
                code="stt_graceful_drain_incomplete",
            )
            await ws_manager.broadcast(
                session_id,
                WSMessage.error_msg(session_id, seq, error),
            )

        return {
            "session_id": session_id,
            "status": session.status.value,
            "transcription_complete": transcription_complete,
        }


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session everywhere: Firestore, GCS documents, and tombstone."""
    assert firestore_storage and session_mgr
    from backend.storage.deletion import delete_session_everywhere

    lifecycle_lock = session_stop_locks.setdefault(session_id, asyncio.Lock())
    async with lifecycle_lock:
        if session_id in deleted_sessions:
            raise HTTPException(status_code=404, detail="Session not found")

        if session_id in session_deletion_fences:
            # A previous delete attempt fenced the session but failed before
            # writing its tombstone. Reuse the in-memory terminal record for a
            # serialized retry; callbacks remain blocked by the fence.
            session = session_mgr.get_session(session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Session not found")
            _assert_session_access(session)
        else:
            session = await _read_session(session_id)
        _assert_session_access(session)
        if session.status == SessionStatus.ACTIVE:
            # Deletion is terminal-only.  Deleting a live session would leave
            # its capture/STT pipeline running, allow late transcript callbacks
            # to recreate children under a deleted parent, and retain sensitive
            # transcript memory because the session is still active.
            raise HTTPException(
                status_code=409,
                detail="Stop the active session before deleting it",
            )

        # Fence callbacks and await any detached report before the first
        # destructive operation. A failed delete remains fenced so a caller
        # can retry without allowing background work to recreate data.
        session_deletion_fences.add(session_id)
        await _cancel_final_summary_task(session_id)

        db = await firestore_storage._get_db()
        user = current_auth()
        try:
            result = await delete_session_everywhere(
                session_id,
                db,
                gcs_storage,
                owner_id=user.uid if user else None,
                org_id=user.org_id if user else None,
            )
            deleted_sessions.add(session_id)
            transcript_persistence_failures.discard(session_id)
            _cleanup_session_context(session_id)
            return result
        except PermissionError:
            raise HTTPException(status_code=404, detail="Session not found") from None


@app.post("/api/sessions/{session_id}/speakers")
async def update_speakers(session_id: str, speaker_map: dict[str, str]):
    """Update speaker label mapping."""
    assert session_mgr
    _assert_session_access(session_mgr.get_session(session_id))
    session_mgr.update_speaker_map(session_id, speaker_map)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/documents")
async def upload_document(
    session_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
):
    """Upload a resume or JD document for interview mode."""
    assert session_mgr and firestore_storage and gcs_storage
    if doc_type not in {"resume", "jd"}:
        raise HTTPException(status_code=400, detail="doc_type must be resume or jd")
    session = _require_active_interview(session_id)

    # Read only one byte beyond the parser's limit so an oversized upload
    # cannot allocate unbounded memory before validation.
    data = await file.read(MAX_FILE_SIZE + 1)
    filename = file.filename or "document"

    try:
        text = parse_document(data, filename)
    except DocumentParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        await firestore_storage.save_interview_context(session_id, doc_type, text)
    except InterviewReportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    # Upload to GCS
    gcs_path = gcs_storage.upload_bytes(
        data,
        f"sessions/{session.org_id}/{session.owner_id}/{session_id}/documents/{uuid4().hex}",
        content_type=file.content_type or "application/octet-stream",
    )

    # Save metadata to Firestore
    await firestore_storage.save_document_metadata(
        session_id, doc_type, filename, text, gcs_path,
        owner_id=session.owner_id,
        org_id=session.org_id,
    )
    # Store extracted text in memory only after the durable source is accepted.
    if session_id not in interview_documents:
        interview_documents[session_id] = {}
    interview_documents[session_id][doc_type] = text

    return {"ok": True, "extracted_chars": len(text)}


@app.post("/api/sessions/{session_id}/context")
async def set_interview_context(session_id: str, body: SetContextRequest):
    """Set interview context text directly (e.g., pasted job description or briefing)."""
    assert session_mgr and firestore_storage
    allowed_types = {"resume", "jd", "briefing", "candidate_name", "next_steps"}
    if body.doc_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"doc_type must be one of {allowed_types}")
    if len(body.text) > 100_000:
        raise HTTPException(status_code=400, detail="text exceeds 100,000 character limit")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    _require_active_interview(session_id)
    try:
        await firestore_storage.save_interview_context(
            session_id,
            body.doc_type,
            body.text,
        )
    except InterviewReportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if session_id not in interview_documents:
        interview_documents[session_id] = {}
    interview_documents[session_id][body.doc_type] = body.text
    return {"ok": True, "chars": len(body.text)}


@app.post("/api/analyze")
async def analyze_candidate(
    jd_text: str = Form(...),
    file: UploadFile | None = File(None),
):
    """Pre-interview analysis: compare CV against JD and return a briefing."""
    assert gemini_client

    # Validate JD length
    if len(jd_text) > 50_000:
        raise HTTPException(status_code=400, detail="jd_text exceeds 50,000 character limit")

    # Parse CV if provided
    cv_text = ""
    if file is not None:
        # Validate content type
        allowed_types = {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
        }
        content_type = file.content_type or ""
        if content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {content_type}. Use PDF, DOCX, or TXT.",
            )
        data = await file.read(MAX_FILE_SIZE + 1)
        filename = file.filename or "document"
        try:
            cv_text = parse_document(data, filename)
        except DocumentParseError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Bound provider input deterministically. The previous CV-first logic
    # allowed a JD between 30,001 and 50,000 characters to bypass the intended
    # combined cap when no CV budget remained.
    original_input_chars = len(cv_text) + len(jd_text)
    cv_text, analysis_jd_text = bound_analysis_inputs(cv_text, jd_text)
    if len(cv_text) + len(analysis_jd_text) < original_input_chars:
        logger.warning(
            "analyze_input_truncated",
            original_len=original_input_chars,
            bounded_len=len(cv_text) + len(analysis_jd_text),
            max_chars=MAX_ANALYSIS_INPUT_CHARS,
        )

    # Build user message
    parts = []
    if cv_text:
        parts.append(f"## Currículo / CV do Candidato\n{cv_text}")
    else:
        parts.append("## Currículo / CV do Candidato\n(Não fornecido)")
    parts.append(
        f"## Descrição da Vaga / Job Description\n{analysis_jd_text}"
    )
    user_message = "\n\n".join(parts)

    # Call Gemini with timeout and semaphore
    provider_operation_id = _register_provider_operation(user=current_auth())
    if provider_operation_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        async with _analyze_semaphore:
            operation = active_provider_operations.get(provider_operation_id)
            if operation is None or not _provider_operation_admitted(operation):
                raise HTTPException(status_code=401, detail="Authentication required")
            try:
                briefing = await asyncio.wait_for(
                    gemini_client.generate(
                        system_instruction=PRE_INTERVIEW_ANALYSIS_PROMPT,
                        user_message=user_message,
                        temperature=0.3,
                        max_output_tokens=min(
                            2048,
                            getattr(settings, "llm_max_output_tokens", 2048),
                        ),
                    ),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="Analysis timed out")
            except Exception:
                logger.exception("analyze_candidate_error")
                raise HTTPException(status_code=500, detail="Analysis failed")
            operation = active_provider_operations.get(provider_operation_id)
            if operation is None or not _provider_operation_admitted(operation):
                raise HTTPException(status_code=401, detail="Authentication required")
    finally:
        _release_provider_operation(provider_operation_id)

    return {
        "briefing_markdown": briefing,
        "cv_text": cv_text,
        "jd_text": jd_text,
    }


@app.get("/api/sessions")
async def list_sessions():
    """List recent sessions."""
    assert firestore_storage
    user = _principal()
    list_kwargs = {"owner_id": user.uid, "org_id": user.org_id} if user else {}
    sessions = await firestore_storage.list_sessions(**list_kwargs)
    return {"sessions": sessions}


async def _read_session(session_id: str):
    """Read through process memory to Firestore without changing either store."""
    assert session_mgr and firestore_storage
    if session_id in deleted_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    session = session_mgr.get_session(session_id)
    if session is not None:
        _assert_session_access(session)
        return session

    record = await firestore_storage.get_session_record(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _assert_persisted_session_access(record)
    try:
        session = deserialize_session(session_id, record)
        _assert_session_access(session)
        return session
    except PersistedReviewError:
        raise HTTPException(
            status_code=409,
            detail="Persisted session is invalid",
        ) from None


async def _read_transcript(session_id: str, session=None) -> list[TranscriptSegment]:
    """Read transcript memory first, then reconstruct durable final segments."""
    assert session_mgr and firestore_storage
    segments = session_mgr.get_transcript(session_id)
    if segments:
        return segments

    records = await firestore_storage.get_session_transcript(session_id)
    if session is not None:
        _assert_child_scope(records, session)
    try:
        return deserialize_transcript(records)
    except PersistedReviewError:
        raise HTTPException(
            status_code=409,
            detail="Persisted transcript is invalid",
        ) from None


@app.get("/api/sessions/recent-interviews")
async def list_recent_interviews():
    """List durable interview review entries without starting any runtime work."""
    assert firestore_storage
    user = _principal()
    list_kwargs = {"owner_id": user.uid, "org_id": user.org_id} if user else {}
    records = await firestore_storage.list_sessions(**list_kwargs)
    interviews = []
    for record in records:
        if record.get("mode") != SessionMode.INTERVIEW.value:
            continue
        session_id = str(record.get("id", ""))
        try:
            session = deserialize_session(session_id, record)
            interviews.append(build_recent_interview(session))
        except PersistedReviewError:
            interviews.append(corrupt_recent_interview(session_id))
    return {"interviews": [item.model_dump(mode="json") for item in interviews]}


@app.post("/api/sessions/{session_id}/notes")
async def create_recruiter_note(
    session_id: str,
    body: CreateRecruiterNoteRequest,
):
    """Persist one wordless recruiter marker against a final live segment."""
    assert session_mgr and firestore_storage
    session = session_mgr.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Active session not found")
    _assert_session_access(session)
    try:
        durable_note = await firestore_storage.save_recruiter_note(session, body)
    except (RecruiterNoteConflict, RecruiterNoteError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return durable_note.model_dump(mode="json")


@app.get("/api/sessions/{session_id}/notes")
async def get_recruiter_notes(session_id: str):
    """Read persisted recruiter markers without resuming any runtime work."""
    assert firestore_storage
    session = await _read_session(session_id)
    if session.mode != SessionMode.INTERVIEW:
        raise HTTPException(status_code=409, detail="Session is not an interview")
    try:
        note_records = await firestore_storage.get_session_notes(session_id)
        _assert_child_scope(note_records, session)
        notes = deserialize_recruiter_notes(
            session_id,
            note_records,
        )
    except RecruiterNoteError:
        raise HTTPException(
            status_code=409,
            detail="Persisted recruiter notes are invalid",
        ) from None
    return {"notes": [note.model_dump(mode="json") for note in notes]}


async def _read_completed_interview(session_id: str):
    session = await _read_session(session_id)
    if session.mode != SessionMode.INTERVIEW:
        raise HTTPException(status_code=409, detail="Session is not an interview")
    if session.status != SessionStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Interview is not completed")
    return session


def _require_active_interview(session_id: str):
    assert session_mgr
    session = session_mgr.get_session(session_id)
    if (
        session is None
        or session.mode != SessionMode.INTERVIEW
        or session.status != SessionStatus.ACTIVE
    ):
        raise HTTPException(status_code=409, detail="Active interview not found")
    _assert_session_access(session)
    return session


@app.get("/api/sessions/{session_id}/report")
async def get_interview_report(session_id: str):
    """Read a typed report after restart without provider calls or writes."""
    assert firestore_storage
    session = await _read_completed_interview(session_id)
    try:
        report = await firestore_storage.get_interview_report(session_id)
    except InterviewReportError:
        raise HTTPException(status_code=409, detail="Persisted report is invalid") from None
    if report is not None:
        _assert_report_scope(report, session)
        return report.model_dump(mode="json")

    state = await firestore_storage.get_report_generation_state(session_id)
    if state is not None:
        _assert_child_scope([state], session)
    if state and state.get("status") == "failed":
        if state.get("reasonCode") == "report_input_too_large":
            raise HTTPException(
                status_code=409,
                detail=(
                    "O contexto durável da entrevista excede o limite de geração; "
                    "a geração automática foi bloqueada para evitar um pedido "
                    "excessivo ao modelo."
                ),
            )
        raise HTTPException(
            status_code=409,
            detail="A geração do relatório falhou e não será repetida automaticamente.",
        )
    if state and state.get("status") in {"queued", "generating"}:
        if report_generation_is_stale(state):
            await _save_report_generation_state(
                session_id,
                "failed",
                reason_code="generation_interrupted",
                session=session,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "A geração do relatório foi interrompida e não será "
                    "repetida automaticamente."
                ),
            )
        raise HTTPException(status_code=425, detail="Relatório ainda em geração")
    if state and state.get("status") == "ready":
        await _save_report_generation_state(
            session_id,
            "failed",
            reason_code="ready_without_report",
            session=session,
        )
        raise HTTPException(
            status_code=409,
            detail="O estado do relatório é inconsistente e requer revisão.",
        )
    raise HTTPException(status_code=404, detail="Report not found")


@app.put("/api/sessions/{session_id}/report")
async def update_interview_report(
    session_id: str,
    body: UpdateInterviewReportRequest,
):
    """Save reviewed prose with optimistic concurrency control."""
    assert firestore_storage
    session = await _read_completed_interview(session_id)
    try:
        report = await firestore_storage.update_interview_report(
            session_id,
            body,
            owner_id=session.owner_id,
            org_id=session.org_id,
        )
        _assert_report_scope(report, session)
    except (InterviewReportConflict, InterviewReportError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return report.model_dump(mode="json")


@app.post("/api/sessions/{session_id}/report/approve")
async def approve_interview_report(
    session_id: str,
    body: ApproveInterviewReportRequest,
):
    """Explicitly pin the exact reviewed version for client export."""
    assert firestore_storage
    session = await _read_completed_interview(session_id)
    try:
        report = await firestore_storage.approve_interview_report(
            session_id,
            body,
            owner_id=session.owner_id,
            org_id=session.org_id,
        )
        _assert_report_scope(report, session)
    except (InterviewReportConflict, InterviewReportError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return report.model_dump(mode="json")


@app.get("/api/sessions/{session_id}/report/client-export")
async def get_approved_client_report(session_id: str):
    """Expose exactly two client paragraphs, and only after human approval."""
    assert firestore_storage
    session = await _read_completed_interview(session_id)
    try:
        report = await firestore_storage.get_interview_report(session_id)
        _assert_report_scope(report, session)
        if report is None:
            raise InterviewReportConflict("report not found")
        client_report = approved_client_report(report)
    except (InterviewReportConflict, InterviewReportError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return client_report.model_dump(mode="json")


@app.post("/api/sessions/{session_id}/ws-ticket")
async def create_ws_ticket(session_id: str):
    """Mint a single-use ticket for the browser WebSocket handshake."""
    assert settings
    _prune_expired_iap_tickets()
    session = await _read_session(session_id)
    user = _assert_session_access(session)
    ticket = _mint_capability(ws_tickets, user, session_id, settings.auth_ws_ticket_ttl_seconds)
    if user is not None and user.auth_time is not None and not auth_runtime.register_ticket(
        ticket, user.uid, user.auth_time
    ):
        ws_tickets.pop(ticket, None)
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"ticket": ticket, "expires_in": settings.auth_ws_ticket_ttl_seconds}


@app.post("/api/sessions/{session_id}/stream-ticket")
async def create_stream_ticket(session_id: str):
    """Mint a single-use browser audio ticket in IAP mode.

    Firebase/native compatibility continues to use the session stream key;
    only the browser IAP path needs a fresh HTTP ticket on reconnect.
    """
    assert settings
    if settings.auth_mode != "iap":
        raise HTTPException(status_code=404, detail="IAP stream ticket is unavailable")
    _prune_expired_iap_tickets()
    session = await _read_session(session_id)
    user = _assert_session_access(session)
    if user is None or user.auth_time is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    ticket = _mint_capability(
        stream_tickets,
        user,
        session_id,
        settings.auth_ws_ticket_ttl_seconds,
    )
    if not auth_runtime.register_ticket(ticket, user.uid, user.auth_time):
        stream_tickets.pop(ticket, None)
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"ticket": ticket, "expires_in": settings.auth_ws_ticket_ttl_seconds}


@app.post("/api/sessions/{session_id}/stop-capability")
async def create_stop_capability(session_id: str):
    """Mint a bounded stop-only capability for token-loss recovery."""
    assert settings
    session = await _read_session(session_id)
    user = _assert_session_access(session)
    capability = _mint_capability(
        stop_capabilities,
        user,
        session_id,
        settings.auth_stop_capability_ttl_seconds,
    )
    return {"capability": capability, "expires_in": settings.auth_stop_capability_ttl_seconds}


@app.get("/api/sessions/{session_id}/review")
async def get_session_review(session_id: str):
    """Reopen one persisted interview without capture, writes, or model calls."""
    assert firestore_storage
    record = await firestore_storage.get_session_record(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _assert_persisted_session_access(record)
    try:
        session = deserialize_session(session_id, record)
        _assert_session_access(session)
        if session.mode != SessionMode.INTERVIEW:
            raise HTTPException(status_code=409, detail="Session is not an interview")
        transcript_records = await firestore_storage.get_session_transcript(session_id)
        _assert_child_scope(transcript_records, session)
        transcript = deserialize_transcript(transcript_records)
        review = build_session_review(session, transcript)
    except PersistedReviewError:
        raise HTTPException(status_code=409, detail="Persisted review is invalid") from None
    return review.model_dump(mode="json")


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details."""
    session = await _read_session(session_id)
    return session.model_dump()


@app.get("/api/sessions/{session_id}/transcript")
async def get_transcript(session_id: str):
    """Get session transcript."""
    session = await _read_session(session_id)
    segments = await _read_transcript(session_id, session)
    return {"segments": [s.model_dump() for s in segments]}


@app.get("/api/sessions/{session_id}/transcript/download")
async def download_transcript(session_id: str):
    """Download the full transcript as a plain text file."""
    session = await _read_session(session_id)
    segments = await _read_transcript(session_id, session)
    if not segments:
        return PlainTextResponse("No transcript available.", status_code=404)

    lines = []
    for seg in segments:
        if not seg.is_final:
            continue
        ts = f"[{seg.start_time:.1f}s]" if seg.start_time is not None else ""
        speaker = seg.speaker_override or seg.speaker or "?"
        lines.append(f"{ts} {speaker}: {seg.text}")

    content = "\n".join(lines)
    return PlainTextResponse(
        content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="transcript-{session_id}.txt"'},
    )


# --- Chrome Extension Endpoints ---

def _validate_extension_token(session_id: str, capability: str | None) -> None:
    """Validate an owner-bound extension capability, separate from Firebase."""
    if settings is not None and not settings.extension_enabled:
        raise HTTPException(status_code=404, detail="Chrome extension bridge is disabled")
    expected = extension_tokens.get(session_id)
    if not expected:
        raise HTTPException(status_code=403, detail="No extension linked to this session")
    expiry = extension_capability_expiry.get(session_id)
    if expiry is None and not auth_is_enforced():
        # Compatibility for the direct unit helper tests; HTTP always uses the
        # expiring X-TARS-Extension-Capability path below.
        if not capability or not capability.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
        legacy = capability[len("Bearer "):] if capability and capability.startswith("Bearer ") else capability
        if not legacy or not secrets.compare_digest(legacy, expected):
            raise HTTPException(status_code=403, detail="Invalid session token")
        return
    if not capability or not expiry or expiry <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Missing or expired extension capability")
    if not secrets.compare_digest(capability, expected):
        raise HTTPException(status_code=403, detail="Invalid session token")
    session = session_mgr.get_session(session_id) if session_mgr else None
    _assert_session_access(session)


@app.post("/api/sessions/{session_id}/extension-link")
async def extension_link(session_id: str):
    """Link Chrome extension to a session. Returns a session token."""
    assert session_mgr and settings
    if not settings.extension_enabled:
        raise HTTPException(status_code=404, detail="Chrome extension bridge is disabled")
    session = session_mgr.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Session is not active")
    _assert_session_access(session)

    token = secrets.token_urlsafe(32)
    extension_tokens[session_id] = token
    extension_capability_expiry[session_id] = datetime.now(timezone.utc) + timedelta(
        seconds=settings.auth_extension_capability_ttl_seconds
    )

    # Create correlator for this session
    if session_id not in speaker_correlators:
        speaker_correlators[session_id] = SpeakerCorrelator(session_id=session_id)

    logger.info("extension_linked", session_id=session_id)
    return {"capability": token, "session_id": session_id}


@app.post("/api/sessions/{session_id}/clock-sync")
async def clock_sync(
    session_id: str,
    body: ClockSyncRequest,
    extension_capability: str | None = Header(None, alias="X-TARS-Extension-Capability"),
):
    """NTP-style clock synchronization for Chrome extension."""
    assert session_mgr
    _validate_extension_token(session_id, extension_capability)

    session = session_mgr.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Rate limit: max 1 req/5s per session (but allow burst of 5 for initial sync)
    now = time.time()
    last_sync = _clock_sync_timestamps.get(session_id, 0)
    if now - last_sync < 0.5:  # Allow rapid sync during initial calibration
        raise HTTPException(status_code=429, detail="Clock sync rate limited")
    _clock_sync_timestamps[session_id] = now

    server_time = time.time()

    # Sanity check: reject if client/server clocks are wildly off
    if abs(server_time - body.client_send_time) > 30:
        raise HTTPException(
            status_code=400,
            detail="Client/server clock difference exceeds 30s sanity check",
        )

    session_start_wall = session.started_at.timestamp()

    return ClockSyncResponse(
        client_send_time=body.client_send_time,
        server_time=server_time,
        session_start_wall=session_start_wall,
    )


@app.post("/api/sessions/{session_id}/active-speaker")
async def active_speaker(
    session_id: str,
    body: ActiveSpeakerBatch,
    extension_capability: str | None = Header(None, alias="X-TARS-Extension-Capability"),
):
    """Receive active-speaker events from Chrome extension."""
    assert session_mgr and firestore_storage
    _validate_extension_token(session_id, extension_capability)

    session = session_mgr.get_session(session_id)
    if session is None or session.status != SessionStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Session not found or inactive")

    correlator = speaker_correlators.get(session_id)
    if not correlator:
        raise HTTPException(status_code=400, detail="No correlator for session")

    # Sanitize participant names
    sanitized_events = []
    for event in body.events:
        clean_name = sanitize_participant_name(event.participant_name)
        if clean_name:
            event.participant_name = clean_name
            sanitized_events.append(event)

    if not sanitized_events:
        return {"ok": True, "relabeled_count": 0}

    correlator.add_events(sanitized_events)

    # Retroactive relabeling: scan recent unmatched final segments
    segments = session_mgr.get_transcript(session_id)
    relabel_updates = []

    # Check last 20 final segments that don't have an override yet
    final_segments = [s for s in segments if s.is_final and not s.speaker_override]
    for seg in final_segments[-20:]:
        new_speaker, confidence = correlator.correlate(seg)
        if new_speaker and confidence >= correlator.min_confidence:
            seg.speaker_override = new_speaker
            relabel_updates.append(
                SpeakerRelabelUpdate(segment_id=seg.id, new_speaker=new_speaker)
            )

    # Broadcast relabel batch if any
    if relabel_updates:
        batch = SpeakerRelabelBatch(updates=relabel_updates)
        seq = ws_manager.next_sequence(session_id)
        msg = WSMessage.speaker_relabel_batch_msg(session_id, seq, batch)
        await ws_manager.broadcast(session_id, msg)

        # Update Firestore for relabeled segments
        for update in relabel_updates:
            try:
                seg = next(s for s in segments if s.id == update.segment_id)
                await _save_transcript_segment(session_id, seg)
            except (StopIteration, Exception):
                logger.exception("firestore_relabel_error", segment_id=update.segment_id)

    return {"ok": True, "relabeled_count": len(relabel_updates)}


@app.post("/api/sessions/{session_id}/participants")
async def update_participants(
    session_id: str,
    body: ParticipantsList,
    extension_capability: str | None = Header(None, alias="X-TARS-Extension-Capability"),
):
    """Receive participant list from Chrome extension."""
    assert session_mgr
    _validate_extension_token(session_id, extension_capability)

    correlator = speaker_correlators.get(session_id)
    if not correlator:
        raise HTTPException(status_code=400, detail="No correlator for session")

    # Sanitize names
    self_name = ""
    clean_participants = []
    for p in body.participants:
        clean_name = sanitize_participant_name(p.name)
        if clean_name:
            clean_participants.append({"name": clean_name, "isSelf": p.isSelf})
            if p.isSelf:
                self_name = clean_name

    correlator.set_participants(clean_participants, self_name)

    # In interview mode: auto-set candidate_name from the non-self participant
    session = session_mgr.get_session(session_id)
    if session and session.mode == SessionMode.INTERVIEW:
        non_self = [p for p in clean_participants if not p["isSelf"]]
        if non_self and session_id in interview_documents:
            interview_documents[session_id]["candidate_name"] = non_self[0]["name"]

    logger.info(
        "participants_updated",
        session_id=session_id,
        count=len(clean_participants),
        self_name=self_name,
    )
    return {"ok": True, "count": len(clean_participants)}


@app.post("/api/sessions/{session_id}/heartbeat")
async def extension_heartbeat(
    session_id: str,
    body: HeartbeatRequest,
    extension_capability: str | None = Header(None, alias="X-TARS-Extension-Capability"),
):
    """Receive health heartbeat from Chrome extension."""
    _validate_extension_token(session_id, extension_capability)

    correlator = speaker_correlators.get(session_id)
    if not correlator:
        return {"ok": True}

    correlator.update_heartbeat(body.can_detect_speaker)

    # Check if heartbeat was stale — warn frontend
    if not body.can_detect_speaker:
        seq = ws_manager.next_sequence(session_id)
        error = ErrorPayload(
            severity=ErrorSeverity.WARNING,
            message="Extensão Chrome não consegue detectar o speaker ativo. Verifique se a aba do Google Meet está aberta.",
            code="extension_speaker_detection_failed",
        )
        msg = WSMessage.error_msg(session_id, seq, error)
        await ws_manager.broadcast(session_id, msg)

    return {"ok": True}


# --- WebSocket ---

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time transcript and suggestion streaming."""
    # Bind the absolute lifetime to connection admission, not to replay
    # completion. The revocation watcher is started before accept/replay below.
    connection_started = datetime.now(timezone.utc)
    # Browser WebSocket cannot set Authorization headers. It receives a
    # short-lived, single-use ticket from the authenticated HTTP API instead.
    iap_user = _iap_websocket_user(websocket)
    if settings is not None and settings.auth_mode == "iap" and iap_user is None:
        await websocket.close(code=1008)
        return
    offered = [item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",") if item.strip()]
    if len(offered) != 2 or offered[0] != "tars-ticket":
        await websocket.close(code=1008)
        return
    ticket = offered[1]
    _prune_expired_iap_tickets()
    entry = ws_tickets.get(ticket)
    now = datetime.now(timezone.utc)
    if entry is None or entry[1] != session_id or entry[2] <= now:
        await websocket.close(code=1008)
        return
    user = entry[0]
    if iap_user is not None:
        if (
            user.uid != iap_user.uid
            or user.email != iap_user.email
            or user.org_id != iap_user.org_id
            or user.auth_time != iap_user.auth_time
            or user.auth_time is None
        ):
            await websocket.close(code=1008)
            return
        if not auth_runtime.admit_principal(user.uid, user.auth_time):
            await websocket.close(code=1008)
            return

    # Ticket commitment is the side-effect boundary. Every operation after it
    # is covered by one outer finally so read/accept/replay/cancellation errors
    # cannot strand auth context, leases, deadline tasks, or manager state.
    ws_tickets.pop(ticket, None)
    auth_runtime.consume_ticket(ticket)
    token = set_current_auth(user)
    lease: ConnectionLease | None = None
    expiry_task: asyncio.Task | None = None
    socket_expiry: datetime | None = None
    manager_connect_attempted = False
    try:
        # Legacy ticket callers do not need to construct Settings here.  The
        # absolute IAP deadline is only relevant when an IAP principal was
        # committed, and avoiding a settings fallback preserves offline/local
        # WebSocket compatibility when no hosted configuration is present.
        app_settings = settings
        if iap_user is not None:
            app_settings = settings or get_settings()
            lease = auth_runtime.register_connection(user.uid, user.auth_time or 0)
            if lease is None:
                await websocket.close(code=1008)
                return
            socket_expiry = connection_started + timedelta(
                seconds=app_settings.auth_iap_ws_max_lifetime_seconds
            )
            # Start the absolute deadline/revocation watcher before the
            # potentially slow session read. A terminal event during that read
            # must close and fence the socket before replay/accept can begin.
            expiry_task = asyncio.create_task(
                _close_ws_at_expiry(websocket, socket_expiry, lease)
            )

        session = await _read_session(session_id)
        def connection_admitted() -> bool:
            if iap_user is None:
                return True
            live_session = _local_session(session_id) or session
            return bool(
                lease is not None
                and not lease.closed
                and socket_expiry is not None
                and datetime.now(timezone.utc) < socket_expiry
                and auth_runtime.is_principal_admissible(
                    user.uid, user.auth_time or 0
                )
                and live_session.owner_id == user.uid
                and live_session.org_id == user.org_id
                and getattr(live_session, "status", SessionStatus.ACTIVE)
                in (SessionStatus.ACTIVE, SessionStatus.COMPLETED)
            )

        session_status = getattr(session, "status", SessionStatus.ACTIVE)
        admission_current = connection_admitted()
        if (
            session.owner_id != user.uid
            or session.org_id != user.org_id
            or session_status not in (SessionStatus.ACTIVE, SessionStatus.COMPLETED)
            or not admission_current
        ):
            await websocket.close(code=1008)
            return

        # Check for last_seq query param for reconnection
        last_seq = 0
        query_params = websocket.query_params
        if "last_seq" in query_params:
            try:
                last_seq = int(query_params["last_seq"])
            except ValueError:
                pass

        # Repeat every admission check at the replay side-effect boundary.
        admission_current = connection_admitted()
        session_status = getattr(session, "status", SessionStatus.ACTIVE)
        if (
            not admission_current
            or session.owner_id != user.uid
            or session.org_id != user.org_id
            or session_status not in (SessionStatus.ACTIVE, SessionStatus.COMPLETED)
        ):
            await websocket.close(code=1008)
            return
        manager_connect_attempted = True
        await ws_manager.connect(
            websocket,
            session_id,
            last_seq=last_seq,
            subprotocol="tars-ticket",
            admission_check=connection_admitted,
        )
        if not connection_admitted():
            await websocket.close(code=4003, reason="auth_revoked")
            return

        while True:
            # Keep connection alive; handle client messages if any
            data = await websocket.receive_json()

            # Handle client commands
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except HTTPException:
        try:
            await websocket.close(code=1008)
        except Exception:
            pass
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("ws_error", session_id=session_id)
    finally:
        try:
            if expiry_task is not None:
                expiry_task.cancel()
                await asyncio.gather(expiry_task, return_exceptions=True)
        finally:
            try:
                if manager_connect_attempted:
                    ws_manager.disconnect(websocket, session_id)
            finally:
                try:
                    if lease is not None:
                        auth_runtime.release_connection(lease)
                finally:
                    reset_current_auth(token)


@app.websocket("/api/stream/native/{session_id}")
async def native_stream_endpoint(websocket: WebSocket, session_id: str):
    """Ingest dual-channel audio from the native macOS companion over WebSocket."""
    connection_started = datetime.now(timezone.utc)
    iap_user = _iap_websocket_user(websocket)
    if settings is not None and settings.auth_mode == "iap" and iap_user is None:
        # Native desktop companions have no approved IAP session flow.  A
        # stream key alone is never an application-level bypass in IAP mode.
        await websocket.close(code=1008)
        return
    raw_subprotocol = websocket.headers.get("sec-websocket-protocol")
    offered = [item.strip() for item in raw_subprotocol.split(",")] if raw_subprotocol is not None else []
    presented = ""
    if len(offered) == 2 and offered[0] == "tars-stream" and offered[1]:
        presented = offered[1]
    _prune_expired_iap_tickets()
    stream_entry = stream_tickets.get(presented) if iap_user is not None else None
    expected = stream_keys.get(session_id)
    session = None
    # secrets.compare_digest(str, str) raises TypeError on non-ASCII input
    # (an attacker-controlled input) before reaching the clean 1008
    # close below. Comparing UTF-8 bytes instead accepts arbitrary str
    # content without that restriction; the try/except is defense in depth
    # in case anything still slips through as non-str/non-encodable.
    if iap_user is not None:
        key_matches = bool(stream_entry) and stream_entry[1] == session_id
        if key_matches and stream_entry is not None:
            key_matches = stream_entry[2] > datetime.now(timezone.utc)
            key_matches = key_matches and (
                stream_entry[0].uid == iap_user.uid
                and stream_entry[0].email == iap_user.email
                and stream_entry[0].org_id == iap_user.org_id
                and stream_entry[0].auth_time == iap_user.auth_time
            )
        if not key_matches or not auth_runtime.admit_principal(
            iap_user.uid, iap_user.auth_time or 0
        ):
            await websocket.close(code=1008)
            return
        session = session_mgr.get_session(session_id) if session_mgr else None
    else:
        session = session_mgr.get_session(session_id) if session_mgr else None
        try:
            key_matches = bool(expected) and bool(presented) and secrets.compare_digest(
                presented.encode("utf-8", "surrogatepass"),
                expected.encode("utf-8", "surrogatepass"),
            )
        except TypeError:
            key_matches = False
    if (
        not key_matches
        or session is None
        or session.status != SessionStatus.ACTIVE
        or (
            iap_user is not None
            and (
                session.owner_id != iap_user.uid
                or session.org_id != iap_user.org_id
            )
        )
    ):
        logger.warning("native_stream_rejected", session_id=session_id)
        await websocket.close(code=1008)
        return
    if iap_user is not None:
        stream_tickets.pop(presented, None)
        auth_runtime.consume_ticket(presented)
    lease: ConnectionLease | None = None
    lease_released = False
    if iap_user is not None:
        lease = auth_runtime.register_connection(iap_user.uid, iap_user.auth_time or 0)
        if lease is None:
            await websocket.close(code=1008)
            return
    expiry_task: asyncio.Task | None = None
    stall_task: asyncio.Task | None = None
    accept_task: asyncio.Task | None = None
    health_registered = False

    async def cancel_expiry_task() -> None:
        nonlocal expiry_task
        if expiry_task is not None:
            expiry_task.cancel()
            await asyncio.gather(expiry_task, return_exceptions=True)
            expiry_task = None

    def release_lease() -> None:
        nonlocal lease_released
        if lease is not None and not lease_released:
            auth_runtime.release_connection(lease)
            lease_released = True

    def admission_is_current() -> bool:
        if lease is None or iap_user is None:
            return True
        session_now = _local_session(session_id)
        return (
            not lease.closed
            and auth_runtime.is_principal_admissible(
                iap_user.uid, iap_user.auth_time or 0
            )
            and session_now is not None
            and session_now.status == SessionStatus.ACTIVE
            and session_now.owner_id == iap_user.uid
            and session_now.org_id == iap_user.org_id
            and session_id in stream_keys
        )

    async def cancel_accept_task() -> None:
        if accept_task is not None:
            if not accept_task.done():
                accept_task.cancel()
            await asyncio.gather(accept_task, return_exceptions=True)

    admission_cleanup_needed = True
    try:
        app_settings = settings or get_settings()
        if lease is not None:
            expiry_task = asyncio.create_task(
                _close_ws_at_expiry(
                    websocket,
                    connection_started
                    + timedelta(seconds=app_settings.auth_iap_ws_max_lifetime_seconds),
                    lease,
                )
            )
            accept_task = asyncio.create_task(
                websocket.accept(subprotocol="tars-stream")
            )
            done, _pending = await asyncio.wait(
                {accept_task, expiry_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if expiry_task in done or lease.closed:
                await websocket.close(code=1008)
                return
            await accept_task
        else:
            await websocket.accept(subprotocol="tars-stream")
        logger.info("native_companion_connected", session_id=session_id)
        if not admission_is_current():
            await websocket.close(code=1008)
            return
        # From this point the common native lifecycle fence below owns every
        # task/health/lease cleanup. The pre-accept fence above remains active
        # for all failures and cancellation before health registration.
        admission_cleanup_needed = False
    finally:
        if admission_cleanup_needed:
            await cancel_accept_task()
            await cancel_expiry_task()
            release_lease()

    # --- companion_health / coverage_gap emission -------------------------
    # Session-scoped (not connection-scoped): native_stream_endpoint serves
    # multiple concurrent WS connections per session (browser mic + native
    # companion), so health is tracked in the shared native_session_health
    # merged view keyed by session_id, and every broadcast carries that
    # MERGED two-source view. This connection only ever mutates the source(s)
    # it has itself observed a frame/gap/stall-transition for (tracked in
    # `owned_sources`) — never resetting a source another still-open
    # connection is carrying. `connections` is the live WS count for the
    # session: physical_capture is "active" while it is >0, "unknown" while
    # connections == 0 but >=1 source is "reconnecting", and "stopped"
    # otherwise.
    session_health: dict | None = None
    owned_sources: set[str] = set()
    intended_since: dict[str, float] = {}
    last_emitted_health: dict | None = None
    last_frame_at: dict[str, float] = {}
    try:
        session_health = native_session_health.setdefault(
            session_id,
            {
                "sources": {"microphone": "unknown", "system_audio": "unknown"},
                "source_connections": {"microphone": 0, "system_audio": 0},
                "alerts": {},
                "connections": 0,
            },
        )
        session_health.setdefault("source_connections", {"microphone": 0, "system_audio": 0})
        session_health.setdefault("alerts", {})
        session_health["connections"] += 1
        health_registered = True
    except BaseException:
        if session_health is not None and health_registered:
            session_health["connections"] = max(0, session_health["connections"] - 1)
        if session_health is not None and not health_registered:
            # A failure during the initial setdefault/source-counter setup
            # must not leave an empty health record that looks connected to a
            # later readiness or cleanup pass.
            if session_health.get("connections", 0) == 0:
                native_session_health.pop(session_id, None)
        await cancel_expiry_task()
        release_lease()
        raise

    def _mark_owned(source_name: str) -> None:
        if source_name in ("microphone", "system_audio"):
            if source_name not in owned_sources:
                owned_sources.add(source_name)
                session_health["source_connections"][source_name] = (
                    session_health["source_connections"].get(source_name, 0) + 1
                )

    def _set_source_health(source_name: str, state: str) -> bool:
        """Mutate the session's MERGED health for source_name to state;
        return True if it actually changed (used to decide whether an
        emission is warranted). Also records that THIS connection has
        observed source_name, so its close-time cleanup (see `finally`
        below) only resets sources it actually carried."""
        if source_name not in ("microphone", "system_audio"):
            return False
        _mark_owned(source_name)
        if session_health["sources"].get(source_name) == state:
            return False
        session_health["sources"][source_name] = state
        return True

    async def emit_health() -> None:
        """Broadcast companion_health iff the payload changed since the last
        emission (avoids spamming identical state every watchdog tick).
        Broadcast failures must never break audio ingestion, so they are
        caught here and only logged."""
        nonlocal last_emitted_health
        if session_health["connections"] > 0:
            phys_capture = "active"
        elif any(s == "reconnecting" for s in session_health["sources"].values()):
            phys_capture = "unknown"
        else:
            phys_capture = "stopped"

        active_alerts = []
        for src in ("microphone", "system_audio"):
            if src in session_health["alerts"]:
                active_alerts.append(session_health["alerts"][src])
        msg_text = " ".join(active_alerts) if active_alerts else None

        payload = CompanionHealthPayload(
            physical_capture=phys_capture,
            sources=SourceHealthReport(**session_health["sources"]),
            message=msg_text,
        )
        payload_dict = payload.model_dump()
        if payload_dict == last_emitted_health:
            return
        last_emitted_health = payload_dict
        try:
            seq = ws_manager.next_sequence(session_id)
            msg = WSMessage.companion_health_msg(session_id, seq, payload)
            await ws_manager.broadcast(session_id, msg)
        except Exception as e:
            logger.debug(
                "native_companion_health_emit_error",
                session_id=session_id,
                error=str(e),
            )

    async def cleanup_native_lifecycle() -> None:
        """Settle every task/lease/health count, including setup failures."""
        nonlocal health_registered, expiry_task, stall_task
        await cancel_accept_task()
        await cancel_expiry_task()
        if stall_task is not None:
            stall_task.cancel()
            await asyncio.gather(stall_task, return_exceptions=True)
            stall_task = None

        if health_registered:
            session_health["connections"] = max(0, session_health["connections"] - 1)
            session_obj = session_mgr.get_session(session_id) if session_mgr else None
            session_is_active = (
                session_id in stream_keys
                and session_obj is not None
                and session_obj.status == SessionStatus.ACTIVE
            )
            for source_name in owned_sources:
                current_count = session_health["source_connections"].get(source_name, 1) - 1
                session_health["source_connections"][source_name] = max(0, current_count)
                if session_health["source_connections"][source_name] == 0:
                    session_health["alerts"].pop(source_name, None)
                    session_health["sources"][source_name] = (
                        "reconnecting" if session_is_active else "unknown"
                    )
            health_registered = False
            try:
                await emit_health()
            except Exception:
                logger.debug("native_companion_health_cleanup_emit_error", session_id=session_id)

        release_lease()

    async def emit_gap(gap: CoverageGapSegment) -> None:
        try:
            seq = ws_manager.next_sequence(session_id)
            msg = WSMessage.coverage_gap_msg(session_id, seq, CoverageGapPayload(gap=gap))
            await ws_manager.broadcast(session_id, msg)
        except Exception as e:
            logger.debug(
                "native_companion_gap_emit_error",
                session_id=session_id,
                error=str(e),
            )

    async def stall_watchdog() -> None:
        """Watchdog for:
        1. Never-produced timeout: an intended source that has not produced any frame
           within NATIVE_NEVER_PRODUCED_TIMEOUT_SECONDS is flagged device_unavailable
           with a descriptive alert message.
        2. Post-first-frame stall: a source that has produced >=1 frame but none for
           longer than NATIVE_STALL_TIMEOUT_SECONDS is flagged device_unavailable."""
        while True:
            await asyncio.sleep(NATIVE_STALL_CHECK_INTERVAL_SECONDS)
            try:
                now = time.monotonic()
                changed = False

                # 1. Never-produced checks for intended sources
                for source_name, start_time in list(intended_since.items()):
                    if source_name not in last_frame_at and (now - start_time > NATIVE_NEVER_PRODUCED_TIMEOUT_SECONDS):
                        if (
                            session_health["source_connections"].get(source_name, 0) > 1
                            and session_health["sources"].get(source_name) == "healthy"
                        ):
                            continue
                        if source_name not in session_health["alerts"]:
                            session_health["alerts"][source_name] = NEVER_PRODUCED_MESSAGES[source_name]
                            _set_source_health(source_name, "device_unavailable")
                            logger.warning(
                                "native_source_never_produced_frames",
                                session_id=session_id,
                                source=source_name,
                            )
                            changed = True

                # 2. Post-first-frame stall checks
                for source_name, last_seen in list(last_frame_at.items()):
                    if now - last_seen > NATIVE_STALL_TIMEOUT_SECONDS:
                        if _set_source_health(source_name, "device_unavailable"):
                            changed = True

                if changed:
                    await emit_health()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # A single bad tick must not kill the watchdog for the rest
                # of the connection, nor escape `finally`'s cancellation
                # await as anything other than CancelledError.
                logger.debug(
                    "native_stall_watchdog_tick_error",
                    session_id=session_id,
                    error=str(e),
                )

    try:
        await emit_health()
        stall_task = asyncio.create_task(stall_watchdog())
    except BaseException:
        await cleanup_native_lifecycle()
        raise

    def _is_duplicate_frame(source_name: str, sequence: object) -> bool:
        """True iff `sequence` is a replay of an already-accepted frame for
        (session_id, source_name). The companion may legitimately resend a
        frame whose send() timed out but actually landed, so only a SMALL
        backward step (< NATIVE_FRAME_DEDUP_WINDOW, ~10s at 50ms/frame) is
        treated as that replay. A larger backward jump is a companion-process
        restart (its own sequence counter restarts at 1) and is accepted,
        moving the tracked baseline to wherever the new stream lands."""
        if source_name not in ("microphone", "system_audio") or not isinstance(sequence, int):
            return False
        per_source = native_frame_last_seq.setdefault(session_id, {})
        last_seq = per_source.get(source_name)
        if last_seq is not None and sequence <= last_seq:
            if (last_seq - sequence) < NATIVE_FRAME_DEDUP_WINDOW:
                return True  # replay within window: drop, baseline untouched
            # Restart branch: the backward jump is >= the window, so this is
            # a legitimate companion-process restart, not a replay. REPLACE
            # the baseline outright rather than max()-ing it against the old
            # (now-irrelevant) high-water mark — otherwise, once the new
            # stream's own sequence climbs back to within
            # NATIVE_FRAME_DEDUP_WINDOW of that stale mark, its genuine
            # frames would be misread as within-window replays of the OLD
            # stream and silently dropped.
            per_source[source_name] = sequence
            return False
        # First frame ever for this source, or normal forward progress
        # (sequence > last_seq): max() and direct assignment agree here
        # (sequence > last_seq always in the forward case), so max() is kept
        # as the explicit, defensive form for this branch only.
        per_source[source_name] = sequence if last_seq is None else max(last_seq, sequence)
        return False

    async def get_or_create_sm(source_label: str) -> StreamManager:
        # Reserve the registry slot under the lock, but do not await provider
        # startup while holding it.  Kill/logout cleanup needs this same lock
        # to detach managers and signal the admission fence.
        async with native_sm_lock:
            if not admission_is_current():
                raise RuntimeError("native stream admission lost")
            per_session = native_stream_managers.setdefault(session_id, {})
            existing = per_session.get(source_label)
            if existing is not None:
                return existing
            # _stop_pipeline pops stream_keys[session_id] under this same
            # lock before tearing down the SM registry. If it's already gone,
            # the session is stopping/stopped: refuse to spin up a manager
            # nothing will ever stop.
            if session_id not in stream_keys:
                raise RuntimeError("session stopping — native stream refused")
            sm = StreamManager(
                settings=app_settings,
                on_transcript=lambda seg: _on_transcript(session_id, seg),
                source_label=source_label,
            )
            # Keep compatibility with injected StreamManager test doubles that
            # predate the admission callback constructor argument.
            setattr(sm, "_admission_check", admission_is_current)
            setattr(
                sm,
                "_task_tracker",
                lambda task: _register_stt_task(task, session_id=session_id),
            )

        try:
            await sm.start()
        except BaseException:
            abort = getattr(sm, "abort_emergency", None)
            try:
                if callable(abort):
                    await abort()
                else:
                    await sm.stop()
            except Exception:
                logger.exception(
                    "native_stream_start_failure_cleanup_error",
                    session_id=session_id,
                )
            raise

        if not admission_is_current():
            try:
                abort = getattr(sm, "abort_emergency", None)
                if callable(abort):
                    await abort()
                else:
                    await sm.stop()
            except Exception:
                logger.exception(
                    "native_stream_admission_lost_stop_error",
                    session_id=session_id,
                )
            raise RuntimeError("native stream admission lost")

        async with native_sm_lock:
            if not admission_is_current():
                publish = False
                existing = None
            else:
                per_session = native_stream_managers.setdefault(session_id, {})
                existing = per_session.get(source_label)
                publish = existing is None
                if publish:
                    per_session[source_label] = sm
                    stream_managers.setdefault(session_id, []).append(sm)
        if not publish:
            try:
                abort = getattr(sm, "abort_emergency", None)
                if callable(abort):
                    await abort()
                else:
                    await sm.stop()
            except Exception:
                logger.exception(
                    "native_stream_duplicate_start_cleanup_error",
                    session_id=session_id,
                )
            if existing is None:
                raise RuntimeError("native stream admission lost")
            # Another request won publication while this manager was starting.
            # Reuse the published manager; only the unpublishable loser is
            # emergency-aborted above.
            return existing
        return sm

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"]:
                raw_data: bytes = message["bytes"]
                if len(raw_data) < 4:
                    continue
                header_len = int.from_bytes(raw_data[:4], byteorder="big")
                if len(raw_data) < 4 + header_len:
                    continue
                header_json = raw_data[4 : 4 + header_len]
                pcm_payload = raw_data[4 + header_len :]

                try:
                    header = json.loads(header_json.decode("utf-8"))
                except Exception:
                    continue

                if not isinstance(header, dict) or not isinstance(header.get("session_id"), str):
                    logger.warning(
                        "native_stream_frame_rejected",
                        session_id=session_id,
                        reason="missing_session_id",
                    )
                    await websocket.close(code=1008)
                    return

                if header.get("session_id") != session_id:
                    logger.warning(
                        "native_stream_frame_rejected",
                        session_id=session_id,
                        reason="mismatched_session_id",
                    )
                    await websocket.close(code=1008)
                    return

                source = header.get("source", "microphone")
                if source in ("microphone", "system_audio"):
                    _mark_owned(source)
                    last_frame_at[source] = time.monotonic()
                    intended_since.pop(source, None)
                    alert_cleared = bool(session_health["alerts"].pop(source, None))
                    health_changed = _set_source_health(source, "healthy")
                    if health_changed or alert_cleared:
                        await emit_health()
                source_label = (
                    app_settings.stt_speaker_label_other
                    if source == "system_audio"
                    else app_settings.stt_speaker_label_self
                )

                if _is_duplicate_frame(source, header.get("sequence")):
                    logger.debug(
                        "native_stream_duplicate_frame",
                        session_id=session_id,
                        source=source,
                        sequence=header.get("sequence"),
                    )
                    continue

                try:
                    sm = await get_or_create_sm(source_label)
                    if not admission_is_current():
                        raise RuntimeError("native stream admission lost")
                    if pcm_payload:
                        await sm.send_audio(pcm_payload)
                except Exception as e:
                    logger.warning(
                        "native_stream_audio_send_error",
                        session_id=session_id,
                        source=source_label,
                        error=str(e),
                    )
            elif "text" in message and message["text"]:
                try:
                    text_data = json.loads(message["text"])
                    msg_type = text_data.get("type")
                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
                    elif msg_type == "hello":
                        raw_sources = text_data.get("sources")
                        if (
                            not isinstance(raw_sources, list)
                            or not raw_sources
                            or not all(isinstance(s, str) and s in ("microphone", "system_audio") for s in raw_sources)
                        ):
                            logger.warning("native_companion_hello_invalid", session_id=session_id)
                        else:
                            valid_sources = sorted(set(raw_sources))
                            now = time.monotonic()
                            for src in valid_sources:
                                if src not in intended_since:
                                    intended_since[src] = now
                                _mark_owned(src)
                            logger.info(
                                "native_companion_hello",
                                session_id=session_id,
                                sources=valid_sources,
                            )
                    elif msg_type == "gap":
                        gap_source = text_data.get("source", "unknown")
                        reason = text_data.get("reason", "unknown")
                        _mark_owned(gap_source)
                        first_sample = text_data.get("first_sample") or 0
                        logger.warning(
                            "native_companion_gap_reported",
                            session_id=session_id,
                            source=gap_source,
                            reason=reason,
                            first_sample=first_sample,
                        )
                        await emit_gap(
                            CoverageGapSegment(
                                id=uuid4().hex[:12],
                                source=gap_source,
                                start_ms=first_sample / 16.0,
                                reason=reason,
                            )
                        )
                        # Map the companion's low-level gap reason onto a
                        # source health state; unmapped reasons leave the
                        # source's health state unchanged.
                        health_state = {
                            "permission_denied": "permission_missing",
                            "device_lost": "device_unavailable",
                            "overrun": "overflow",
                            "buffer_exhaustion": "overflow",
                        }.get(reason)
                        if health_state and _set_source_health(gap_source, health_state):
                            await emit_health()
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("native_stream_error", session_id=session_id)
    finally:
        await cleanup_native_lifecycle()
        logger.info("native_companion_disconnected", session_id=session_id)


# --- Entry point ---

def main() -> None:
    s = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=s.fastapi_host,
        port=s.fastapi_port,
        reload=True,
    )


if __name__ == "__main__":
    main()
