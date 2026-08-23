# Task 05a — Gateway hello truth, never-produced alarm, reconnecting cockpit state

Read `docs/builder/README.md` first. Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote the path). Current prerequisite commit is `0ba314d`: the native audio gateway accepts `Sec-WebSocket-Protocol: tars-stream, <stream-key>` and still has a deprecated query-key fallback.

This is the server/UI half of Task 05. A later Task 05b will make the real browser and Swift clients send hello messages. For this task, backend tests send the hello directly. Do not change any audio client yet.

## Why

Today the gateway learns that a connection owns `microphone` or `system_audio` only after its first audio frame. Therefore:

- a selected source that never produces even one frame stays `unknown` forever;
- a transient audio-socket disconnect resets the source to `unknown`, which the cockpit renders as “Aguardando companion” instead of the truthful “Reconectando…”;
- the last socket disconnect is reported as `physical_capture="stopped"` even when the session is still ACTIVE and the capture client is expected to reconnect.

The protocol needs a strict hello declaring intended sources. That declaration starts a 15-second first-frame deadline and gives the gateway per-source ownership before audio exists.

## Exact file plan

Modify only:

- `backend/main.py`
- `backend/tests/test_native_stream_endpoint.py`
- `frontend/src/types/ws.ts`
- `frontend/src/hooks/useWebSocket.ts`
- `frontend/src/components/CaptureSourceStatus.tsx`
- `frontend/src/components/views/InterviewLiveView.tsx`
- `frontend/src/app/page.tsx`

Create only:

- `frontend/src/lib/companionHealth.ts`
- `frontend/src/lib/companionHealth.test.ts`
- `docs/builder/task-05a-report.md`

Do not touch Swift, scripts, configuration, any other docs, or the three protected untracked instruction files. Do not run any git command, including read-only git commands.

## Existing backend facts

`backend/main.py::native_stream_endpoint` already has:

- authenticated accept before the receive loop;
- session-scoped `native_session_health[session_id]` with `sources` and a total `connections` count;
- a connection-local `owned_sources` set, `last_frame_at`, `_mark_owned`, `_set_source_health`, `emit_health`, and `stall_watchdog`;
- a 5-second watchdog check and 10-second post-first-frame stall timeout;
- text handling for `ping` and `gap`;
- `finally` cancellation of the watchdog, decrement of `connections`, reset of this connection's owned sources to `unknown`, and final health emission;
- `CompanionHealthPayload.message`, already present and optional in `backend/schemas/models.py` and `frontend/src/types/ws.ts`.

The endpoint serves concurrent browser-mic and companion-system-audio connections. It must preserve the merged-view rule: one connection may not clobber a still-live owner of the same or another source.

## Hello wire contract

A valid hello is a JSON text message with exactly this semantic shape:

```json
{"type":"hello","sources":["system_audio"]}
```

or:

```json
{"type":"hello","sources":["microphone"]}
```

`sources` may contain both allowed names. Requirements:

1. `sources` must be a non-empty JSON array of strings.
2. Every entry must be exactly `microphone` or `system_audio`.
3. Deduplicate repeated valid entries without incrementing ownership twice.
4. Treat a repeated hello on the same connection as an idempotent union: it may add a newly declared source, but it never retracts an already declared source and never resets that source's first-frame deadline.
5. If the shape is invalid or any entry is unknown, reject the whole hello semantically: log `native_companion_hello_invalid` with `session_id` only, claim no sources from that message, and keep the socket open. Never log the untrusted values.
6. On a valid hello, log `native_companion_hello` with `session_id` and the canonical sorted source list.
7. Existing `ping`, `gap`, binary framing, auth, StreamManager, dedup, and cleanup behavior stays intact.

## Backend state contract

Add:

```python
NATIVE_NEVER_PRODUCED_TIMEOUT_SECONDS = 15.0
```

Extend each session-health dictionary lazily and safely with:

```python
"source_connections": {"microphone": 0, "system_audio": 0},
"alerts": {},
```

Use `setdefault` after retrieving an existing entry so tests or an already-created entry with the old two-key shape remain valid.

### Per-source ownership

- `_mark_owned(source)` must increment `source_connections[source]` only the first time THIS connection owns that source.
- A source becomes owned either from a valid hello, a valid audio frame, or the existing gap path.
- In `finally`, decrement only this connection's owned-source counts, never below zero.
- Change a source on disconnect only when its per-source connection count reaches zero. If another live connection still owns the same source, preserve its state exactly.

### First-frame deadline

Maintain a connection-local `intended_since: dict[str, float]`.

- A source newly claimed by hello gets `time.monotonic()` once.
- The watchdog continues its current post-first-frame stall checks.
- Additionally, if an intended source has never appeared in `last_frame_at` and `now - intended_since[source] > NATIVE_NEVER_PRODUCED_TIMEOUT_SECONDS`, set that source to `device_unavailable`, add its exact message to the session-shared `alerts`, log `native_source_never_produced_frames` with `session_id` and `source`, and emit health.
- The warning/log/health transition must happen once, not on every watchdog tick.
- Use these exact pt-BR messages:
  - `system_audio`: `Nenhum frame recebido de Áudio do Sistema em 15 s. Verifique se há áudio em reprodução e se a permissão do companion está ativa.`
  - `microphone`: `Nenhum frame recebido do Microfone em 15 s. Verifique a permissão e o dispositivo selecionado.`
- `emit_health()` sets `CompanionHealthPayload.message` to all active alert messages joined in canonical source order (`microphone`, then `system_audio`) with one space, or `None` when no alert remains.
- The first later frame for a source removes its first-frame deadline and shared alert, sets the source to `healthy`, and emits the recovered payload with `message=None` when that was the only alert.

### Disconnect truth

In `finally`, after decrementing ownership:

- If the session is still active for reconnect (`session_id in stream_keys` and the authenticated session status is still `SessionStatus.ACTIVE`), a now-unowned source becomes `reconnecting` and its never-produced alert is cleared.
- If the session is stopping/stopped (the stream key has been removed or status is no longer ACTIVE), a now-unowned source becomes `unknown` and its alert is cleared.
- `emit_health()` derives `physical_capture` as:
  - `active` when total live `connections > 0`;
  - `unknown` when there are zero live connections but at least one source is `reconnecting` (capture may still be continuing locally);
  - `stopped` otherwise.

This is required by `docs/product/companion-web-state-contract.md`: a transport loss must not falsely assert physical capture stopped.

## Backend tests — write these first and capture RED

Use the existing `FakeNativeWebSocket`, `_MidStreamWebSocket`, `_install_session`, `_encode_native_packet`, `_health_msgs`, and monkeypatchable watchdog constants. Authenticate new tests with the Task-04 subprotocol header instead of the deprecated query parameter.

Add these exact tests:

1. `test_hello_never_produced_source_alarms_then_recovers`
   - Monkeypatch check interval and never-produced timeout to a few milliseconds.
   - Messages: valid system-audio hello, then a valid system-audio frame.
   - Use `_MidStreamWebSocket` to sleep past the threshold before delivering the frame.
   - Assert a health payload contains `system_audio == "device_unavailable"` and the exact system-audio message.
   - Assert a later payload contains `system_audio == "healthy"` and `message is None`.
   - Assert `native_source_never_produced_frames` was logged exactly once.

2. `test_announced_source_disconnect_is_reconnecting_not_stopped`
   - Send only a valid system-audio hello, then disconnect while the session/key remain active.
   - Final payload: `system_audio == "reconnecting"`, `physical_capture == "unknown"`, `message is None`.

3. `test_session_stop_disconnect_is_stopped_not_reconnecting`
   - Send a valid hello; before the next receive, remove this session's key to simulate `_stop_pipeline`.
   - Final payload: that source is `unknown`, `physical_capture == "stopped"`.

4. `test_invalid_hello_does_not_claim_sources`
   - Send `{"type":"hello","sources":["system_audio","not-a-source"]}`.
   - Assert both source states remain `unknown`, no source becomes `reconnecting` on disconnect, and `native_companion_hello_invalid` logs exactly once without the invalid values in its kwargs.

5. `test_overlapping_same_source_disconnect_preserves_live_owner`
   - Connection A produces a microphone frame and stays logically open via `_MidStreamWebSocket`.
   - During A's second receive, run connection B for the same session; B sends a microphone hello and disconnects.
   - Snapshot after B closes but while A remains open. Microphone must still be `healthy`, never `reconnecting`; physical capture must stay `active`.

Update existing health assertions intentionally affected by the new contract:

- a previously frame-producing source whose active session socket closes now ends `reconnecting` and, if it was the last connection, `physical_capture="unknown"`;
- when another source connection remains live, only the disconnected source becomes `reconnecting`, while the live source remains healthy and physical capture remains active;
- session-stop tests must still end stopped/unknown, never reconnecting.

Do not weaken unrelated assertions.

## Frontend behavior

### Types and hook

- Add `"reconnecting"` to `SourceHealthState` in `frontend/src/types/ws.ts`. Do not add it to `PhysicalCaptureState`; reconnecting is a transport/source presentation, not physical-capture truth.
- Change `CompanionHealthPayload.message` to `message?: string | null`.
- Add `companionMessage: string | null` to `UseWebSocketReturn` and component state.
- On every `companion_health`, set it to `payload.message ?? null` so a recovery payload clears an old alarm.
- Clear it in both new-session reset and `hydrateReview`.
- Return it, destructure it in `frontend/src/app/page.tsx`, pass it through `InterviewLiveView`, and pass it to `CaptureSourceStatus` as `message`.

### Pure presentation helper

Move the current pure `formatSourceHealth` logic from `CaptureSourceStatus.tsx` into new `frontend/src/lib/companionHealth.ts` and export it. Preserve every existing label/color/icon exactly. Add this exact new mapping:

```text
microphone + reconnecting  -> label `Microfone: Reconectando…`, amber background/color used by device_unavailable, icon `↻`
system_audio + reconnecting -> label `Áudio do Sistema: Reconectando…`, same amber treatment, icon `↻`
```

`CaptureSourceStatus` imports and uses the helper.

When `message` is non-empty, render a separate amber warning after the source badges with:

- the exact server text;
- `role="status"`;
- `aria-live="polite"`;
- no color-only communication (include visible `⚠`).

Do not replace or hide either source badge.

### Frontend tests

In `frontend/src/lib/companionHealth.test.ts`, use `node:test` + `node:assert/strict` like the other lib tests. Assert:

- both reconnecting labels are exact, use icon `↻`, and use the amber colors;
- at least one existing mapping (`unknown` or `healthy`) remains byte-for-byte unchanged.

## TDD and verification

Run RED after adding tests but before implementation and record the real failures. Then implement and run:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm run build
```

Baselines before this task: backend endpoint `30`, full backend `295`, frontend `59`. New totals must be at least endpoint `35`, backend `300`, frontend `61`, all with zero failures. The Next.js build must succeed.

Also run a non-Git whitespace check of the changed text files if desired. Do not run `git status`, `git diff`, `git diff --check`, or any other Git command; the designer performs those gates.

## Out of scope

- Do not make browser or Swift clients send hello yet.
- Do not change browser/Swift stream-key transport yet.
- Do not remove the backend query-key fallback.
- Do not change `backend/schemas/models.py`; its optional `message` field already exists and source states are strings.
- Do not change frame header/session validation (Task 06).
- Do not deploy or touch Firebase, GCP, credentials, signing, devices, or live audio.

## Report

Write `docs/builder/task-05a-report.md` with:

- every file changed;
- the exact RED and GREEN commands/output;
- final test counts and build result;
- the hello validation rules implemented;
- how overlapping same-source connections avoid state regression;
- anything skipped or uncertain.
