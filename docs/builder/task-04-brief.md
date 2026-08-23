# Task 04 — Gateway auth via WebSocket subprotocol (backend, backward compatible)

Read `docs/builder/README.md` first. Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote — space in path). Backend only: Python 3.12 in `.venv`, FastAPI/Starlette.

## Why

The audio gateway currently takes its per-session secret from the URL query string (`?stream_key=...`). Query strings are written to server access logs, proxy logs, and browser devtools — unacceptable once the backend is hosted. The UI WebSocket already solves this with a subprotocol handshake; the audio gateway must do the same. This task makes the backend accept BOTH (subprotocol preferred, query string still working) so no client breaks; a later task switches the clients and removes the query path.

## Existing code facts

- `backend/main.py`, `native_stream_endpoint` (`@app.websocket("/api/stream/native/{session_id}")`): its auth preamble reads `websocket.query_params.get("stream_key", "")`, compares with `secrets.compare_digest` over UTF-8 bytes (wrapped in `try/except TypeError`), requires `stream_keys.get(session_id)` to exist, the session to exist via `session_mgr.get_session(session_id)`, and `session.status == SessionStatus.ACTIVE`. On any failure it logs `native_stream_rejected` and does `await websocket.close(code=1008)` and returns — **before** `await websocket.accept()`.
- The precedent to imitate is the UI socket at `@app.websocket("/ws/{session_id}")`: it reads `websocket.headers.get("sec-websocket-protocol", "")`, splits on `,`, strips each entry, requires exactly 2 entries with the first equal to `"tars-ticket"`, and later accepts with `subprotocol="tars-ticket"`.
- Tests live in `backend/tests/test_native_stream_endpoint.py`. The `FakeNativeWebSocket` class there currently has `query_params`, `accepted`, `closed_code`, `async def accept(self)`, `async def receive(self)`, `async def send_json(self)`. Helper `_install_session(monkeypatch, session_id, key=...)` installs a fake `session_mgr` plus `main.stream_keys[session_id]`.

## Required behavior

1. Read the offered subprotocols from the `sec-websocket-protocol` header (comma-separated, strip whitespace).
2. If there are exactly 2 entries and the first is `"tars-stream"`, take the second as the presented key and remember that the handshake must be accepted with `subprotocol="tars-stream"`.
3. Otherwise fall back to the existing `?stream_key=` query parameter, and when a key is presented that way log exactly once: `logger.warning("native_stream_query_key_deprecated", session_id=session_id)`.
4. All existing validation is unchanged (bytes `compare_digest`, key must exist, session must exist and be ACTIVE, failure → log `native_stream_rejected` + `close(code=1008)` before accept).
5. On success call `await websocket.accept(subprotocol="tars-stream")` when the subprotocol path was used, otherwise `await websocket.accept()` exactly as today. (Selecting the subprotocol in the response is mandatory — a browser closes the connection if the server does not echo one of the protocols it offered.)
6. Nothing else in the endpoint changes: framing, StreamManager handling, health emission, gap handling, dedup, and the finally block stay exactly as they are.

## Tests (add to `backend/tests/test_native_stream_endpoint.py`)

First extend `FakeNativeWebSocket`: add a `headers: dict` (default `{}`) attribute, and change `accept` to `async def accept(self, subprotocol=None): self.accepted = True; self.accepted_subprotocol = subprotocol` (initialise `accepted_subprotocol = None` in `__init__`). Existing tests must keep passing unchanged.

Then add these tests:
- `test_native_stream_accepts_subprotocol_key`: headers `{"sec-websocket-protocol": "tars-stream, <valid key>"}`, no query param → `accepted is True` and `accepted_subprotocol == "tars-stream"`; a ping message still gets a pong.
- `test_native_stream_rejects_wrong_subprotocol_key`: same shape with a wrong key → `accepted is False`, `closed_code == 1008`.
- `test_native_stream_rejects_unknown_subprotocol_name`: headers `{"sec-websocket-protocol": "something-else, <valid key>"}` and NO query param → rejected (falls through to the empty query path), `closed_code == 1008`.
- `test_native_stream_query_key_still_works_and_warns`: query param path with the valid key → accepted, `accepted_subprotocol is None`. Assert the deprecation warning fired by monkeypatching `main.logger` with an object recording `warning(event, **kw)` calls, or by using `caplog` if the project's structlog setup routes there — pick whichever actually works and say which in your report.

## Verification

```
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q     # must be 290 + your new tests, 0 failures
```

TDD: write the new tests first, run them, record the failures, then implement, then re-run.

## Out of scope

Do NOT change any client (no frontend, no Swift). Do NOT remove the query-parameter path. Do NOT touch `docs/`, `companion/`, or `frontend/`.

## Report

`docs/builder/task-04-report.md`: files changed, RED/GREEN output, full-suite count, and which logging-assertion approach you used.
