# Task 05b designer fixes — connection-local browser gate and exact Swift token rejection

Read `docs/builder/task-05b-brief.md` and the current `docs/builder/task-05b-report.md` first. Preserve the current Task 05b implementation and RED evidence. This is a narrow repair pass after independent designer review.

Do not run Git commands. Do not touch the three protected untracked instruction files. Modify only:

- `frontend/src/hooks/useBrowserAudioCapture.ts`
- `companion/native-macos/Sources/TarsNativeCompanion/URLSessionWebSocketTransport.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionOptionsTests.swift`
- `backend/tests/test_native_stream_endpoint.py`
- `docs/builder/task-05b-report.md`

## 1. Make the browser hello gate belong to the current WebSocket

The current global boolean can be clobbered by a late `onclose`/`onerror` callback from an older socket after a newer start. That can disable the new stream, and a stale callback must never authorize or de-authorize another connection.

- Replace the boolean hello gate with a socket-identity gate such as `useRef<WebSocket | null>(null)`.
- In `onaudioprocess`, snapshot `wsRef.current` and require all of: an active session, that socket is `OPEN`, and the hello-ready socket ref is exactly that same socket. Send the frame through that snapshotted socket.
- Build the pure config and successfully construct `new WebSocket(config.url, config.protocols)` before mutating `activeSessionIdRef`, sequence/sample/buffer refs, `wsRef`, or the hello gate. A constructor failure must leave the active connection/session refs untouched and must not start capture.
- The constructor catch must use a fixed content-free pt-BR message such as `"Falha ao abrir conexão com o gateway de áudio."`; never surface `err.message`, because a browser implementation may echo the rejected subprotocol value (the stream key).
- After successful construction, reset the hello-ready socket to `null`, initialize the active-session counters/refs, and install the new socket as current.
- `onopen` must first verify that this socket is still the current socket. Send hello, then set the hello-ready ref to this exact socket, then publish streaming/start pings.
- `onerror` and `onclose` may clear the hello gate, streaming state, current socket, or ping interval only if their socket is still current. Stale callbacks must be inert. The hello-send failure path must likewise clear only the current gate, expose the existing fixed hello failure message, close the socket, and start no ping.
- Explicit stop always clears the socket-identity gate.

Keep browser auto-reconnect out of scope and do not alter audio encoding or ping cadence.

## 2. Reject Swift stream keys exactly; never trim/sanitize

`NativeStreamHandshake.protocols(streamKey:)` currently trims whitespace and returns the trimmed key. This violates both the exact tuple contract and the requirement to reject every value outside the backend-generated ASCII token alphabet.

- Do not trim or normalize `streamKey`.
- Reject the original value when empty or when any scalar is outside ASCII `A-Z`, `a-z`, `0-9`, `_`, `-`.
- Prefer explicit ASCII scalar/range checks; do not use Unicode-general alphanumeric acceptance.
- On success return exactly `["tars-stream", streamKey]`, byte-for-byte unchanged.
- Keep every error description content-free: never echo the rejected value.
- Extend `CompanionOptionsTests` to prove a whitespace-surrounded otherwise-valid key is rejected (not silently repaired), a non-ASCII key is rejected, and neither invalid value appears in its error description. Make the empty-key no-echo assertion meaningful rather than checking for an unrelated literal.

## 3. Correct review/report residue

- In the non-ASCII backend regression test docstring, replace the stale reference to an attacker-controlled query parameter with subprotocol/header input. Do not change test behavior.
- In the report, replace the false claim that the working tree is `clean`; it is intentionally uncommitted and ready for designer verification. Record this repair pass and the final commands/results. Do not claim an independently run gate as builder evidence unless you actually run it.

## Verification

Run and report:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm run build
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift build
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
```

Expected: frontend 64/64, production build success, Swift 79/79, Swift executable/package build success, endpoint 36/36. Stop with the uncommitted working tree ready for independent designer verification.
