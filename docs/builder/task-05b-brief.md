# Task 05b — Migrate real audio clients to subprotocol auth and hello-first reconnects

Read `docs/builder/README.md` first. Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote the path). Prerequisites are:

- `0ba314d`: the gateway accepts `Sec-WebSocket-Protocol: tars-stream, <stream-key>` while retaining a temporary query fallback;
- `9398ce1`: the gateway accepts strict hello messages, starts a 15-second never-produced deadline, and exposes reconnecting/alert state in the cockpit.

This task migrates the real browser-microphone and macOS companion clients to those contracts. Once both production clients are migrated, remove the deprecated backend query-key fallback. This is a source/test task only: no live network, audio, signing, credentials, provider, or deployment actions.

## Exact file plan

Modify only:

- `backend/main.py`
- `backend/tests/test_native_stream_endpoint.py`
- `frontend/src/lib/streamUrl.ts`
- `frontend/src/lib/streamUrl.test.ts`
- `frontend/src/hooks/useBrowserAudioCapture.ts`
- `scripts/verify_live_system_audio.py`
- `companion/native-macos/Sources/TarsNativeCompanion/URLSessionWebSocketTransport.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/ReconnectingAudioSink.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/CompanionOptions.swift`
- `companion/native-macos/Sources/TarsCompanionCLI/main.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/ReconnectingAudioSinkTests.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionOptionsTests.swift`

Create only:

- `docs/builder/task-05b-report.md`

Do not touch any other file, including `JoinLink.swift`, the deep-link builders/tests, package manifests, configuration, other docs, or the three protected untracked instruction files. Do not run any Git command, including read-only Git commands.

## Shared wire contract

Every production audio WebSocket connection must now:

1. use a keyless URL shaped exactly as `<gateway-base>/<session-id>` — never `?stream_key=...`;
2. offer exactly two WebSocket subprotocol entries, in this order: `tars-stream`, then the stream key;
3. after the WebSocket handshake succeeds, send a valid hello as the first **application** message, before any audio frame, gap, or JSON ping;
4. send the hello again on every newly created transport after reconnect;
5. never put the stream key in a URL, banner, diagnostic, or log.

The control-frame ping used by `URLSessionWebSocketTransport.connect()` to prove the handshake is alive is not an application message and may precede hello. The server selects/echoes `tars-stream`, as implemented in Task 04.

Hello JSON has this semantic shape:

```json
{"type":"hello","sources":["microphone","system_audio"]}
```

Deduplicate sources and order them canonically as `microphone`, then `system_audio`. Browser mic sends only `microphone`. The menu-bar controller sends only `system_audio`. The CLI sends the exact set selected by `--sources` (`system_audio`, `microphone`, or both).

## Backend: remove query fallback

In `native_stream_endpoint`:

- Authenticate only when `Sec-WebSocket-Protocol` parses to exactly two comma-separated entries and the first stripped entry is `tars-stream`; the second stripped entry is the presented key.
- Preserve the exact-two-entry and empty-entry rejection from Task 04.
- Remove all reads of `websocket.query_params["stream_key"]` / `.get("stream_key", ...)` from the endpoint.
- Remove `native_stream_query_key_deprecated`; there is no fallback to warn about anymore.
- On a successful connection, always call `await websocket.accept(subprotocol="tars-stream")`.
- Missing/malformed header, wrong name, wrong key, a query-only key, unknown session, or inactive session still closes with code `1008` before accept and logs the existing content-free `native_stream_rejected` event.
- Do not change hello, frame, gap, watchdog, STT, ownership, disconnect, or health behavior.

Replace `test_native_stream_query_key_still_works_and_warns` with:

```text
test_native_stream_rejects_query_key_without_subprotocol
```

Install a valid active session/key, supply that key only through `query_params`, and assert the socket is not accepted, closes `1008`, sends no pong, and never selects a subprotocol. Keep all other Task 04/05a tests intact.

## Frontend: pure socket configuration and hello-first browser mic

### `streamUrl.ts`

Replace the query-oriented helper contract with a pure exported socket configuration helper. Use these public shapes (the exact interface name may differ only if TypeScript requires it, but the fields and semantics are fixed):

```ts
export type NativeStreamSource = "microphone" | "system_audio";

export interface NativeStreamSocketConfig {
  url: string;
  protocols: [string, string];
  hello: string;
}

export function buildStreamSocketConfig(
  base: string,
  sessionId: string,
  streamKey: string,
  sources: NativeStreamSource[],
): NativeStreamSocketConfig;
```

Requirements:

- Reject/throw on an empty stream key or an empty source array.
- Deduplicate sources and return the canonical allowed-source order (`microphone`, then `system_audio`). Since runtime input can evade TypeScript, reject an unknown source rather than serializing it.
- `url` is exactly `${base}/${sessionId}` and contains neither the key nor a query string.
- `protocols` is exactly `["tars-stream", streamKey]`.
- `hello` is JSON for `{type:"hello", sources: canonicalSources}` and contains no key.

Write four focused `node:test` tests covering keyless URL + exact protocols, microphone hello, canonical/deduplicated two-source hello, and rejection of missing key/empty or invalid sources. Remove the old expectation that a key is appended to the URL.

### `useBrowserAudioCapture.ts`

- Build the config before mutating active-session counters/refs. Surface configuration or constructor failures through the existing pt-BR `lastError` path without starting capture.
- Construct the socket as `new WebSocket(config.url, config.protocols)`.
- In `onopen`, send `config.hello` first. Only after that send succeeds may the hook mark streaming true or start the ping interval.
- Add a connection-local/ref gate checked by `onaudioprocess` so a binary frame cannot race ahead merely because `readyState` became `OPEN` before the open callback finished. Reset the gate on each start, close, error-to-close path, and explicit stop.
- If sending hello throws, keep streaming false, expose `"Falha ao anunciar a fonte de áudio ao gateway."`, close that socket, and do not start pings.
- Existing pings and all later frames remain unchanged after hello.
- Do not add browser auto-reconnect in this task.

## Swift: subprotocol configuration

### Handshake helper and URLSession transport

In `URLSessionWebSocketTransport.swift`, add a small pure public namespace/type such as `NativeStreamHandshake` with:

```swift
public static func protocols(streamKey: String) throws -> [String]
```

It returns exactly `["tars-stream", streamKey]`. Reject an empty key and reject values outside the backend-generated URL-safe token alphabet (`A-Z`, `a-z`, `0-9`, `_`, `-`) so an edited deep link cannot become an invalid/header-like protocol value. Never include the rejected value in the error string.

Extend `URLSessionWebSocketTransport` to retain a protocol array and construct its task with `session.webSocketTask(with: url, protocols: protocols)` when non-empty. Production callers in this task must always pass the two handshake protocols. A default empty array is permitted only to avoid needless source-test churn; it must not be used by the CLI or menu-bar controller.

### Keyless Swift URL configuration

- `CompanionOptions.gatewayURL()` now returns only `<gatewayBase>/<sessionID>` regardless of key; remove query construction/percent-encoding comments and helpers that become unused.
- Add `CompanionOptions.webSocketProtocols()` delegating to the handshake helper.
- The CLI resolves both URL and protocols before starting its sink. Invalid/missing keys fail early with a content-free configuration error. Its printed Gateway URL must be keyless.
- Change `CompanionSessionController.TransportFactory` to receive `(URL, [String])`, update its default and test factories, build a keyless URL, derive protocols through the helper, and pass both into each new transport.
- Remove the controller's query-key encoding and string-replacement redaction block. Its `sink iniciado` log remains useful but now naturally contains only the keyless URL.

Do not change deep-link parsing: it may decode arbitrary input; the connection boundary is where invalid protocol tokens fail closed.

Add/update tests:

1. `CompanionOptionsTests`
   - URL is keyless even with a key set.
   - protocols are exactly `tars-stream`, key.
   - empty and non-token keys are rejected without echoing the value in the error description.

2. `CompanionSessionControllerTests`
   - Add `testControllerPassesKeyOnlyAsSubprotocol` (or an exact semantic equivalent): capture the factory URL/protocols, start with a safe key, and assert URL has no query/key while protocols equal `["tars-stream", key]`.
   - Update every existing factory closure for the new signature without weakening its assertions.

## Swift: hello before every queued item and reconnect

Extend `ReconnectingAudioSink` with an initializer parameter:

```swift
intendedSources: [AudioSource] = []
```

The default preserves isolated legacy tests, but both production callers must pass a non-empty list:

- menu-bar controller: `[.systemAudio]`;
- CLI: the exact requested source set, including both when selected.

The sink must encode a strict hello once from the canonical deduplicated list. On each successful `transport.connect()`:

- send hello through `sendText` and the existing send deadline machinery **before** publishing the candidate as the live transport, setting `connected=true`, or notifying `onStateChange(true)`;
- if hello send fails or times out, cancel the candidate, remain disconnected, notify the normal false transition, back off, and retry with a fresh transport;
- do not dequeue or lose a frame/gap while hello is failing;
- on every fresh reconnect, send hello again before the retried frame/gap;
- do not reset the audio-delivery backoff merely because hello succeeded. Preserve the existing rule that a delivered queued item proves full recovery and resets backoff.

Add three focused `ReconnectingAudioSinkTests`:

1. `testHelloIsFirstApplicationMessageAndCanonicalizesSources`
   - Configure duplicated/reversed sources and one queued frame.
   - Assert first recorded event parses as hello with `microphone`, `system_audio`; second event is the frame.

2. `testEveryReconnectSendsHelloBeforeRetriedFrame`
   - Make the first frame send fail after a successful hello.
   - Assert event order is hello, reconnect hello, retried frame; two transports connect; normal first backoff is used.

3. `testFailedHelloRetriesWithoutDequeuingFrame`
   - Make the first hello send fail.
   - Assert a fresh transport sends hello and then the original frame exactly once; no frame precedes a successful hello.

Parse hello JSON in assertions; do not depend on dictionary key serialization order.

## Canonical live-proof harness: migrate without running live proof

`scripts/verify_live_system_audio.py` is the repository's canonical live system-audio proof and is reused by the later taps re-proof. It currently has three query-auth call sites that would break as soon as the backend fallback is removed. Migrate its WebSocket construction in this task, but do **not** execute the live proof (it invokes TCC, real audio, Google STT, and provider credentials).

- Add a small content-free helper returning `['tars-stream', stream_key]` for the `websockets` client's `subprotocols=` argument. The harness receives backend-generated keys; do not print or embed them in a URL.
- `_probe_invalid_key` uses the keyless session URL and `subprotocols=['tars-stream', 'WRONG']`; preserve its positive rejection semantics and accepted close/status cases.
- `_probe_valid_key` uses the keyless URL and valid subprotocols. Immediately after connect, send a microphone hello before waiting for the control-positive silence. It must still create no `StreamManager` because it sends no audio frame.
- `MicChannel` stores a keyless URL plus the protocol list. Immediately after connect, send the microphone hello before setting `_ready` or sending any real/silence frame.
- Preserve all phase names, exit semantics, ADC/TCC isolation, evidence generation, frame encoding, timing, and real-audio behavior.
- Do not modify generated evidence docs. Do not run this script in Task 05b.

Run only this offline syntax gate for the harness:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m py_compile scripts/verify_live_system_audio.py
```

## TDD and verification

Write the new/changed tests first and capture real RED results. Then implement and run:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm run build
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m py_compile scripts/verify_live_system_audio.py
```

Baselines before this task: endpoint `36`, backend `301`, frontend `62`, Swift `73`. Expected minimums: endpoint `36`, backend `301`, frontend `64`, Swift `79`; all zero failures, and Next.js build succeeds.

Also run a non-Git production-source scan proving there is no `stream_key=` URL construction, no backend query read, and no `native_stream_query_key_deprecated` event in the production files changed by this task, including `scripts/verify_live_system_audio.py`. It is expected that the backend rejection test still mentions `query_params`, and ordinary variable/response-field names such as `stream_key` remain valid.

## Out of scope

- No browser audio auto-reconnect.
- No frame-header `session_id` vs path validation (Task 06).
- No backend health/state redesign beyond removing query auth.
- No deep-link key redesign; the pilot-accepted session key remains in `tars-companion://` pairing links.
- No Windows changes.
- No signing, packaging, entitlements, Developer ID, notarization, live audio, hosted service, Firebase, GCP, credentials, or deployment actions.

## Report

Write `docs/builder/task-05b-report.md` with:

- every file changed;
- exact RED and GREEN commands/output and final counts;
- proof that browser, menu-bar controller, and CLI URLs are keyless;
- proof that the canonical live-proof probes and mic injector are keyless and hello-first (offline source/syntax evidence only);
- proof of exact subprotocol order;
- proof hello is first and repeated on reconnect;
- the production-source no-query scan;
- anything skipped or uncertain.

Do not commit. Stop with the working tree ready for designer verification.
