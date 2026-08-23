# Task 06 — Final Independent-Review Repair Packet

## Authority and execution boundary

This is a bounded follow-up to `docs/builder/task-06-brief.md` and
`docs/builder/task-06-fixes.md`. The first repair pass is still uncommitted product
work on top of designer commit `118b4a7`. Independent final review found a small
set of remaining contract and evidence gaps.

Antigravity/Gemini is the sole writer for the files listed below. Do not run any
Git command. Do not touch the protected untracked files `AGENTS.md`,
`frontend/AGENTS.md`, or `frontend/CLAUDE.md`. Do not run Docker, the live audio
harness, cloud/Firebase/provider/network/credential/signing/device/deployment
actions, or inspect `.env.local` contents.

## Exact repair allowlist

Modify only:

- `backend/config.py`
- `backend/tests/test_cloud_run_readiness.py`
- `backend/tests/test_native_stream_endpoint.py`
- `docs/launch/cloud-run-pilot-source-readiness.md`
- `docs/builder/task-06-report.md`

Do not modify `Dockerfile`, `.dockerignore`, either environment example,
production frame/readiness/storage behavior, the frontend component, or the live
harness in this pass. Their current production content was independently
approved; this packet strengthens the remaining parser, tests, and evidence.

## 1. Close the remaining CORS parser bypasses

### Required new RED regressions

Extend the invalid-origin matrix so all of these fail with `ValueError`:

```text
https://example.com?
https://example.com#
https://example.com?#
http://example.com:
https://example.com:
http://[::1]:
chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga:
chrome-extension://FHNADCDKFGDLKOMJPILMGEHHPGMKJNGA
https://\u212a.example
http://[v1.foo]
http://[::1%25lo0]
```

Also add a 32-character extension-ID case made from 31 lowercase `a` characters
plus Unicode U+212A KELVIN SIGN. It must fail; `urlsplit().hostname` currently
normalizes/lowercases that character and the parser then returns the original
authority.

Add a content-free error regression using a distinctive credential sentinel,
for example:

```text
https://alice:TASK06_TOPSECRET@example.com
```

It must fail and `TASK06_TOPSECRET` (and preferably the whole raw origin) must not
appear in `str(exc.value)`. Invalid userinfo is forbidden input and must never be
echoed into an import-time startup traceback/log.

Run the focused CORS/readiness test before the production repair and record the
genuine new failures in the report. Do not manufacture RED for characterization
assertions that already pass against the safe production files.

### Parser contract

Preserve absent defaults, explicit-list replacement, order-preserving
deduplication, and normalization of exactly one trailing `/`. Then make the
parser strict as follows:

- Reject a raw `?` or `#` marker even when its query/fragment value is empty;
  checking only truthy `split.query`/`split.fragment` is insufficient.
- Require the complete origin entry to be ASCII. Valid accepted schemes,
  punycode DNS labels, IPv4, bracketed IPv6, ports, and extension IDs are all
  representable in ASCII. This prevents `urlsplit` Unicode/NFKC host
  normalization from validating one authority and returning another.
- Reject `%` anywhere in the authority. Do not accept percent-encoded host
  punctuation or scoped IPv6 authorities for this deployment allowlist.
- Reject an authority ending in `:`. `split.port is None` does not distinguish
  no port from an empty port delimiter.
- Continue forcing `split.port` evaluation and reject non-numeric, zero, or
  out-of-range ports.
- For a bracketed `http`/`https` host, require a real `ipaddress.IPv6Address`.
  Do not fall through and validate bracketed IPvFuture such as `[v1.foo]` as DNS.
- For an unbracketed `http`/`https` host, accept a valid IPv4 address or the
  existing strict ASCII DNS/localhost label grammar.
- For `chrome-extension`, require the raw authority itself to be exactly the
  canonical 32-character lowercase `a` through `p` ID—no port delimiter,
  uppercase form, Unicode-normalized equivalent, or other authority syntax.
- Wrap `urlsplit`, `hostname`, and `port` parsing failures as fixed,
  content-free `ValueError` categories using `raise ... from None` where needed.
  Remove every exception message that interpolates the raw invalid entry. Do
  not add a logger call.

Keep valid hosted DNS, IPv4-with-port, bracketed-IPv6-with-port, the existing
lowercase extension origin, explicit replacement, and deduplication tests green.
Do not change GCS validation; its adversarial matrix passed.

## 2. Encode the complete no-frame-side-effect invariant

Production frame/path validation is approved and must not change.

Strengthen every omitted, non-string, non-object, and mismatched frame-header
rejection case in `backend/tests/test_native_stream_endpoint.py`:

- assert `StreamManager` construction/start/audio remain zero as now;
- assert neither `microphone` nor `system_audio` becomes healthy or gains a
  source connection—check both source slots for every rejected case, not only
  the attacker-selected/default source;
- retain the no-dedup assertions for the route ID and attacker ID;
- for the mismatch sentinel, assert it is absent from the complete captured
  warning record, including event/positional values and keyword values, not
  only from `kwargs`.

Keep the valid dual-source regression proving two audio forwards and zero stops.

## 3. Preserve the readiness ordering proof

Keep the isolated no-lifespan ASGI route tests and global restoration fixture.
Strengthen `test_lifespan_ready_state_transition` so it explicitly asserts:

- the synthetic app is false before lifespan entry;
- the mocked ADC probe observes readiness false when invoked;
- readiness is true only inside the fully mocked initialized lifespan;
- readiness is false after shutdown.

This is a test-only clarification of the original brief's ADC-before-readiness
contract. Do not change production lifespan code unless this exact test exposes
a real mismatch.

## 4. Make static environment and Docker guards prove their claims

In `backend/tests/test_cloud_run_readiness.py` only:

### Environment examples

- Parse non-comment assignments and require the root placeholder exactly:
  `AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com`.
- Require no `AUTH_ALLOWED_EMAILS` assignment in the frontend example.
- Across both examples, assert there is no
  `GOOGLE_APPLICATION_CREDENTIALS`, credential-path field, `service_account`,
  `private_key`, or `client_email` material.
- Retain exact disabled bypass assignments, all five local CORS defaults, the
  frontend test-only warning, and wildcard rejection.

Do not inspect any real `.env` or `.env.local` file.

### Docker process contract

Parse non-comment Dockerfile instructions and assert:

- there is exactly one `CMD` instruction and it is the approved exact exec-form
  shell command;
- there is no `ENTRYPOINT` instruction that could alter/override the process;
- the effective CMD/ENTRYPOINT instruction set contains neither `--reload` nor
  `--workers`.

This must catch a later overriding multi-worker CMD. Do not modify the approved
Dockerfile itself and do not retry Docker while the daemon is unavailable.

## 5. Restore the complete Task 08 safety handoff

Expand `docs/launch/cloud-run-pilot-source-readiness.md` so its owner/designer
Task 08 gates retain every item from the approved brief:

- bind the exact committed SHA, GCP/Firebase project, owner account, runtime
  service identity, allowed mutation set, and rollback;
- pre-create/verify the exact bucket, grant only required object access, and use
  no downloaded JSON service-account key;
- retain the one-process, `min=1`, `max=1`, `3600`-second timeout, `/readyz`
  startup, `/healthz` liveness, and no end-to-end HTTP/2 WebSocket requirements;
- bind exact safe runtime values and explicitly prove no local `.env` leakage;
- require a clean Firebase Hosting build with explicit API/transcript/native WS
  URLs, all required Firebase public configuration, and bypass `0`;
- require the hosted checklist evidence for allowed and denied accounts,
  cross-owner behavior, path/header mismatch, health probes, ticket renewal and
  reconnect, TLS/ingress/IAM, exact deployed revision, and traffic allocation.

Do not claim any of these gates ran in Task 06.

## 6. Correct the report's build evidence ceiling

Update `docs/builder/task-06-report.md` with a second independent-review/final
repair section and actual post-repair outputs.

The frontend build output states `Environments: .env.local`. Therefore:

- rename the gate from “Clean Production Build” to a truthful production-mode
  build with explicit critical process-variable overrides;
- state that the four required URL/bypass variables were supplied on the
  command line, but Next discovered `.env.local`;
- do not inspect or reproduce `.env.local` contents;
- state explicitly that this is not clean hosted-environment or Firebase-config
  proof and that Task 08 retains that gate.

Also record the final CORS regressions/content-free error behavior, both-source
frame assertions, ADC-before-readiness assertion, stronger environment/Docker
guards, restored Task 08 checklist, final test counts, and unchanged Docker
daemon limitation. Preserve the genuine initial RED evidence and do not rewrite
earlier builder evidence as designer verification.

## Required offline gates

Run from the exact workspace unless a subcommand specifies otherwise:

```bash
.venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py backend/tests/test_cloud_run_readiness.py -q
.venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
.venv/bin/python -m pytest backend/tests -q
(cd frontend && npm test)
(cd frontend && NEXT_PUBLIC_API_URL=https://backend.invalid NEXT_PUBLIC_WS_URL=wss://backend.invalid/ws NEXT_PUBLIC_WS_STREAM_URL=wss://backend.invalid/api/stream/native NEXT_PUBLIC_AUTH_BYPASS=0 npm run build)
(cd companion/native-macos && swift test)
(cd companion/native-macos && swift build)
.venv/bin/python -m py_compile backend/config.py backend/main.py backend/storage/gcs.py scripts/verify_live_system_audio.py
```

Run non-Git source scans for empty production frame IDs, request-time bucket
creation, visible component `--stream-key`, wildcard CORS, and the exact
Docker/context contract. Do not run the live harness. Do not run Docker.

## Completion response

Stop with the working tree uncommitted. Report:

- the five files modified and confirmation that nothing else was touched;
- exact new RED failures before the parser repair;
- all final commands, counts, and failures/skips;
- the parser cases now rejected and safe valid cases retained;
- the corrected frontend-build evidence ceiling;
- confirmation of no Git, protected-file, cloud, provider, credential, live
  network/audio, signing, device, Docker, or deployment action.
