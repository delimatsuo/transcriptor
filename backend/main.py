"""FastAPI application — T.A.R.S. backend server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware

from backend.audio.buffer import AudioBuffer
from backend.audio.capture import AudioCapture
from backend.config import Settings, get_settings
from backend.documents.parser import parse_document, DocumentParseError
from backend.llm.context_window import ContextWindowManager
from backend.llm.gemini import GeminiClient
from backend.llm.interview_prompts import INTERVIEW_SYSTEM_PROMPT, build_interview_user_message
from backend.llm.meeting_prompts import FINAL_SUMMARY_PROMPT
from backend.schemas.models import (
    ActionItem,
    ConnectionHealth,
    ConnectionStatusPayload,
    ErrorPayload,
    ErrorSeverity,
    SessionMode,
    SessionStatus,
    Suggestion,
    SummaryUpdate,
    TranscriptDelta,
    TranscriptSegment,
    WSMessage,
    WSMessageType,
)
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
    allow_origins=["http://localhost:3000"],
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

    # Store in session manager
    session_mgr.add_transcript_segment(session_id, segment)

    # Broadcast via WebSocket
    seq = ws_manager.next_sequence(session_id)
    msg = WSMessage.transcript_delta(session_id, seq, segment)
    await ws_manager.broadcast(session_id, msg)

    # Persist final segments to Firestore
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

    # Interview mode: generate suggestions on speaker turn
    session = session_mgr.get_session(session_id)
    if (
        session
        and session.mode == SessionMode.INTERVIEW
        and segment.is_final
        and session_id in interview_documents
    ):
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


async def _generate_interview_suggestions(session_id: str) -> None:
    """Generate interview follow-up question suggestions."""
    assert session_mgr and gemini_client

    docs = interview_documents.get(session_id, {})
    if not docs:
        return

    try:
        recent = session_mgr.get_recent_transcript_text(session_id, max_segments=10)

        user_msg = build_interview_user_message(
            resume_text=docs.get("resume", ""),
            jd_text=docs.get("jd", ""),
            recent_transcript=recent,
        )

        response = await gemini_client.generate(
            system_instruction=INTERVIEW_SYSTEM_PROMPT,
            user_message=user_msg,
            temperature=0.4,
            max_output_tokens=512,
        )

        # Parse questions from response
        questions = []
        for line in response.split("\n"):
            line = line.strip()
            if line and (line[0].isdigit() and "." in line[:3]):
                questions.append(line.split(".", 1)[-1].strip())

        if questions:
            seq = ws_manager.next_sequence(session_id)
            suggestion = Suggestion(questions=questions, context="Based on candidate's latest response")
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

    try:
        summary = await gemini_client.generate(
            system_instruction=FINAL_SUMMARY_PROMPT,
            user_message=f"## Transcript\n{transcript_text}",
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


# --- REST Endpoints ---

@app.post("/api/sessions")
async def create_session(
    mode: str = "meeting",
    title: str = "",
):
    """Create a new session and start the audio pipeline."""
    assert settings and session_mgr and firestore_storage

    session_mode = SessionMode(mode)
    session = session_mgr.create_session(mode=session_mode, title=title)

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
        return {"error": "Session not found"}, 404

    # Generate final summary async
    asyncio.create_task(_generate_final_summary(session_id))

    return {"session_id": session_id, "status": "completed"}


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
        return {"error": str(e)}, 400

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
        return {"error": "Session not found"}, 404
    return session.model_dump()


@app.get("/api/sessions/{session_id}/transcript")
async def get_transcript(session_id: str):
    """Get session transcript."""
    assert session_mgr
    segments = session_mgr.get_transcript(session_id)
    return {"segments": [s.model_dump() for s in segments]}


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
