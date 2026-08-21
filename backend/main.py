"""FastAPI application — T.A.R.S. backend server."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import structlog
import uvicorn
from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

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
)
from backend.config import Settings, get_settings
from backend.documents.parser import MAX_FILE_SIZE, DocumentParseError, parse_document
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
    ConnectionHealth,
    ConnectionStatusPayload,
    ErrorPayload,
    ErrorSeverity,
    HeartbeatRequest,
    ParticipantsList,
    SessionMode,
    SessionStatus,
    SetContextRequest,
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
pipeline_tasks: dict[str, list[asyncio.Task]] = {}
session_stop_locks: dict[str, asyncio.Lock] = {}
final_summary_scheduled: set[str] = set()
final_summary_tasks: dict[str, asyncio.Task] = {}
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
stop_capabilities: dict[str, tuple[AuthContext, str, datetime]] = {}
_clock_sync_timestamps: dict[str, float] = {}  # session_id -> last sync time (rate limit)


def _context_window_for(session_id: str) -> ContextWindowManager | None:
    """Return isolated rolling-summary state for one session."""
    return context_windows.get(session_id) or context_window


# Concurrency guard for pre-interview analysis
_analyze_semaphore = asyncio.Semaphore(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize and cleanup."""
    global settings, session_mgr, firestore_storage, gcs_storage, gemini_client, context_window

    settings = get_settings()
    await probe_application_default_credentials()
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
    yield

    # Cleanup: stop all active pipelines
    for session_id in list(pipeline_tasks.keys()):
        await _stop_pipeline(session_id)
    context_windows.clear()

    logger.info("server_stopped")


app = FastAPI(title="T.A.R.S.", lifespan=lifespan)

@app.middleware("http")
async def authenticate_api_requests(request: Request, call_next):
    """Authenticate every API request before route code can touch data."""
    if request.method == "OPTIONS" or not request.url.path.startswith("/api/"):
        return await call_next(request)
    try:
        request_settings = settings or get_settings()
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3003",
        "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    """Unauthenticated process health; readiness still depends on lifespan."""
    return {"status": "ok"}


@app.get("/api/me")
async def current_user_profile():
    """Authenticated admission preflight; no interview data is returned."""
    user = _principal()
    return {"uid": user.uid, "email": user.email, "org_id": user.org_id}


async def _close_ws_at_expiry(websocket: WebSocket, expires_at: datetime) -> None:
    """Bound a socket's authorization lifetime; reconnect mints a fresh ticket."""
    delay = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
    await asyncio.sleep(delay)
    try:
        await websocket.close(code=4001, reason="auth_expired")
    except Exception:
        pass


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
    token = secrets.token_urlsafe(32)
    store[token] = (
        user,
        session_id,
        datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    )
    return token


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
            # A PortAudio callback may already have queued _enqueue onto this
            # event loop. Give that callback one turn before emptying the tail.
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
    if session_id in session_deletion_fences:
        logger.warning(
            "transcript_callback_after_delete_ignored",
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
    if session_id in session_deletion_fences:
        return

    # 4. Persist to Firestore (WITH override, after correlation)
    if segment.is_final:
        try:
            await _save_transcript_segment(session_id, segment)
        except Exception:
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

        if session_id in session_deletion_fences:
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
        if firestore_storage:
            if session_id in session_deletion_fences:
                return
            session = session_mgr.get_session(session_id)
            await firestore_storage.save_summary(
                session_id, summary,
                covering_from=previous_seq,
                covering_to=batch_end,
                owner_id=session.owner_id if session else None,
                org_id=session.org_id if session else None,
            )

        if batch_end < current_seq:
            # The task completion callback starts the next contiguous batch
            # after this task is removed from the in-flight map.
            rolling_summary_followups.add(session_id)

    except Exception:
        logger.exception("rolling_summary_error", session_id=session_id)


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
            message="Audio de apenas uma fonte detectado. Verifique a configuração do BlackHole para capturar o áudio remoto.",
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

        if session_id in session_deletion_fences:
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

    except Exception:
        logger.exception("interview_suggestion_error", session_id=session_id)


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

    try:
        if is_interview:
            existing = await firestore_storage.get_interview_report(session_id)
            generation_state = await firestore_storage.get_report_generation_state(
                session_id
            )
            if existing is not None:
                report = existing.model_copy(
                    update={
                        "owner_id": session.owner_id,
                        "org_id": session.org_id,
                    }
                )
                await _save_report_generation_state(
                    session_id,
                    "ready",
                    session=session,
                )
            elif generation_state and generation_state.get("status") == "failed":
                logger.warning(
                    "interview_report_generation_previously_failed",
                    session_id=session_id,
                )
                return
            elif generation_state and generation_state.get("status") == "generating":
                await _save_report_generation_state(
                    session_id,
                    "failed",
                    reason_code="generation_interrupted",
                    session=session,
                )
                logger.error(
                    "interview_report_generation_interrupted",
                    session_id=session_id,
                )
                return
            elif generation_state and generation_state.get("status") == "ready":
                await _save_report_generation_state(
                    session_id,
                    "failed",
                    reason_code="ready_without_report",
                    session=session,
                )
                logger.error(
                    "interview_report_ready_without_report",
                    session_id=session_id,
                )
                return
            else:
                await _save_report_generation_state(
                    session_id,
                    "generating",
                    session=session,
                )
                transcript_records = await firestore_storage.get_session_transcript(session_id)
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
                if session.owner_id is not None:
                    for record in note_records:
                        if record.get("ownerId") != session.owner_id or record.get("orgId") != session.org_id:
                            raise InterviewReportError("durable note scope is invalid")
                notes = deserialize_recruiter_notes(
                    session_id,
                    note_records,
                )
                context_records = await firestore_storage.get_interview_context(session_id)
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
                    await _save_report_generation_state(
                        session_id,
                        "failed",
                        reason_code="report_input_too_large",
                        session=session,
                    )
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
                await _save_report_generation_state(
                    session_id,
                    "ready",
                    session=session,
                )
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
        session = session_mgr.get_session(session_id)
        if session:
            await firestore_storage.save_session(session)
            await firestore_storage.save_summary(
                session_id, summary,
                covering_from=summary_covering_from,
                covering_to=len(transcript),
                is_final=True,
                owner_id=session.owner_id,
                org_id=session.org_id,
            )

    except Exception:
        logger.exception("final_summary_error", session_id=session_id)
        if is_interview:
            try:
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


def _cleanup_session_context(
    session_id: str,
    *,
    release_transcript_memory: bool = True,
    preserve_stop_capability: bool = False,
) -> None:
    """Remove sensitive per-interview context after terminal handling."""
    interview_documents.pop(session_id, None)
    interview_final_segment_counts.pop(session_id, None)
    interview_suggestion_counters.pop(session_id, None)
    single_source_warned.discard(session_id)
    context_windows.pop(session_id, None)
    rolling_task = rolling_summary_tasks.pop(session_id, None)
    if rolling_task is not None and not rolling_task.done():
        rolling_task.cancel()
    rolling_summary_followups.discard(session_id)
    suggestion_task = interview_suggestion_tasks.pop(session_id, None)
    if suggestion_task is not None and not suggestion_task.done():
        suggestion_task.cancel()
    single_source_task = single_source_check_tasks.pop(session_id, None)
    if single_source_task is not None and not single_source_task.done():
        single_source_task.cancel()
    speaker_correlators.pop(session_id, None)
    extension_tokens.pop(session_id, None)
    extension_capability_expiry.pop(session_id, None)
    for token, (_, token_session_id, _) in list(ws_tickets.items()):
        if token_session_id == session_id:
            ws_tickets.pop(token, None)
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
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("final_summary_cancel_error", session_id=session_id)


def _schedule_final_summary_once(session_id: str) -> None:
    """Schedule at most one final report for a session in this process."""
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


async def _stop_pipeline(session_id: str) -> bool:
    """Stop the audio pipeline and report whether every STT stream drained."""
    managers = list(stream_managers.get(session_id, []))
    tasks = pipeline_tasks.pop(session_id, None)
    if tasks:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Clean up interview runtime state (documents cleaned after final summary)
    interview_final_segment_counts.pop(session_id, None)
    interview_suggestion_counters.pop(session_id, None)
    rolling_task = rolling_summary_tasks.pop(session_id, None)
    if rolling_task is not None and not rolling_task.done():
        rolling_task.cancel()
    suggestion_task = interview_suggestion_tasks.pop(session_id, None)
    if suggestion_task is not None and not suggestion_task.done():
        suggestion_task.cancel()
    single_source_warned.discard(session_id)
    single_source_task = single_source_check_tasks.pop(session_id, None)
    if single_source_task is not None and not single_source_task.done():
        single_source_task.cancel()
    if not managers:
        logger.error(
            "audio_pipeline_missing_stream_manager",
            session_id=session_id,
        )
        return False
    return all(manager.drain_completed is True for manager in managers)


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

    session_mode = SessionMode(mode)
    # Launch Week 4 is interview-only; meeting remains a backend compatibility
    # mode for older direct callers but is not admitted by the web UI.
    session = session_mgr.create_session(
        mode=session_mode,
        title=title,
        owner_id=user.uid if user else None,
        org_id=user.org_id if user else None,
    )
    session.notice_given = notice_given

    # Save to Firestore
    await firestore_storage.save_session(session)

    # Rolling summaries carry prior model context and counters; never share
    # that state between authenticated sessions or organizations.
    assert gemini_client
    context_windows[session.id] = ContextWindowManager(settings, gemini_client)

    # Mint the stop-only recovery capability before capture starts. Its TTL is
    # configured above the maximum session duration plus the bounded drain.
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

    # Start heartbeat
    await session_mgr.start_heartbeat(session.id)

    # Start audio pipeline
    task = asyncio.create_task(_run_audio_pipeline(session.id))
    pipeline_tasks[session.id] = [task]

    return {
        "session_id": session.id,
        "status": "active",
        "mode": mode,
        "stop_capability": stop_capability,
        "stop_capability_expires_in": settings.auth_stop_capability_ttl_seconds,
    }


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
    async with _analyze_semaphore:
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
    session = await _read_session(session_id)
    user = _assert_session_access(session)
    ticket = _mint_capability(ws_tickets, user, session_id, settings.auth_ws_ticket_ttl_seconds)
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
    # Browser WebSocket cannot set Authorization headers. It receives a
    # short-lived, single-use ticket from the authenticated HTTP API instead.
    offered = [item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",") if item.strip()]
    if len(offered) != 2 or offered[0] != "tars-ticket":
        await websocket.close(code=1008)
        return
    ticket = offered[1]
    entry = ws_tickets.pop(ticket, None)
    now = datetime.now(timezone.utc)
    if entry is None or entry[1] != session_id or entry[2] <= now:
        await websocket.close(code=1008)
        return
    user = entry[0]
    token = set_current_auth(user)
    try:
        session = await _read_session(session_id)
        if session.owner_id != user.uid or session.org_id != user.org_id:
            await websocket.close(code=1008)
            return
    except HTTPException:
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

    await ws_manager.connect(websocket, session_id, last_seq=last_seq, subprotocol="tars-ticket")
    expiry_task = asyncio.create_task(_close_ws_at_expiry(websocket, entry[2]))

    try:
        while True:
            # Keep connection alive; handle client messages if any
            data = await websocket.receive_json()

            # Handle client commands
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, session_id)
    except Exception:
        logger.exception("ws_error", session_id=session_id)
        ws_manager.disconnect(websocket, session_id)
    finally:
        expiry_task.cancel()
        reset_current_auth(token)


@app.websocket("/api/stream/native/{session_id}")
async def native_stream_endpoint(websocket: WebSocket, session_id: str):
    """Ingest dual-channel audio from the native macOS companion over WebSocket."""
    await websocket.accept()
    logger.info("native_companion_connected", session_id=session_id)
    app_settings = settings or get_settings()

    sms: dict[str, StreamManager] = {}

    async def get_or_create_sm(source_label: str) -> StreamManager:
        if source_label not in sms:
            sm = StreamManager(
                settings=app_settings,
                on_transcript=lambda seg: _on_transcript(session_id, seg),
                source_label=source_label,
            )
            await sm.start()
            sms[source_label] = sm
            stream_managers.setdefault(session_id, []).append(sm)
        return sms[source_label]

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

                source = header.get("source", "microphone")
                source_label = (
                    app_settings.stt_speaker_label_other
                    if source == "system_audio"
                    else app_settings.stt_speaker_label_self
                )

                sm = await get_or_create_sm(source_label)
                if pcm_payload:
                    await sm.send_audio(pcm_payload)
            elif "text" in message and message["text"]:
                try:
                    text_data = json.loads(message["text"])
                    if text_data.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                    elif text_data.get("type") == "gap":
                        logger.warning(
                            "native_companion_gap_reported",
                            session_id=session_id,
                            source=text_data.get("source"),
                            reason=text_data.get("reason"),
                            first_sample=text_data.get("first_sample"),
                        )
                except Exception:
                    pass
    except WebSocketDisconnect:
        logger.info("native_companion_disconnected", session_id=session_id)
    except Exception:
        logger.exception("native_stream_error", session_id=session_id)
    finally:
        for sm in list(sms.values()):
            try:
                await sm.stop()
            except Exception:
                pass
        stream_managers.pop(session_id, None)


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
