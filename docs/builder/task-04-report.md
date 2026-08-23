# Task 04 Report — Gateway auth via WebSocket subprotocol (backend, backward compatible)

## 1. Summary of Work Implemented

Implemented WebSocket subprotocol authentication for the native audio streaming gateway endpoint (`/api/stream/native/{session_id}`) with backward-compatible query-parameter fallback according to `docs/builder/task-04-brief.md`:
- **`backend/main.py` (`native_stream_endpoint`)**:
  - Extracted subprotocol offers from the `sec-websocket-protocol` header.
  - If exactly 2 entries are provided and the first entry equals `"tars-stream"`, the second entry is parsed as the stream key and the handshake is marked to accept with `subprotocol="tars-stream"`.
  - If the subprotocol offer is absent or invalid, falls back to the legacy `?stream_key=` query parameter and emits deprecation warning `logger.warning("native_stream_query_key_deprecated", session_id=session_id)`.
  - All existing auth invariants preserved (`secrets.compare_digest` with surrogatepass bytes encoding, session existence and `ACTIVE` status checks, rejected attempts closed with code `1008` before accept).
  - Accepted connections invoke `await websocket.accept(subprotocol="tars-stream")` when authenticated via subprotocol, or `await websocket.accept()` when falling back to query parameters.
- **`backend/tests/test_native_stream_endpoint.py`**:
  - Extended `FakeNativeWebSocket` with `headers: dict` support and `accepted_subprotocol: str | None` recording in `accept(subprotocol=None)`.
  - Added test `test_native_stream_accepts_subprotocol_key` (verifies subprotocol auth and pong response).
  - Added test `test_native_stream_rejects_wrong_subprotocol_key` (verifies 1008 rejection on mismatch).
  - Added test `test_native_stream_rejects_unknown_subprotocol_name` (verifies rejection when protocol name is not `tars-stream`).
  - Added test `test_native_stream_query_key_still_works_and_warns` (verifies query parameter fallback acceptance without subprotocol and asserts `native_stream_query_key_deprecated` emission).

---

## 2. Files Changed

### Modified
1. `backend/main.py`
2. `backend/tests/test_native_stream_endpoint.py`

---

## 3. TDD Output

### RED Phase (Tests executed before backend/main.py implementation)
Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q`

```
.........................F..F                                            [100%]
=================================== FAILURES ===================================
__________________ test_native_stream_accepts_subprotocol_key __________________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x1120a2fc0>

    def test_native_stream_accepts_subprotocol_key(monkeypatch):
        key = _install_session(monkeypatch, "s-sub-1")
        ws = FakeNativeWebSocket(
            [{"text": json.dumps({"type": "ping"})}],
            headers={"sec-websocket-protocol": f"tars-stream, {key}"},
        )
        asyncio.run(main.native_stream_endpoint(ws, "s-sub-1"))
>       assert ws.accepted is True
E       assert False is True
E        +  where False = <backend.tests.test_native_stream_endpoint.FakeNativeWebSocket object at 0x1120a2ff0>.accepted

backend/tests/test_native_stream_endpoint.py:704: AssertionError
----------------------------- Captured stdout call -----------------------------
2026-08-23 13:44:02 [warning  ] native_stream_rejected         session_id=s-sub-1
______________ test_native_stream_query_key_still_works_and_warns ______________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x1120966f0>

    def test_native_stream_query_key_still_works_and_warns(monkeypatch):
        key = _install_session(monkeypatch, "s-query-warn")
        warnings_recorded = []
        original_warning = main.logger.warning

        def fake_warning(event, **kw):
            warnings_recorded.append((event, kw))
            try:
                return original_warning(event, **kw)
            except Exception:
                pass

        monkeypatch.setattr(main.logger, "warning", fake_warning)
        ws = FakeNativeWebSocket(
            [{"text": json.dumps({"type": "ping"})}],
            query_params={"stream_key": key},
        )
        asyncio.run(main.native_stream_endpoint(ws, "s-query-warn"))
        assert ws.accepted is True
        assert ws.accepted_subprotocol is None
        assert ws.sent_json == [{"type": "pong"}]
>       assert any(
            event == "native_stream_query_key_deprecated" and kw.get("session_id") == "s-query-warn"
            for event, kw in warnings_recorded
        )
E       assert False

=========================== short test summary info ============================
FAILED backend/tests/test_native_stream_endpoint.py::test_native_stream_accepts_subprotocol_key
FAILED backend/tests/test_native_stream_endpoint.py::test_native_stream_query_key_still_works_and_warns
2 failed, 27 passed in 3.50s
```

### GREEN Phase (Tests executed after implementation)
Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q`

```
.............................                                            [100%]
29 passed in 2.71s
```

---

## 4. Verification Suite Results

### A. Endpoint Unit Suite
- Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q`
- Result: `29 passed in 2.71s` (25 existing + 4 new).

### B. Full Backend Pytest Suite
- Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q`
- Result: `294 passed in 4.44s` (290 baseline + 4 new, 0 failures).

---

## 5. Logging Assertion Approach

In `test_native_stream_query_key_still_works_and_warns`, logging assertion was performed via `monkeypatch.setattr(main.logger, "warning", fake_warning)`. A lightweight recorder function intercepted call parameters `(event, **kw)` into a list before delegating to `original_warning`, allowing direct assertion on `event == "native_stream_query_key_deprecated"` and `session_id="s-query-warn"`.

---

## 6. Notes & Observations

- **Backward Compatibility**: Existing clients providing `?stream_key=...` continue to connect normally without error while triggering the deprecation warning. Clients providing `Sec-WebSocket-Protocol: tars-stream, <key>` authenticate without exposing secrets in query strings.
- **Git Compliance**: No git commands were executed. Only `backend/main.py` and `backend/tests/test_native_stream_endpoint.py` were modified.

---

## 7. Verification fixes

### A. Repairs Implemented
1. **Finding 1 — Exact two-entry subprotocol parsing**:
   - In `backend/main.py` (`native_stream_endpoint`), updated subprotocol splitting so that empty elements are not dropped prior to length validation (`offered = [item.strip() for item in raw_subprotocol.split(",")] if raw_subprotocol is not None else []`).
   - Added regression test `test_native_stream_rejects_subprotocol_with_extra_empty_entry` verifying that headers with trailing commas or extra entries like `"tars-stream, <key>,"` without query params are rejected with close code `1008`.
2. **Finding 2 — Exact deprecation warning assertion**:
   - In `backend/tests/test_native_stream_endpoint.py` (`test_native_stream_query_key_still_works_and_warns`), replaced the loose `any(...)` check with an exact list match assertion:
     ```python
     assert warnings_recorded == [
         ("native_stream_query_key_deprecated", {"session_id": "s-query-warn"})
     ]
     ```
   - This asserts that the deprecation warning fired exactly once with the expected event and session parameters.
3. **Finding 3 — Clean EOF whitespace**:
   - Cleaned up trailing blank lines at EOF in `backend/tests/test_native_stream_endpoint.py`. Verified clean status via `git diff --check`.

### B. Verification Output & New Totals

**Endpoint Unit Tests:**
Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q`
```
..............................                                           [100%]
30 passed in 3.73s
```

**Full Backend Pytest Suite:**
Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q`
```
........................................................................ [ 24%]
........................................................................ [ 48%]
........................................................................ [ 73%]
........................................................................ [ 97%]
.......                                                                  [100%]
295 passed in 6.10s
```
