"""FastAPI application — T.A.R.S. backend server."""

from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from backend.audio.buffer import AudioBuffer
from backend.audio.capture import AudioCapture
from backend.config import Settings, get_settings
from backend.documents.parser import parse_document, DocumentParseError
from backend.llm.context_window import ContextWindowManager
from backend.llm.gemini import GeminiClient
from backend.llm.interview_prompts import (
    INTERVIEW_SYSTEM_PROMPT,
    INTERVIEW_REPORT_PROMPT,
    PRE_INTERVIEW_ANALYSIS_PROMPT,
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
from backend.utils.sanitize import sanitize_participant_name
from backend.sessions.manager import SessionManager
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
context_window: ContextWindowManager | None = None

# Active audio pipeline per session (lists for dual capture: [system, mic])
audio_captures: dict[str, list[AudioCapture]] = {}
audio_buffers: dict[str, list[AudioBuffer]] = {}
stream_managers: dict[str, list[StreamManager]] = {}
pipeline_tasks: dict[str, list[asyncio.Task]] = {}

# Interview context
interview_documents: dict[str, dict[str, str]] = {}  # session_id -> {resume, jd}
interview_suggestion_counters: dict[str, int] = {}  # session_id -> final segment count
single_source_warned: set[str] = set()  # sessions already warned about single audio source

# Speaker correlation (Chrome extension)
speaker_correlators: dict[str, SpeakerCorrelator] = {}
extension_tokens: dict[str, str] = {}  # session_id -> token
_clock_sync_timestamps: dict[str, float] = {}  # session_id -> last sync time (rate limit)

# Concurrency guard for pre-interview analysis
_analyze_semaphore = asyncio.Semaphore(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize and cleanup."""
    global settings, session_mgr, firestore_storage, gcs_storage, gemini_client, context_window

    settings = get_settings()
    session_mgr = SessionManager(settings)
    firestore_storage = FirestoreStorage(settings)
    gcs_storage = GCSStorage(settings)
    gemini_client = GeminiClient(settings)
    context_window = ContextWindowManager(settings, gemini_client)

    # Detect orphaned sessions from previous crash
    orphaned = session_mgr.detect_orphaned_sessions()
    if orphaned:
        logger.warning("orphaned_sessions_found", count=len(orphaned))

    logger.info("server_started", host=settings.fastapi_host, port=settings.fastapi_port)
    yield

    # Cleanup: stop all active pipelines
    for session_id in list(pipeline_tasks.keys()):
        await _stop_pipeline(session_id)

    logger.info("server_stopped")


app = FastAPI(title="T.A.R.S.", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga",  # pinned extension ID (derived from manifest "key")
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Audio Pipeline ---

async def _run_single_audio_stream(
    session_id: str,
    device_name: str,
    source_label: str,
    capture_list: list[AudioCapture],
    buffer_list: list[AudioBuffer],
    sm_list: list[StreamManager],
) -> None:
    """Single audio stream: capture → STT → broadcast.

    All audio (including silence) is sent continuously to keep the STT
    stream alive. Google STT V2 voice_activity_timeout handles endpointing.
    """
    assert settings and session_mgr

    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=settings.buffer_max_chunks)
    capture = AudioCapture(
        settings, audio_queue, device_name=device_name, label=source_label,
    )
    buffer = AudioBuffer(settings, audio_queue)

    sm = StreamManager(
        settings=settings,
        on_transcript=lambda seg: _on_transcript_sync(session_id, seg),
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
        logger.exception(
            "audio_pipeline_error", session_id=session_id, source=source_label,
        )
    finally:
        await sm.stop()
        await buffer.stop()
        await capture.stop()


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
            ),
            _run_single_audio_stream(
                session_id=session_id,
                device_name=settings.microphone_device_name,
                source_label=settings.stt_speaker_label_self,
                capture_list=captures,
                buffer_list=buffers,
                sm_list=sms,
            ),
        )
    finally:
        audio_captures.pop(session_id, None)
        audio_buffers.pop(session_id, None)
        stream_managers.pop(session_id, None)


def _on_transcript_sync(session_id: str, segment: TranscriptSegment) -> None:
    """Called from the stream manager (sync context) — schedules async work."""
    loop = asyncio.get_event_loop()
    loop.create_task(_on_transcript(session_id, segment))


async def _on_transcript(session_id: str, segment: TranscriptSegment) -> None:
    """Handle a new transcript segment."""
    assert session_mgr and firestore_storage and context_window

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

    # 4. Persist to Firestore (WITH override, after correlation)
    if segment.is_final:
        try:
            await firestore_storage.save_transcript_segment(session_id, segment)
        except Exception:
            logger.exception("firestore_save_error", session_id=session_id)

    # Check if we should generate a rolling summary
    if segment.is_final:
        word_count = session_mgr.get_transcript_word_count(
            session_id, from_seq=context_window.last_summary_seq
        )
        if context_window.should_summarize(word_count):
            asyncio.create_task(_generate_rolling_summary(session_id))

    # Interview mode: generate suggestions every 5th final segment
    session = session_mgr.get_session(session_id)
    if (
        session
        and session.mode == SessionMode.INTERVIEW
        and segment.is_final
    ):
        interview_suggestion_counters.setdefault(session_id, 0)
        interview_suggestion_counters[session_id] += 1

        # Detect single audio source after 3 final segments
        if (
            interview_suggestion_counters[session_id] == 3
            and session_id not in single_source_warned
        ):
            asyncio.create_task(_check_single_audio_source(session_id))

        if interview_suggestion_counters[session_id] % 5 == 0:
            asyncio.create_task(_generate_interview_suggestions(session_id))


async def _generate_rolling_summary(session_id: str) -> None:
    """Generate a rolling summary from recent transcript."""
    assert session_mgr and context_window and gemini_client

    try:
        transcript_text = session_mgr.get_recent_transcript_text(session_id)
        current_seq = len(session_mgr.get_transcript(session_id))

        summary = await context_window.update_summary(transcript_text, current_seq)

        # Broadcast summary update
        seq = ws_manager.next_sequence(session_id)
        update = SummaryUpdate(
            text=summary,
            covering_from=0,
            covering_to=current_seq,
        )
        msg = WSMessage.summary_update(session_id, seq, update)
        await ws_manager.broadcast(session_id, msg)

        # Save to Firestore
        if firestore_storage:
            await firestore_storage.save_summary(
                session_id, summary,
                covering_from=0, covering_to=current_seq,
            )

    except Exception:
        logger.exception("rolling_summary_error", session_id=session_id)


async def _check_single_audio_source(session_id: str) -> None:
    """Check if all final segments have the same speaker — warn about single audio source."""
    assert session_mgr

    segments = session_mgr.get_transcript(session_id)
    final_segments = [s for s in segments if s.is_final]
    if len(final_segments) < 3:
        return

    speakers = {s.speaker for s in final_segments}
    if len(speakers) <= 1:
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


async def _generate_interview_suggestions(session_id: str) -> None:
    """Generate interview follow-up question suggestions."""
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
            max_output_tokens=2048,
        )

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


async def _generate_final_summary(session_id: str) -> None:
    """Generate comprehensive final summary at end of session."""
    assert session_mgr and gemini_client and firestore_storage

    transcript_text = session_mgr.get_recent_transcript_text(session_id, max_segments=9999)
    if not transcript_text:
        return

    session = session_mgr.get_session(session_id)
    is_interview = session and session.mode == SessionMode.INTERVIEW

    try:
        if is_interview:
            from datetime import date
            prompt = INTERVIEW_REPORT_PROMPT.replace("{interview_date}", date.today().strftime("%d/%m/%Y"))
            docs = interview_documents.get(session_id, {})
            user_parts = []
            if docs.get("resume"):
                user_parts.append(f"## Currículo / CV do Candidato\n{docs['resume']}")
            if docs.get("jd"):
                user_parts.append(f"## Descrição da Vaga / Job Description\n{docs['jd']}")
            if docs.get("briefing"):
                user_parts.append(f"## Briefing Pré-Entrevista\n{docs['briefing']}")
            user_parts.append(f"## Transcrição Completa da Entrevista\n{transcript_text}")
            user_message = "\n\n".join(user_parts)
        else:
            prompt = FINAL_SUMMARY_PROMPT
            user_message = f"## Transcript\n{transcript_text}"

        summary = await gemini_client.generate(
            system_instruction=prompt,
            user_message=user_message,
            temperature=0.2,
            max_output_tokens=4096,
        )

        session_mgr.set_summary(session_id, summary)

        # Broadcast
        seq = ws_manager.next_sequence(session_id)
        transcript = session_mgr.get_transcript(session_id)
        update = SummaryUpdate(
            text=summary,
            is_final=True,
            covering_from=0,
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
                covering_from=0, covering_to=len(transcript),
                is_final=True,
            )

    except Exception:
        logger.exception("final_summary_error", session_id=session_id)
    finally:
        interview_documents.pop(session_id, None)
        speaker_correlators.pop(session_id, None)
        extension_tokens.pop(session_id, None)
        _clock_sync_timestamps.pop(session_id, None)


async def _stop_pipeline(session_id: str) -> None:
    """Stop the audio pipeline for a session."""
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
    interview_suggestion_counters.pop(session_id, None)
    single_source_warned.discard(session_id)


# --- REST Endpoints ---

@app.post("/api/sessions")
async def create_session(
    mode: str = "meeting",
    title: str = "",
    notice_given: bool = False,
):
    """Create a new session and start the audio pipeline."""
    assert settings and session_mgr and firestore_storage

    session_mode = SessionMode(mode)
    session = session_mgr.create_session(mode=session_mode, title=title)
    session.notice_given = notice_given

    # Save to Firestore
    await firestore_storage.save_session(session)

    # Start heartbeat
    await session_mgr.start_heartbeat(session.id)

    # Start audio pipeline
    task = asyncio.create_task(_run_audio_pipeline(session.id))
    pipeline_tasks[session.id] = [task]

    return {"session_id": session.id, "status": "active", "mode": mode}


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    """Stop a session and generate final summary."""
    assert session_mgr

    await _stop_pipeline(session_id)
    session = await session_mgr.stop_session(session_id)

    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Generate final summary async
    asyncio.create_task(_generate_final_summary(session_id))

    return {"session_id": session_id, "status": "completed"}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session everywhere: Firestore, GCS documents, and tombstone."""
    assert firestore_storage
    from backend.storage.deletion import delete_session_everywhere

    db = await firestore_storage._get_db()
    return await delete_session_everywhere(session_id, db, gcs_storage)


@app.post("/api/sessions/{session_id}/speakers")
async def update_speakers(session_id: str, speaker_map: dict[str, str]):
    """Update speaker label mapping."""
    assert session_mgr
    session_mgr.update_speaker_map(session_id, speaker_map)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/documents")
async def upload_document(
    session_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
):
    """Upload a resume or JD document for interview mode."""
    assert firestore_storage and gcs_storage

    data = await file.read()
    filename = file.filename or "document"

    try:
        text = parse_document(data, filename)
    except DocumentParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Store extracted text for interview context
    if session_id not in interview_documents:
        interview_documents[session_id] = {}
    interview_documents[session_id][doc_type] = text

    # Upload to GCS
    gcs_path = gcs_storage.upload_bytes(
        data,
        f"sessions/{session_id}/documents/{filename}",
        content_type=file.content_type or "application/octet-stream",
    )

    # Save metadata to Firestore
    await firestore_storage.save_document_metadata(
        session_id, doc_type, filename, text, gcs_path
    )

    return {"ok": True, "extracted_chars": len(text)}


@app.post("/api/sessions/{session_id}/context")
async def set_interview_context(session_id: str, body: SetContextRequest):
    """Set interview context text directly (e.g., pasted job description or briefing)."""
    allowed_types = {"resume", "jd", "briefing", "candidate_name"}
    if body.doc_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"doc_type must be one of {allowed_types}")
    if len(body.text) > 100_000:
        raise HTTPException(status_code=400, detail="text exceeds 100,000 character limit")
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
        data = await file.read()
        filename = file.filename or "document"
        try:
            cv_text = parse_document(data, filename)
        except DocumentParseError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Cap combined input
    combined = cv_text + jd_text
    if len(combined) > 30_000:
        logger.warning("analyze_input_truncated", original_len=len(combined))
        # Truncate CV first (JD is required, CV is supplementary)
        max_cv = 30_000 - len(jd_text)
        if max_cv > 0:
            cv_text = cv_text[:max_cv]
        else:
            cv_text = ""

    # Build user message
    parts = []
    if cv_text:
        parts.append(f"## Currículo / CV do Candidato\n{cv_text}")
    else:
        parts.append("## Currículo / CV do Candidato\n(Não fornecido)")
    parts.append(f"## Descrição da Vaga / Job Description\n{jd_text}")
    user_message = "\n\n".join(parts)

    # Call Gemini with timeout and semaphore
    async with _analyze_semaphore:
        try:
            briefing = await asyncio.wait_for(
                gemini_client.generate(
                    system_instruction=PRE_INTERVIEW_ANALYSIS_PROMPT,
                    user_message=user_message,
                    temperature=0.3,
                    max_output_tokens=2048,
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
    sessions = await firestore_storage.list_sessions()
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details."""
    assert session_mgr
    session = session_mgr.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump()


@app.get("/api/sessions/{session_id}/transcript")
async def get_transcript(session_id: str):
    """Get session transcript."""
    assert session_mgr
    segments = session_mgr.get_transcript(session_id)
    return {"segments": [s.model_dump() for s in segments]}


@app.get("/api/sessions/{session_id}/transcript/download")
async def download_transcript(session_id: str):
    """Download the full transcript as a plain text file."""
    assert session_mgr
    segments = session_mgr.get_transcript(session_id)
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

def _validate_extension_token(session_id: str, authorization: str | None) -> None:
    """Validate the extension session token."""
    expected = extension_tokens.get(session_id)
    if not expected:
        raise HTTPException(status_code=403, detail="No extension linked to this session")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    token = authorization[len("Bearer "):]
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid session token")


@app.post("/api/sessions/{session_id}/extension-link")
async def extension_link(session_id: str):
    """Link Chrome extension to a session. Returns a session token."""
    assert session_mgr
    session = session_mgr.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Session is not active")

    token = secrets.token_urlsafe(32)
    extension_tokens[session_id] = token

    # Create correlator for this session
    if session_id not in speaker_correlators:
        speaker_correlators[session_id] = SpeakerCorrelator(session_id=session_id)

    logger.info("extension_linked", session_id=session_id)
    return {"token": token, "session_id": session_id}


@app.post("/api/sessions/{session_id}/clock-sync")
async def clock_sync(
    session_id: str,
    body: ClockSyncRequest,
    authorization: str | None = Header(None),
):
    """NTP-style clock synchronization for Chrome extension."""
    assert session_mgr
    _validate_extension_token(session_id, authorization)

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
    authorization: str | None = Header(None),
):
    """Receive active-speaker events from Chrome extension."""
    assert session_mgr and firestore_storage
    _validate_extension_token(session_id, authorization)

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
                await firestore_storage.save_transcript_segment(session_id, seg)
            except (StopIteration, Exception):
                logger.exception("firestore_relabel_error", segment_id=update.segment_id)

    return {"ok": True, "relabeled_count": len(relabel_updates)}


@app.post("/api/sessions/{session_id}/participants")
async def update_participants(
    session_id: str,
    body: ParticipantsList,
    authorization: str | None = Header(None),
):
    """Receive participant list from Chrome extension."""
    assert session_mgr
    _validate_extension_token(session_id, authorization)

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
    authorization: str | None = Header(None),
):
    """Receive health heartbeat from Chrome extension."""
    _validate_extension_token(session_id, authorization)

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
    # Check for last_seq query param for reconnection
    last_seq = 0
    query_params = websocket.query_params
    if "last_seq" in query_params:
        try:
            last_seq = int(query_params["last_seq"])
        except ValueError:
            pass

    await ws_manager.connect(websocket, session_id, last_seq=last_seq)

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
