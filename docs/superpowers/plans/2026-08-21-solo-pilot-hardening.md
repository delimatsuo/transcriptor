# Solo Pilot Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the native capture spine trustworthy for one real solo interview on the owner's Mac: candidate channel proven live, channels split (browser=self, companion=candidate), stream-key auth, reconnect with honest gaps, working health UI, loud permission failures, and corrected evidence docs.

**Architecture:** Backend FastAPI gateway (`backend/main.py`) gains per-session stream-key auth and session-lifetime StreamManagers; the Swift companion CLI (`companion/native-macos`) becomes system-audio-only by default with a reconnecting buffered sink; the Next.js cockpit sends the stream key and shows a copy-ready companion command; false launch docs get retraction headers; a live proof script exercises the real backend + real ScreenCaptureKit + real Google STT using macOS `say`.

**Tech Stack:** Python 3.12 (root `.venv`), FastAPI/Starlette, pytest; Swift 5.9 SwiftPM (`swift test`, macOS 13+); Next.js/TypeScript (`npm test` in `frontend/`); .NET 8 (`dotnet test`); Google STT v2.

**Spec:** `docs/superpowers/specs/2026-08-21-solo-pilot-hardening-design.md` (D1–D6, S1–S7 referenced below).

## Global Constraints

- Branch: `codex/solo-pilot-hardening` (already created; spec committed at f8761a8). Repo: `/Volumes/Extreme Pro/MYPROJECTS/Transcriptor` — **path contains a space; always quote it.**
- NEVER stage/modify/delete these untracked files: `AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`. NEVER run `git add -A` or `git add .` — commit with explicit pathspecs only (`git add -- <paths>` then `git commit -- <paths>`).
- Backend tests: run from repo root with `.venv/bin/python -m pytest backend/tests -x -q`. Swift: `cd companion/native-macos && swift test`. Frontend: `cd frontend && npm test`. .NET: `cd companion/native-windows && dotnet test`.
- Never print Google credentials/tokens. ADC validity check pattern: `gcloud auth application-default print-access-token >/dev/null 2>&1; echo $?`.
- All user-facing copy in Brazilian Portuguese (existing style: "Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema").
- Evidence honesty (spec D6): every evidence/verification doc states machine, commit, what actually ran, and an explicit claim ceiling. No doc may claim more than its script proved.
- The wire protocol (4-byte big-endian header length + JSON header + Int16 LE 16 kHz mono PCM; header keys `session_id, source, sequence, first_sample, captured_at_ms, sample_rate, channel_count, duration_ms`) must NOT change — three clients speak it.
- Full backend suite must stay green after every task (existing baseline ~269 passed).

---

### Task 1: Backend — per-session stream key, gateway auth

**Files:**
- Modify: `backend/main.py` (module state near line 119; `create_session` ~line 1317; `native_stream_endpoint` line 2333; `_stop_pipeline` line 1278)
- Test: `backend/tests/test_native_stream_endpoint.py` (extend; see existing `FakeNativeWebSocket` at top of file)

**Interfaces:**
- Produces: module dict `stream_keys: dict[str, str]` (session_id → key); `create_session` response gains `"stream_key": <str>`; `native_stream_endpoint` rejects (close code 1008, no accept) when the `stream_key` query param is absent/wrong or the session is unknown/not ACTIVE; `_stop_pipeline` pops `stream_keys[session_id]`.
- Consumes: existing `session_mgr.get_session(session_id)` (returns `Session | None` with `.status`), `SessionStatus.ACTIVE`, `secrets` (already imported).

- [ ] **Step 1: Write failing tests** — add to `backend/tests/test_native_stream_endpoint.py`. Extend `FakeNativeWebSocket` with `query_params: dict` (constructor kwarg, default `{}`), `closed_code: int | None = None`, and `async def close(self, code=1000): self.closed_code = code`. New tests:

```python
class _FakeSession:
    def __init__(self, status="active"):
        from backend.schemas.models import SessionStatus
        self.status = SessionStatus(status)

def _install_session(monkeypatch, session_id, key="k" * 43, status="active"):
    fake_mgr = type("M", (), {"get_session": lambda self, sid: _FakeSession(status) if sid == session_id else None})()
    monkeypatch.setattr(main, "session_mgr", fake_mgr)
    main.stream_keys[session_id] = key
    return key

def test_native_stream_rejects_missing_key(monkeypatch):
    _install_session(monkeypatch, "s1")
    ws = FakeNativeWebSocket([], )
    ws.query_params = {}
    asyncio.run(main.native_stream_endpoint(ws, "s1"))
    assert ws.accepted is False
    assert ws.closed_code == 1008

def test_native_stream_rejects_wrong_key(monkeypatch):
    _install_session(monkeypatch, "s1", key="rightkey")
    ws = FakeNativeWebSocket([])
    ws.query_params = {"stream_key": "wrongkey"}
    asyncio.run(main.native_stream_endpoint(ws, "s1"))
    assert ws.accepted is False and ws.closed_code == 1008

def test_native_stream_rejects_unknown_session(monkeypatch):
    monkeypatch.setattr(main, "session_mgr", type("M", (), {"get_session": lambda self, sid: None})())
    main.stream_keys.pop("ghost", None)
    ws = FakeNativeWebSocket([])
    ws.query_params = {"stream_key": "anything"}
    asyncio.run(main.native_stream_endpoint(ws, "ghost"))
    assert ws.accepted is False and ws.closed_code == 1008

def test_native_stream_accepts_valid_key(monkeypatch):
    key = _install_session(monkeypatch, "s1")
    ws = FakeNativeWebSocket([{"text": json.dumps({"type": "ping"})}])
    ws.query_params = {"stream_key": key}
    asyncio.run(main.native_stream_endpoint(ws, "s1"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]
```

Also update every EXISTING test in this file that calls `main.native_stream_endpoint` to install a session + key the same way (helper `_install_session` + `ws.query_params = {"stream_key": key}`), so the suite stays green after auth lands. Use `secrets.compare_digest` semantics in mind for the wrong-key test (any mismatch closes).

- [ ] **Step 2: Run tests, verify the new ones FAIL** — `.venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -x -q`. Expected: new tests fail (endpoint accepts unconditionally today).

- [ ] **Step 3: Implement** in `backend/main.py`:

```python
# near line 119, alongside stream_managers:
stream_keys: dict[str, str] = {}
```

In `create_session` (after `await firestore_storage.save_session(session)`):

```python
    stream_key = secrets.token_urlsafe(32)
    stream_keys[session.id] = stream_key
```

and add `"stream_key": stream_key,` to the return dict.

At the top of `native_stream_endpoint` (replacing the unconditional accept):

```python
    presented = websocket.query_params.get("stream_key", "")
    expected = stream_keys.get(session_id)
    session = session_mgr.get_session(session_id) if session_mgr else None
    if (
        not expected
        or not presented
        or not secrets.compare_digest(presented, expected)
        or session is None
        or session.status != SessionStatus.ACTIVE
    ):
        logger.warning("native_stream_rejected", session_id=session_id)
        await websocket.close(code=1008)
        return
    await websocket.accept()
```

In `_stop_pipeline`, add `stream_keys.pop(session_id, None)` next to the other per-session cleanups. Confirm `SessionStatus` is already imported in `main.py` (it is used elsewhere; if not, add to the existing schemas import).

- [ ] **Step 4: Run full backend suite** — `.venv/bin/python -m pytest backend/tests -x -q`. Expected: PASS (including pre-existing native-stream tests you updated).

- [ ] **Step 5: Commit**

```bash
git add -- backend/main.py backend/tests/test_native_stream_endpoint.py
git commit -m "feat(gateway): require per-session stream key on native audio WebSocket" -- backend/main.py backend/tests/test_native_stream_endpoint.py
```

---

### Task 2: Backend — StreamManagers survive reconnects; session-scoped cleanup

**Files:**
- Modify: `backend/main.py` (`native_stream_endpoint` body; `_stop_pipeline` line 1278)
- Test: `backend/tests/test_native_stream_endpoint.py`

**Interfaces:**
- Produces: module dict `native_stream_managers: dict[str, dict[str, StreamManager]]` (session_id → source_label → SM) and module `native_sm_lock = asyncio.Lock()`; the endpoint's `finally` no longer stops SMs nor pops `stream_managers`; `_stop_pipeline` stops native SMs (drain) and pops both dicts.
- Consumes: Task 1's auth preamble unchanged.

- [ ] **Step 1: Write failing tests**:

```python
@patch("backend.main.StreamManager")
def test_stream_managers_survive_reconnect(mock_sm_cls, monkeypatch):
    mock_sm = AsyncMock()
    mock_sm_cls.return_value = mock_sm
    key = _install_session(monkeypatch, "s-reconnect")
    header = {"session_id": "s-reconnect", "source": "system_audio", "sequence": 1,
              "first_sample": 0, "captured_at_ms": 0, "sample_rate": 16000,
              "channel_count": 1, "duration_ms": 50}
    for _ in range(2):  # two sequential connections = drop + reconnect
        ws = FakeNativeWebSocket([{"bytes": _encode_native_packet(header)}])
        ws.query_params = {"stream_key": key}
        asyncio.run(main.native_stream_endpoint(ws, "s-reconnect"))
    assert mock_sm_cls.call_count == 1          # one SM per source per SESSION, not per connection
    assert mock_sm.send_audio.await_count == 2  # both connections fed it
    mock_sm.stop.assert_not_awaited()           # disconnect must not drain/stop
    assert "s-reconnect" in main.stream_managers  # registry intact for drain accounting

def test_stop_pipeline_stops_native_sms(monkeypatch):
    sm = AsyncMock()
    sm.drain_completed = True
    main.native_stream_managers["s-stop"] = {"Candidato": sm}
    main.stream_managers["s-stop"] = [sm]
    main.stream_keys["s-stop"] = "k"
    result = asyncio.run(main._stop_pipeline("s-stop"))
    assert result is True
    sm.stop.assert_awaited_once()
    assert "s-stop" not in main.native_stream_managers
    assert "s-stop" not in main.stream_keys
```

Add cleanup between tests: a fixture that clears `main.native_stream_managers`, `main.stream_managers`, `main.stream_keys` (extend the existing autouse fixture).

- [ ] **Step 2: Run, verify FAIL** — reconnect test fails today (SM created per connection, stopped in finally; registry popped).

- [ ] **Step 3: Implement** — in `backend/main.py`:

```python
# near stream_keys:
native_stream_managers: dict[str, dict[str, StreamManager]] = {}
native_sm_lock = asyncio.Lock()
```

Inside `native_stream_endpoint`, replace the per-connection `sms` dict and `get_or_create_sm` with:

```python
    async def get_or_create_sm(source_label: str) -> StreamManager:
        async with native_sm_lock:
            per_session = native_stream_managers.setdefault(session_id, {})
            if source_label not in per_session:
                sm = StreamManager(
                    settings=app_settings,
                    on_transcript=lambda seg: _on_transcript(session_id, seg),
                    source_label=source_label,
                )
                await sm.start()
                per_session[source_label] = sm
                stream_managers.setdefault(session_id, []).append(sm)
            return per_session[source_label]
```

Replace the `finally` block: remove the `sm.stop()` loop and the `stream_managers.pop(...)`; keep only a disconnect log (Task 3 adds a health broadcast here). In `_stop_pipeline`, before the drain verdict at the end:

```python
    native_sms = native_stream_managers.pop(session_id, {})
    for sm in native_sms.values():
        try:
            await sm.stop()
        except Exception:
            logger.exception("native_sm_stop_error", session_id=session_id)
```

(Keep the existing `managers` list read at the top — it already includes these SMs because creation appended them to `stream_managers[session_id]`; the drain verdict line stays unchanged. Note `_stop_pipeline` reads `managers` before stopping native SMs — move the `managers = list(...)` read AFTER the native stop loop so `drain_completed` reflects post-stop state.)

- [ ] **Step 4: Run full backend suite** — expected PASS.
- [ ] **Step 5: Commit** (pathspec: same two files) — `fix(gateway): session-lifetime StreamManagers; companion reconnect no longer kills STT drain accounting`.

---

### Task 3: Backend — companion_health + coverage_gap emission, stall watchdog, copy fix

**Files:**
- Modify: `backend/schemas/models.py` (WSMessageType line 26; payload models near line 99; WSMessage constructors near line 240)
- Modify: `backend/main.py` (`native_stream_endpoint`; single-source watchdog copy ~line 778-802)
- Test: `backend/tests/test_native_stream_endpoint.py`

**Interfaces:**
- Produces (models, mirroring `frontend/src/types/ws.ts:192-213` exactly): `WSMessageType.COMPANION_HEALTH = "companion_health"`, `WSMessageType.COVERAGE_GAP = "coverage_gap"`; Pydantic models `SourceHealthReport {microphone: str = "unknown", system_audio: str = "unknown"}`, `CompanionHealthPayload {physical_capture: str = "unknown", sources: SourceHealthReport, message: str | None = None}`, `CoverageGapSegment {id: str, source: str, start_ms: float, end_ms: float | None = None, reason: str = "unknown"}`, `CoverageGapPayload {gap: CoverageGapSegment}`; constructors `WSMessage.companion_health_msg(session_id, seq, payload)` and `WSMessage.coverage_gap_msg(session_id, seq, payload)` following the `connection_status_msg` pattern.
- Produces (behavior): on WS accept → broadcast companion_health `physical_capture="active"`, both sources `"unknown"`; on first frame per source → that source `"healthy"`; on `gap` text message → rebroadcast as coverage_gap with reason mapping AND set that source's health (`permission_denied→"permission_missing"`, `device_lost→"device_unavailable"`, `overrun|buffer_exhaustion→"overflow"`, else unchanged); on disconnect (finally) → `physical_capture="stopped"`, sources `"unknown"`; stall watchdog task: any source that has produced a frame but none for >10 s while connected → `"device_unavailable"` (recovers to `"healthy"` on next frame). `start_ms` for gaps = `first_sample / 16.0` (16 kHz).
- Consumes: `ws_manager.broadcast(session_id, msg)` + `ws_manager.next_sequence(session_id)` (existing pattern, e.g. main.py:2261-2268).

- [ ] **Step 1: Write failing tests** (patch `main.ws_manager` with an object holding `broadcast=AsyncMock()` and `next_sequence=lambda sid: 1`):

```python
def _health_msgs(fake_ws_manager):
    return [c.args[1] for c in fake_ws_manager.broadcast.await_args_list
            if c.args[1].type.value == "companion_health"]

def test_health_emitted_on_connect_first_frame_and_disconnect(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    key = _install_session(monkeypatch, "s-h")
    header = {"session_id": "s-h", "source": "system_audio", "sequence": 1, "first_sample": 0,
              "captured_at_ms": 0, "sample_rate": 16000, "channel_count": 1, "duration_ms": 50}
    with patch("backend.main.StreamManager") as sm_cls:
        sm_cls.return_value = AsyncMock()
        ws = FakeNativeWebSocket([{"bytes": _encode_native_packet(header)}])
        ws.query_params = {"stream_key": key}
        asyncio.run(main.native_stream_endpoint(ws, "s-h"))
    msgs = _health_msgs(fake_wsm)
    assert msgs[0].payload["physical_capture"] == "active"
    assert any(m.payload["sources"]["system_audio"] == "healthy" for m in msgs)
    assert msgs[-1].payload["physical_capture"] == "stopped"

def test_gap_rebroadcast_as_coverage_gap(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    key = _install_session(monkeypatch, "s-g")
    gap = {"type": "gap", "source": "system_audio", "reason": "device_lost", "first_sample": 16000}
    ws = FakeNativeWebSocket([{"text": json.dumps(gap)}])
    ws.query_params = {"stream_key": key}
    asyncio.run(main.native_stream_endpoint(ws, "s-g"))
    gaps = [c.args[1] for c in fake_wsm.broadcast.await_args_list if c.args[1].type.value == "coverage_gap"]
    assert len(gaps) == 1
    assert gaps[0].payload["gap"]["reason"] == "device_lost"
    assert gaps[0].payload["gap"]["start_ms"] == 1000.0
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** models + constructors in `schemas/models.py` per the Interfaces block (copy field names/values exactly from `frontend/src/types/ws.ts` — the frontend already parses these in `useWebSocket.ts:150-166`). In `native_stream_endpoint`: a local `health = CompanionHealthPayload(physical_capture="active", sources=SourceHealthReport())` + `async def emit_health(): await ws_manager.broadcast(session_id, WSMessage.companion_health_msg(session_id, ws_manager.next_sequence(session_id), health))` called on accept, on per-source first frame / gap-driven state change / stall transitions, and in `finally` with `physical_capture="stopped"`, sources reset to `"unknown"`. Track `last_frame_at: dict[str, float]` (`time.monotonic()`); start `stall_task = asyncio.create_task(...)` after accept looping every 5 s applying the >10 s rule; cancel it in `finally`. Wrap all emission in try/except so health can never break audio ingestion. Then fix the stale copy in the single-source watchdog (~line 778-802): replace BlackHole-specific wording with channel wording, e.g. `"Apenas um canal de áudio está produzindo transcrição. Verifique se o companion (Áudio do Sistema) está em execução e com permissão concedida."` — grep that block for `BlackHole` and update only user-facing strings.
- [ ] **Step 4: Run full backend suite** — PASS. Also run `cd frontend && npm test` (type mirror sanity; no frontend change expected yet).
- [ ] **Step 5: Commit** — pathspec `backend/main.py backend/schemas/models.py backend/tests/test_native_stream_endpoint.py`; message `feat(gateway): emit companion_health + coverage_gap to session WS; stall watchdog`.

---

### Task 4: Frontend — stream key wiring + companion launch command

**Files:**
- Modify: `frontend/src/hooks/useBrowserAudioCapture.ts` (lines 11-13, 300-312)
- Create: `frontend/src/lib/streamUrl.ts`, `frontend/src/lib/streamUrl.test.ts`, `frontend/src/components/CompanionCommand.tsx`
- Modify: `frontend/src/app/page.tsx` (session create ~line 90; render area near line 261), `frontend/src/lib/transcript.ts` (line 6 comment)

**Interfaces:**
- Consumes: `create_session` response `stream_key` (Task 1).
- Produces: `buildStreamUrl(base: string, sessionId: string, streamKey?: string): string` (appends `?stream_key=` URL-encoded when provided); `startStreaming(sessionId: string, streamKey?: string)` (signature change; existing caller updated); `<CompanionCommand sessionId={...} streamKey={...} />` rendering a copyable one-liner `./tars-companion --session-id <id> --stream-key <key>` with a "Copiar" button (`navigator.clipboard.writeText`), labeled "Canal do Candidato — execute o companion:" — rendered only for interview mode while a session is active.

- [ ] **Step 1: Write failing test** `frontend/src/lib/streamUrl.test.ts`:

```typescript
import { buildStreamUrl } from "./streamUrl";

describe("buildStreamUrl", () => {
  it("appends encoded stream key", () => {
    expect(buildStreamUrl("ws://h/api/stream/native", "s1", "k/+=")).toBe(
      "ws://h/api/stream/native/s1?stream_key=k%2F%2B%3D",
    );
  });
  it("omits query without key", () => {
    expect(buildStreamUrl("ws://h/api/stream/native", "s1")).toBe("ws://h/api/stream/native/s1");
  });
});
```

- [ ] **Step 2: Run `cd frontend && npm test` — verify FAIL (module missing).**
- [ ] **Step 3: Implement** `streamUrl.ts`; use it in `startStreaming` (`const wsUrl = buildStreamUrl(WS_STREAM_BASE, sessionId, streamKey);`); thread `stream_key` from the session-create response in `page.tsx` into component state, pass to `audioCapture.startStreaming(id, streamKey)` and `<CompanionCommand>`; write `CompanionCommand.tsx` (client component, monospace block + copy button, Tailwind classes consistent with `CaptureSourceStatus.tsx`); fix the stale BlackHole doc comment in `transcript.ts` line 6 to describe the current dual-stream sources (browser microphone / companion system audio).
- [ ] **Step 4: Run `npm test` and `npm run build` in `frontend/` — both must pass** (build catches the page.tsx typing).
- [ ] **Step 5: Commit** — pathspec the five files; `feat(ui): stream-key auth for browser audio + copyable companion launch command`.

---

### Task 5: Companion (Swift) — `--sources` default system_audio, `--stream-key`, permission preflight, fail-loud

**Files:**
- Create: `companion/native-macos/Sources/TarsNativeCompanion/CompanionOptions.swift`
- Create: `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionOptionsTests.swift`
- Modify: `companion/native-macos/Sources/TarsCompanionCLI/main.swift`

**Interfaces:**
- Produces:

```swift
public struct CompanionOptions: Equatable, Sendable {
    public enum Sources: String, Sendable { case systemAudio = "system_audio", microphone, both }
    public var sessionID: String = "default"
    public var gatewayBase: String = "ws://127.0.0.1:8000/api/stream/native"
    public var streamKey: String = ""
    public var sources: Sources = .systemAudio
    public static func parse(_ args: [String]) throws -> CompanionOptions
    public func gatewayURL() throws -> URL   // appends /<session> and ?stream_key= (percent-encoded) when non-empty
}
```

`parse` accepts `--session-id`, `--gateway`, `--stream-key`, `--sources system_audio|microphone|both`; unknown `--sources` value throws `CompanionError.invalid`. `--token` remains an accepted alias for `--stream-key` (prints a deprecation note to stderr at use).
- Consumes: `CompanionError.invalid(String)` (exists in TarsNativeCompanion).

- [ ] **Step 1: Write failing tests**:

```swift
import XCTest
@testable import TarsNativeCompanion

final class CompanionOptionsTests: XCTestCase {
    func testDefaultsToSystemAudioOnly() throws {
        let o = try CompanionOptions.parse(["bin"])
        XCTAssertEqual(o.sources, .systemAudio)
    }
    func testParsesAllFlags() throws {
        let o = try CompanionOptions.parse(["bin", "--session-id", "s1", "--stream-key", "k1",
                                            "--sources", "both", "--gateway", "ws://x/api/stream/native"])
        XCTAssertEqual(o.sessionID, "s1")
        XCTAssertEqual(o.streamKey, "k1")
        XCTAssertEqual(o.sources, .both)
    }
    func testRejectsUnknownSources() {
        XCTAssertThrowsError(try CompanionOptions.parse(["bin", "--sources", "tab_audio"]))
    }
    func testURLIncludesEncodedStreamKey() throws {
        var o = try CompanionOptions.parse(["bin", "--session-id", "s1", "--stream-key", "k/+="])
        XCTAssertEqual(try o.gatewayURL().absoluteString,
                       "ws://127.0.0.1:8000/api/stream/native/s1?stream_key=k%2F%2B%3D")
        o.streamKey = ""
        XCTAssertEqual(try o.gatewayURL().absoluteString, "ws://127.0.0.1:8000/api/stream/native/s1")
    }
}
```

- [ ] **Step 2: `cd companion/native-macos && swift test` — verify FAIL.**
- [ ] **Step 3: Implement** `CompanionOptions.swift` (pure parsing, no I/O — testable). Rewrite `main.swift`'s `CompanionApp.run()` to use it:
  - `import CoreGraphics`. If `options.sources != .microphone`: preflight `if !CGPreflightScreenCaptureAccess() { _ = CGRequestScreenCaptureAccess(); }` then re-check; if still false → print `"❌ Permissão ausente. Habilite em: Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema → habilite o seu app de Terminal, e rode novamente."` and `exit(2)` (spec S6).
  - Start only the sources selected. If system audio is selected and `sysSource.start()` throws → print the pt-BR hint and `exit(3)` (no warn-and-continue).
  - Zero-frame advisory: 15 s after successful start, if the sink's `framesSent(for: .systemAudio) == 0`, print `"⚠ Nenhum frame de áudio do sistema em 15s — verifique se há áudio tocando e se a permissão foi concedida ao app de Terminal correto."` (keep running). Requires the frame counter from Task 6's sink — if executing this task first, add a minimal `framesSent(for:)` counter to the existing `WebSocketAudioSink` and carry it into Task 6.
  - Banner: print the active sources; drop the "dual-channel" wording when running system-audio-only.
- [ ] **Step 4: `swift test` PASS and `swift build` succeeds.**
- [ ] **Step 5: Commit** — pathspec the three files; `feat(companion): system-audio-only default, stream-key auth, loud TCC preflight`.

---

### Task 6: Companion (Swift) — reconnecting buffered sink with honest gaps

**Files:**
- Create: `companion/native-macos/Sources/TarsNativeCompanion/ReconnectingAudioSink.swift`
- Create: `companion/native-macos/Tests/TarsNativeCompanionTests/ReconnectingAudioSinkTests.swift`
- Modify: `companion/native-macos/Sources/TarsCompanionCLI/main.swift` (replace inline `WebSocketAudioSink` usage; the inline class may be deleted once unused)

**Interfaces:**
- Produces:

```swift
public protocol AudioStreamTransport: Sendable {
    func connect() async throws
    func send(_ data: Data) async throws
    func sendText(_ text: String) async throws
    func cancel()
}

public final class ReconnectingAudioSink: CaptureFrameSink, @unchecked Sendable {
    public init(
        sessionID: String,
        transportFactory: @escaping @Sendable () -> AudioStreamTransport,
        bufferCapacityFrames: Int = 600,               // ~30 s of 50 ms frames
        reconnectDelaysSeconds: [Double] = [1, 2, 4, 8, 16, 30],
        sleep: @escaping @Sendable (Double) async -> Void = { try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) }
    )
    public func start()                                 // spawns the sender task
    public func stop() async
    public func framesSent(for source: AudioSource) -> Int
    public func receive(_ frame: AudioFrame) async throws    // encodes (same packet layout as today: 4-byte BE header len + JSON + PCM) and enqueues
    public func receiveGap(_ gap: CoverageGap) async throws  // enqueues gap JSON text
}
```

Behavior contract (all tested): frames enqueue into a bounded FIFO of `(firstSample: UInt64, source: AudioSource, packet: Data)`; a single sender task dequeues in order — on send failure it re-queues the frame at the head, calls `transportFactory()` → `connect()` after the next backoff delay (cycle through `reconnectDelaysSeconds`, clamp at last, reset index on success), and resumes; when the buffer is full the OLDEST frame is dropped and a drop-run is recorded; after the next successful send, one gap text message `{"type":"gap","source":<source>,"reason":"buffer_exhaustion","first_sample":<first dropped frame's firstSample>}` is emitted per drop-run. No gap is emitted when nothing was dropped (replay covered the outage). Header JSON uses the exact key set listed in Global Constraints.
- Consumes: `AudioFrame` (fields `identity.source.rawValue`, `identity.sampleRate`, `identity.channelCount`, `sequence`, `firstSample`, `capturedAtMs`, `durationMs`, `payload.copyData()` — copy the encoding verbatim from the current `WebSocketAudioSink.receive` in `main.swift:30-62`), `CoverageGap` (fields `identity.source.rawValue`, `reason.rawValue`, `firstSample`).
- `main.swift` gains a concrete `URLSessionWebSocketTransport: AudioStreamTransport` (wraps one `URLSessionWebSocketTask` per `connect()`; `send` wraps `webSocketTask.send(.data(...))` in a checked continuation; `connect()` performs `resume()` then a ping round-trip `sendPing`/continuation to confirm liveness).

- [ ] **Step 1: Write failing tests** with a scripted mock transport:

```swift
final class MockTransport: AudioStreamTransport, @unchecked Sendable {
    // failSendsUntil: number of send() calls that throw before succeeding forever
    // connectFailures: number of connect() calls that throw first
    // records every successfully sent Data / text in order
}

func testFramesBufferedDuringOutageAreReplayedInOrder() async throws { /* transport fails 3 sends, then succeeds; enqueue frames seq 1...5; assert sent packets decode to sequences [1,2,3,4,5] with no loss */ }
func testOverflowDropsOldestAndEmitsSingleGapPerRun() async throws { /* capacity 3; push 5 frames while transport dead; reconnect; assert frames 3,4,5 delivered and exactly one gap text with first_sample of frame 1 and reason buffer_exhaustion */ }
func testBackoffSequenceAndResetOnSuccess() async throws { /* capture sleep() delays through 3 connect failures → [1,2,4]; success; fail again → next delay is 1 */ }
func testFramesSentCounterPerSource() async throws { /* 2 system frames + 1 mic frame delivered → framesSent(.systemAudio)==2 */ }
```

Write these as real tests (the comments above describe the assertions to implement — write full bodies; use short arrays and `await` on deterministic hooks, never wall-clock sleeps: inject `sleep` that records the delay and returns immediately).

- [ ] **Step 2: `swift test` — verify FAIL.**
- [ ] **Step 3: Implement `ReconnectingAudioSink`** (actor-style with an internal `NSLock`-guarded deque or an `actor` — match the codebase's existing `@unchecked Sendable + NSLock` style, e.g. `ScreenCaptureKitSystemAudioSource`). Then rewrite `main.swift` to construct `ReconnectingAudioSink(sessionID:transportFactory:)` with `URLSessionWebSocketTransport` and delete the now-unused inline `WebSocketAudioSink` class.
- [ ] **Step 4: `swift test` PASS; `swift build -c release` succeeds.**
- [ ] **Step 5: Commit** — pathspec the three files; `feat(companion): reconnecting buffered sink — outages replay, real losses become gap reports`.

---

### Task 7: Windows CLI honesty

**Files:**
- Modify: `companion/native-windows/src/TarsCompanionCLI/Program.cs`
- Test: `companion/native-windows/tests/TarsNativeCompanion.Tests/` (add `ProgramModeTests.cs`; confirm exact test project dir with `ls companion/native-windows/tests` first and place the file beside the existing test classes)

**Interfaces:**
- Produces: `public static class CaptureModeGate { public static int Validate(bool simulate) }` in `Program.cs` (same namespace) returning `0` when `simulate` is true and `2` otherwise; `Main` calls it before connecting and on `2` prints: `"ERRO: A captura WASAPI real ainda NÃO está implementada neste companion. Este binário só funciona com --simulate (tom de teste). Não use em entrevistas reais."` and returns 2. Banner line 57's non-simulate branch changes to `"NÃO IMPLEMENTADO — apenas --simulate disponível"`.

- [ ] **Step 1: Write failing test**:

```csharp
using TarsCompanionCLI;
using Xunit;

public class ProgramModeTests
{
    [Fact]
    public void NonSimulateModeIsRefused() => Assert.Equal(2, CaptureModeGate.Validate(simulate: false));

    [Fact]
    public void SimulateModeIsAllowed() => Assert.Equal(0, CaptureModeGate.Validate(simulate: true));
}
```

(Match the existing test framework — inspect an existing test file first; if it's xUnit keep as-is, if NUnit/MSTest adapt attributes. If the test project does not reference the CLI project, add the `ProjectReference` to the test `.csproj`.)

- [ ] **Step 2: `cd companion/native-windows && dotnet test` — verify FAIL/compile error.**
- [ ] **Step 3: Implement** the gate + `Main` early-return + banner text.
- [ ] **Step 4: `dotnet test` PASS. Also verify by running: `dotnet run --project src/TarsCompanionCLI -- --session-id x` → exit code 2 with the pt-BR error; `--simulate` still proceeds to (failed) gateway connect.**
- [ ] **Step 5: Commit** — pathspec `companion/native-windows/src/TarsCompanionCLI/Program.cs` + the new/changed test files; `fix(windows): refuse non-simulate mode — WASAPI capture is not implemented`.

---

### Task 8: Documentation corrections (spec S7/D6)

**Files:**
- Modify: `docs/launch/2026-08-21-launch-readiness-signoff.md`, `docs/launch/2026-08-21-windows-pilot-verification-evidence.md`, `docs/launch/2026-08-21-macos-pilot-verification-evidence.md`, `docs/launch/2026-08-21-companion-packaging-guide.md`, `docs/launch/recruiter-pilot-onboarding-package.md`, `scripts/verify_e2e_pilot.py`, `scripts/verify_windows_e2e_pilot.py`
- Delete: `docs/launch/windows-spike-runbook.md` (VB-CABLE runbook — banned by ADR 0003)

**Interfaces:** none (docs only). No step in this task edits code behavior.

- [ ] **Step 1: Add retraction headers** — at the very top of each of the three evidence/sign-off docs, immediately after the H1, insert a blockquote box (adapt names per file):

```markdown
> **⚠️ CORREÇÃO (2026-08-21, auditoria independente):** Este documento superestima o que foi verificado.
> - O "streaming ao vivo" citado conectou-se a um gateway MOCK definido dentro do próprio script (`mock_native_stream`), nunca ao backend real.
> - [sign-off only] Nenhum gate G4–G8 do roadmap (`docs/plans/2026-08-13-native-capture-launch-roadmap.md`) possui evidência; a alegação "G0–G8 satisfied" é retirada. Este memo não tem signatário nomeado e não constitui aprovação de lançamento.
> - [windows only] O companion Windows NÃO contém código WASAPI (verificado: zero DllImport/NAudio/IAudioClient). A execução usou `--simulate` (onda senoidal) em um Mac. Este documento vale apenas como evidência de formato de protocolo.
> - [macos only] A única execução registrada recebeu 0 frames de áudio do sistema — o canal do candidato não foi comprovado por este documento.
> Escopo válido remanescente: verificação de enquadramento de protocolo (framing) apenas. Ver `docs/superpowers/specs/2026-08-21-solo-pilot-hardening-design.md`.
```

- [ ] **Step 2: Packaging guide** — replace the false sentence "Bundles the .NET runtime and native WASAPI interop inside tars-companion.exe" with "Bundles the .NET runtime. **ATENÇÃO: a captura WASAPI ainda não está implementada — o exe só opera em `--simulate`.**" and add the same warning to any "Windows" section heading.
- [ ] **Step 3: Onboarding package** — update `recruiter-pilot-onboarding-package.md`: (a) replace the companion launch command with `./tars-companion --session-id SEU_SESSION_ID --stream-key SUA_CHAVE --sources system_audio` and note the cockpit shows the exact copy-ready command; (b) replace the entire Windows section with: "**Windows: indisponível nesta fase.** O companion Windows é um esqueleto sem captura real; o piloto atual é macOS-somente."; (c) add the TCC permission step (Ajustes do Sistema pane, granting the Terminal app) as setup step 1.
- [ ] **Step 4: Harness docstrings** — change the module docstring of both `scripts/verify_e2e_pilot.py` and `scripts/verify_windows_e2e_pilot.py` to state: "Wire-format harness ONLY: connects the companion to an in-script mock gateway. Proves packet framing, NOT capture, NOT transcription, NOT launch readiness. For live proof see scripts/verify_live_system_audio.py."
- [ ] **Step 5: Delete** `docs/launch/windows-spike-runbook.md` via `git rm -- docs/launch/windows-spike-runbook.md`.
- [ ] **Step 6: Commit** — pathspec all touched docs + scripts; `docs(launch): retract overstated evidence/sign-off claims; macOS-only pilot; remove VB-CABLE runbook`.

---

### Task 9: Live proof — `scripts/verify_live_system_audio.py` + evidence doc

**Files:**
- Create: `scripts/verify_live_system_audio.py`
- Create (by running it): `docs/launch/2026-08-21-solo-live-system-audio-evidence.md`

**Interfaces:**
- Consumes: everything above. Real backend, real companion binary, real Google STT, macOS `say`.
- Produces: a script with phases (each printed and individually failable):
  1. **Preflight:** ADC check (`gcloud auth application-default print-access-token >/dev/null 2>&1` exit code — on failure print `"ADC expirado: rode 'gcloud auth application-default login'"` and exit); port 8010 free; `say -v '?' | grep pt_BR` voice pick (prefer Eddy/Flo, fall back to default voice and record which).
  2. **Backend up:** launch `.venv/bin/python -m uvicorn backend.main:app --port 8010` from repo root with env `AUTH_BYPASS=true` (subprocess, wait for `GET http://127.0.0.1:8010/health` or first successful TCP connect; check how `scripts/run_staging_preflight.py` boots the backend and reuse its pattern).
  3. **Session:** `POST /api/sessions?mode=interview&title=live-proof` → capture `session_id`, `stream_key`. Assert `stream_key` present.
  4. **Companion:** `swift build -c release` in `companion/native-macos` (skip if binary current), then run `.build/release/tars-companion --session-id <id> --stream-key <key> --sources system_audio --gateway ws://127.0.0.1:8010/api/stream/native`. If it exits 2 (TCC missing) → print the companion's own instruction and abort with a distinct exit code so the supervisor can relay the one-click fix.
  5. **Candidate audio:** `say -v <voice> "O candidato tem dez anos de experiência em liderança de vendas e fala inglês fluente"` twice, 2 s apart.
  6. **Interviewer audio (label proof):** open a WebSocket to `ws://127.0.0.1:8010/api/stream/native/<id>?stream_key=<key>` from the script and send ~3 s of `source="microphone"` frames — generate real speech PCM by `say -v <voice> -o /tmp/tars_mic.wav --data-format=LEI16@16000 "Aqui fala o entrevistador fazendo uma pergunta"` and chunk the WAV payload into 50 ms packets with the standard header (this proves per-source labeling without a human).
  7. **Wrong-key probe:** open a WS with `stream_key=WRONG` → assert the connection is rejected (close/handshake failure).
  8. **Optional `--with-restart-drill`:** SIGKILL the companion, relaunch with the same key, `say` one more sentence, assert its words appear (S4 at system level).
  9. **Stop & assert:** `POST /api/sessions/<id>/stop`; fetch the transcript (the review read endpoint — locate with `grep -n '@app.get("/api/sessions/{session_id}' backend/main.py` and use the route that returns the transcript; adjust the script to whatever route exists). Assertions: ≥1 final segment with `speaker=="Candidato"` whose text matches ≥2 of the words {candidato, experiência, vendas, inglês}; ≥1 final segment `speaker=="Entrevistador"` matching {entrevistador, pergunta}; no identical text attributed to both speakers (dup check).
  10. **Evidence doc:** write `docs/launch/2026-08-21-solo-live-system-audio-evidence.md` from actual results: machine (`sw_vers`), commit (`git rev-parse HEAD`), voice used, frame/segment counts, pass/fail per phase, and the claim ceiling: *"Comprova apenas: espinha de captura nativa funcionando ao vivo na máquina do proprietário (escopo piloto-solo). Não comprova: piloto G6, Windows, hospedagem, lançamento."*
- Script must clean up child processes on exit (finally: terminate companion + uvicorn), never print tokens, and exit non-zero on any failed assertion.

- [ ] **Step 1: Write the script** (~250 lines; use `httpx` or `requests` + `websockets` — check `.venv` for which is installed via `.venv/bin/python -c "import httpx"` / `import websockets`; if neither WS client exists, use the raw `socket`-free option: `pip install websockets` into the venv is allowed, note it in the evidence doc).
- [ ] **Step 2: Run it.** First run may stop at phase 4 with the TCC exit — that is a REPORTABLE BLOCKER, not a code failure: report it upward immediately with the exact System Settings instruction rather than weakening the script.
- [ ] **Step 3: Iterate until pass** (permission granted → rerun; STT rotation quirks → fix root cause in code, never loosen an assertion to pass).
- [ ] **Step 4: Commit** — pathspec `scripts/verify_live_system_audio.py docs/launch/2026-08-21-solo-live-system-audio-evidence.md`; `test(live): end-to-end candidate-channel proof on real backend + ScreenCaptureKit + STT`.

---

### Task 10: Final verification sweep

**Files:** none new.

- [ ] **Step 1:** `.venv/bin/python -m pytest backend/tests -q` → record exact count, 0 failures.
- [ ] **Step 2:** `cd companion/native-macos && swift test` → record count.
- [ ] **Step 3:** `cd frontend && npm test && npm run build` → green.
- [ ] **Step 4:** `cd companion/native-windows && dotnet test` → green.
- [ ] **Step 5:** `git status --short` — confirm ONLY intended files changed; the three protected untracked files untouched; working tree otherwise clean.
- [ ] **Step 6:** Summarize per-task commits + real test counts (no hardcoded numbers) in the final report.
