# Task 06 independent-review fixes

Read `docs/builder/README.md`, `docs/builder/task-06-brief.md`, and this file completely. Preserve the Task 06 implementation and genuine RED evidence already in the working tree. Apply only the independent-review repairs below, update `docs/builder/task-06-report.md` with the new evidence, run every listed offline gate, and stop uncommitted for designer verification.

Do not run any Git command. Do not touch the three protected untracked instruction files. All cloud, Firebase, credential, provider, network, deployment, signing, device, and live-audio prohibitions from the original brief remain in force. Do not execute `scripts/verify_live_system_audio.py`.

## Exact repair file plan

Modify only:

- `backend/config.py`
- `backend/main.py`
- `backend/tests/test_native_stream_endpoint.py`
- `backend/tests/test_cloud_run_readiness.py`
- `.env.example`
- `frontend/.env.example`
- `docs/launch/cloud-run-pilot-source-readiness.md`
- `docs/builder/task-06-report.md`

Do not modify any other product/test/doc file in this repair pass.

## 1. Make CORS origin parsing actually fail closed

The current parser checks only a non-empty `netloc`. It incorrectly accepts malformed examples including:

```text
http://:80
http://@
http://example.com:bad
http://example.com:99999
http://exa mple.com
http://example.com\evil
chrome-extension://id:99
```

Repair `parse_cors_allowed_origins` while preserving the exact absent defaults, explicit replacement behavior, one-trailing-slash normalization, order-preserving deduplication, and error-on-invalid contract:

- reject any backslash, Unicode whitespace, ASCII control character, or DEL anywhere in an entry;
- treat userinfo as present when `split.username is not None` or `split.password is not None`, so empty userinfo such as `http://@` is rejected;
- require `split.hostname` to be non-empty;
- force access to `split.port` inside a `try/except ValueError` so a non-numeric or out-of-range port fails;
- for `http`/`https`, accept a valid IPv4/IPv6 literal or an ASCII DNS/localhost hostname whose labels are non-empty, use only letters/digits/hyphens (punycode labels are fine), and neither start nor end with `-`; reject invalid percent escapes/host punctuation rather than trying to repair them;
- for `chrome-extension`, require no port and require the canonical Chrome extension ID shape: exactly 32 lowercase characters in `a` through `p`;
- keep query, fragment, non-root path, wildcard, and scheme rejection;
- never silently drop one bad entry or merge local defaults into an explicit list.

Remove the unused `cors_allowed_origins` field from the full `Settings` class; the narrow `.env`-aware `CorsSettings` reader owns this import-time configuration. Keep normal process-environment precedence over `.env`.

Extend the invalid CORS parametrization with every example above plus an internal tab/newline/control case. Replace the current fake extension ID in the valid custom-origin test with a real-format 32-character `a`–`p` ID. Add one valid IPv4/port case and one valid bracketed-IPv6/port case so the validation does not become DNS-only.

## 2. Restore the exact GCS blank/default and whitespace contract

The original brief requires blank **or** absent `GCS_BUCKET_NAME` to use `<GOOGLE_CLOUD_PROJECT>-tars`. The current validator incorrectly rejects blank, and it rejects only ordinary spaces.

Change the validator so:

- `None` returns `None`;
- a value is trimmed;
- empty after trimming returns `None`, selecting the compatibility default;
- a non-empty value containing **any** remaining Unicode whitespace (`char.isspace()`), `/`, or a `gs://` prefix is rejected;
- surrounding whitespace around an otherwise valid bare name is trimmed and accepted.

Update tests to prove absent, empty, whitespace-only, and surrounding-whitespace values resolve correctly; prove internal space, tab, newline, and non-breaking-space values fail; retain `gs://` and slash rejection. Do not add provider naming-policy guesses beyond the brief and do not contact GCS.

## 3. Restore and strengthen native-frame regression assertions

`test_native_stream_endpoint_routes_microphone_and_system_audio` lost two pre-existing assertions during the Task 06 edit. Restore both:

```python
assert mock_sm_instance.send_audio.call_count == 2
assert mock_sm_instance.stop.call_count == 0
```

Do not weaken or relocate them; this test must still prove both valid sources forward PCM and survive connection close.

Strengthen the new rejection tests:

- for omitted, non-string, and non-object JSON header cases, assert `StreamManager` was never constructed (`sm_cls.call_count == 0`), never started, never sent audio, no dedup entry was created, and neither source became owned/healthy;
- for mismatch, make the same no-construction/no-frame-side-effect assertions;
- retain exact one-event content-free logger assertions and `1008` close with no attacker-controlled data;
- keep the matching-path delivery assertions.

Do not change production frame-gate ordering; independent review approved it.

## 4. Isolate readiness tests and exercise the public ASGI routes

The current lifespan test overwrites module globals (`settings`, `session_mgr`, `firestore_storage`, `gcs_storage`, `gemini_client`, `context_window`, and clears `context_windows`) but the fixture restores only two values. Snapshot and restore every affected global and the contents of mutable context state so this file cannot contaminate later tests. Keep monkeypatch restoration separate from runtime-global restoration.

Replace or supplement the direct health-handler checks with no-lifespan ASGI requests using `httpx.ASGITransport(app=main.app)`:

- set readiness false and prove unauthenticated `GET /healthz` is `200 {"status":"ok"}` and `GET /readyz` is `503 {"status":"not_ready"}`;
- set readiness true and prove unauthenticated `GET /readyz` is `200 {"status":"ready"}`;
- patch ADC/Firebase/storage/Gemini/lifespan provider entry points to fail if called and assert none were called—the ASGI transport in this test must not run lifespan;
- retain the separately fully mocked lifespan transition test and the existing ADC-before-readiness regression test elsewhere.

Remove now-unused `TestClient`/mock imports. Do not start the real lifespan or contact any dependency.

## 5. Tighten static containment tests and examples

Change the `.dockerignore` static test to assert the exact ordered non-comment lines:

```python
[
    "**",
    "!Dockerfile",
    "!requirements.txt",
    "!backend/",
    "!backend/**/",
    "!backend/**/*.py",
    "backend/tests/",
    "backend/tests/**",
]
```

This prevents a later unsafe re-inclusion from passing. Tighten the Dockerfile test to require the exact UID-1001 user creation, `USER appuser`, and single-process `CMD`, not the current loose `"1001" or "tars"` assertion.

Add static environment-example assertions proving:

- root and frontend examples contain the required API, transcript WS, native-stream WS, and bypass variables;
- assignments are exactly `AUTH_BYPASS=false` and `NEXT_PUBLIC_AUTH_BYPASS=0`, never their enabled forms;
- the root CORS local example lists all five current local defaults, so copying it does not silently remove ports 3003 or the existing extension origin;
- `frontend/.env.example` restores a concise comment that bypass is test-only and must never be enabled in a production build;
- no real email allowlist, credential path, service-account JSON key, or wildcard CORS value is introduced.

Do not change Dockerfile or `.dockerignore` production content in this repair unless a new test exposes a real mismatch; both were independently approved.

## 6. Correct stale wording and evidence ceilings

In `backend/main.py`, replace the stale comment that says the stream key is a WebSocket query parameter. It is now a per-session secret offered only as the second `tars-stream` WebSocket subprotocol entry.

In `docs/launch/cloud-run-pilot-source-readiness.md`:

- replace “browser microphone streaming requires session restart if interrupted” with the exact source evidence: it has **no automatic reconnect**; Task 08 must validate the operator recovery path and must not claim uninterrupted browser audio across a Cloud Run timeout;
- call `.dockerignore` evidence a checked-in source-rule contract, not a resolved Docker-context/container proof, and state the Docker build was skipped because the daemon was unavailable;
- avoid saying the environment examples define “all” parameters; say they define the required pilot parameters.

Update `docs/builder/task-06-report.md` with an independent-review/fix section, every real changed test count/output, the Docker skip, and corrected claims. Mention that the designer-authored `docs/builder/task-06-fixes.md` is task documentation outside the builder's product-file allowlist. Do not fabricate or rewrite the genuine initial RED evidence.

## Offline verification

Run all of these after the repairs:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py backend/tests/test_cloud_run_readiness.py -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && NEXT_PUBLIC_API_URL=https://backend.invalid NEXT_PUBLIC_WS_URL=wss://backend.invalid/ws NEXT_PUBLIC_WS_STREAM_URL=wss://backend.invalid/api/stream/native NEXT_PUBLIC_AUTH_BYPASS=0 npm run build
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift build
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m py_compile backend/config.py backend/main.py backend/storage/gcs.py scripts/verify_live_system_audio.py
```

Run non-Git source scans for the same Task 06 invariants. Do not retry Docker: the designer independently confirmed the daemon is unavailable, and static checks must remain labeled as source evidence only.

Stop with the uncommitted tree ready for designer verification. Do not commit.
