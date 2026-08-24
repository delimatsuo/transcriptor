# Builder Task 07 — Verification Repair Packet 1

Date: 2026-08-24

This packet is an additive correction to `docs/builder/task-07-brief.md`. Read the original brief and this packet completely before editing. This packet wins wherever the two documents conflict. All original role, privacy, provider, clean-room, no-Git, no-credential, no-login, no-deploy, and evidence-ceiling boundaries remain in force.

The designer has rejected the first Task-07 candidate. Passing test counts do not qualify it because independent probes reproduced fail-open behavior and several tests passed for unrelated reasons. Repair the candidate in the existing working tree; do not restart or discard valid Task-07 work.

## 0. Builder and evidence boundaries

- Do not run any Git command.
- Do not manually inspect, print, copy, rename, or edit a non-example `.env*` file.
- Do not run `npx`, install/update packages, download a browser/tool, or enable Next telemetry.
- Do not contact Firebase, Google, GCP, Cloud Run, Firestore, GCS, STT, Gemini, Apple, a deployed service, or a real login/account.
- Do not use Docker, Firebase CLI, gcloud, ADC, live audio, or deployment commands.
- Do not touch `AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`, or `docs/builder/task-07-source-manifest.txt`.
- Do not copy or reproduce any named pilot identity from historical documents. Synthetic tests may use generated obviously synthetic local parts under the public corporate-domain constraint.
- Use the exact positive-manifest clean-room and `task07_run` wrapper in Task-07 brief section 12 for every executable gate. Do not claim OS-level network isolation; the wrapper provides environment/dependency isolation and npm offline mode, while the auth browser test must actively abort and record external attempts.
- A previously attempted `npx playwright ...` command and earlier canceled/nonconforming auth-E2E attempts are inadmissible evidence. Do not repeat them. Disclose them in the final report as canceled/discarded process deviations, without claiming they proved a gate.
- Write failing regression/mutation tests first for every defect below. A mutation test is valid only when its unmodified positive baseline passes and changing exactly one contract property causes the expected fixed failure class.

## 1. Exact repair scope

The original Task-07 file plan remains authoritative with these four adjudications only:

1. `frontend/e2e/recent-interviews.spec.ts` is now authorized for the single synthetic `/ws-ticket` route already added. Keep that bounded change because this spec does not use the shared fixture and the centralized authenticated WebSocket-ticket path must remain offline.
2. `frontend/playwright.config.ts` is now authorized for the single `testIgnore` needed to keep the dedicated auth-offline spec out of the bypass-enabled core suite. Keep no other change there.
3. Restore `frontend/next-env.d.ts` exactly to the prerequisite form. Its two imports must be:

   ```ts
   import "./.next/types/routes.d.ts";
   import "./.next/types/root-params.d.ts";
   ```

   Do not allow Next dev/build generation to leave this file dirty again.
4. Restore `frontend/src/components/AudioDeviceSelector.tsx` exactly to its prerequisite behavior. Remove both first-pass hunks that hid the compact selector and replaced the select with permission-dependent explanatory text. Task 07 does not authorize audio-device UX changes.

After those restorations, the expected builder-owned changed-file inventory is exactly these 38 paths (designer-provided; the builder must not use Git to rediscover it):

```text
.env.example
Dockerfile
backend/auth.py
backend/config.py
backend/main.py
backend/scripts/check_auth_setup.py
backend/tests/test_auth.py
backend/tests/test_auth_matrix.py
backend/tests/test_cloud_run_readiness.py
backend/tests/test_startup_credentials.py
backend/tests/test_task07_auth_source_readiness.py
docs/builder/task-07-report.md
docs/launch/cloud-run-pilot-source-readiness.md
docs/launch/firebase-auth-pilot-source-readiness.md
frontend/e2e/auth-source-readiness.spec.ts
frontend/e2e/fixtures.ts
frontend/e2e/recent-interviews.spec.ts
frontend/package.json
frontend/playwright.auth.config.ts
frontend/playwright.config.ts
frontend/src/app/page.tsx
frontend/src/components/AuthControls.tsx
frontend/src/components/CompanionCommand.tsx
frontend/src/components/InterviewReportReview.tsx
frontend/src/components/NoteChips.tsx
frontend/src/components/RecentInterviews.tsx
frontend/src/components/SessionControls.tsx
frontend/src/hooks/useBrowserAudioCapture.ts
frontend/src/hooks/useWebSocket.ts
frontend/src/lib/auth.ts
frontend/src/lib/authAdmission.test.ts
frontend/src/lib/authAdmission.ts
frontend/src/lib/authController.test.ts
frontend/src/lib/authController.ts
frontend/src/lib/firebase.ts
frontend/src/lib/runtimeConfig.test.ts
frontend/src/lib/runtimeConfig.ts
scripts/run_staging_preflight.py
```

If another product/test/document path is genuinely required, stop and report it; do not expand scope yourself. Do not edit this repair packet.

## 2. Backend fail-closed startup ordering

Repair `backend/config.py`, `backend/auth.py`, and `backend/main.py` so startup has this observable order while `app.state.ready` remains false:

```text
raw environment gate
-> sanitized Settings resolution
-> raw-versus-resolved binding gate
-> pure runtime auth configuration gate
-> existing Firebase app explicit local project-binding gate
-> ADC probe
-> Firebase initialization
-> provider/storage constructors
-> ready true
```

Required behavior:

- The first raw gate must run before `get_settings()` or any Pydantic/BaseSettings resolution. It must not need a `Settings` instance.
- `backend/main.py` currently constructs `CorsSettings()` at module import. That is also BaseSettings resolution and must not precede the raw gate. Preserve the Task-06 CORS policy while eliminating/deferring this eager read or putting it behind the same raw-first, content-free construction boundary. Add an isolated module/app-construction ordering test proving the raw gate precedes both `CorsSettings` and `Settings`; lifespan-only ordering is insufficient.
- Treat exact-uppercase `K_SERVICE` as hosted when its value is any non-empty string by length, including ASCII space/tab/newline, NBSP, or other Unicode whitespace. Do not call `strip()` to decide whether it is present. Only an exact empty string is non-hosted.
- A non-empty `K_SERVICE` requires the complete exact hosted raw binding from the original brief. It can never fall back to local or bypass.
- Preserve the original acceptance matrix: raw-absent/local plus resolved local is allowed; raw-present local protected keys must be exact/collision-free/equal; dotenv-only local bypass remains allowed; hosted mode cannot originate only in defaults/dotenv/another settings source.
- Resolve `Settings` behind one reusable content-free boundary. If Pydantic or another settings source raises, catch only to a boolean/fixed outcome, leave the active `except` block, and raise a fixed `AuthConfigurationError` afterward. A sentinel raw value must be absent from `str`, `repr`, recursive `__cause__`/`__context__`, `traceback.format_exception`, response data, and captured logs. Use that safe boundary for every remaining settings resolution; the request middleware must not retain an unsanitized `settings or get_settings()` fallback. It may use initialized settings or fail closed with a fixed response while readiness is false.
- Run the raw-versus-resolved gate only after sanitized resolution and before any provider call.
- Split existing Firebase-app validation from initialization. Before ADC, inspect only the default app's explicitly stored local `projectId` option or a task-owned pure binding record. Never touch `app.project_id`, credentials, or a lazy accessor. Missing explicit binding or mismatch fails with a fixed error. If no app exists, this pre-ADC check is a local no-op; initialization may occur only after the ADC probe. Test the matching path with a non-`dict` `_options` object matching Firebase Admin's local shape and exposing `get("projectId")`: matching passes, missing/mismatch fails, and lazy project/credential accessors remain untouched. A dict-only fake is not qualifying evidence.
- A failure in any gate through existing-app binding must leave readiness false and produce zero ADC or later effects. A failure at ADC or any later initialization stage must leave readiness false and prevent every subsequent stage; already completed earlier calls and the failing stage's invocation are not asserted to be zero.
- Replace dynamic raw-key messages such as `found '<attacker key>'` with fixed field/reason codes. Fixed compile-time field names are allowed; raw spelling/value is not.

Add ordering tests that monkeypatch every stage and assert the exact call sequence. Add one isolated failure at every pre-provider gate and assert all later counters remain zero.

## 3. Backend project, org, hosted Firebase, and raw binding grammar

Repair both runtime validation and `backend/scripts/check_auth_setup.py`:

- Use true full-string matching (`fullmatch` or equivalent `\Z` semantics), not `match()` with a `$` anchor.
- `GOOGLE_CLOUD_PROJECT` is always required and must be unpadded ASCII matching the exact 6–30 lowercase project grammar.
- Every non-`None` `FIREBASE_PROJECT_ID`, including `""`, must pass the same grammar. Do not use truthiness to skip validation.
- Hosted mode requires an explicitly supplied, nonblank valid Firebase project ID exactly equal to the Google project.
- `AUTH_ORG_ID` must be unpadded ASCII and true-full-match the exact org grammar. Newline/control-bearing values must fail.
- The setup checker must apply the same true full-string project validation before equality checks.
- Setup-checker failures and raw-environment failures use only fixed field/reason codes. Never interpolate raw key spelling, value, allowlist data, path, or provider text.
- The checker remains a pure injected-mapping validator and `main()` reads process environment only. Do not run it against the builder's real process environment.

Required isolated probes include terminal `\n`, `\r`, embedded controls, NBSP, zero width, Unicode, leading/trailing ASCII whitespace, slash, URL-like value, uppercase, too-short, minimum valid, maximum valid, and too-long for both project fields and the org where applicable. Include hosted blank Firebase ID and equal-but-malformed project IDs. Each failure must occur before ADC.

Repair allowlist boundary whitespace as part of the preserved parser contract: ASCII HTAB is accepted only as surrounding per-item padding and trimmed exactly like ASCII space. Embedded tabs, CR/LF, and every other control remain invalid. Add a positive tab-padded parser baseline and independent embedded-tab/control negatives.

## 4. Backend bearer-token contract

Repair `verify_bearer_token` and its tests:

- `sub` is mandatory. It must be an actual unpadded 1–128-character ASCII string containing no whitespace or control characters.
- Do not add a narrower identifier alphabet. Printable ASCII values permitted by that contract, including punctuation such as `.`, must remain valid.
- If `uid` is present, independently validate it under the identical grammar and require exact equality with `sub`.
- Never select `uid` as a substitute for missing `sub`.
- Preserve exact email parsing, `email_verified is True`, audience, ID-token issuer, allowlist membership, `check_revoked=True`, and server-derived org.
- Provider failures must be erased from the complete exception chain by raising the fixed authentication error outside active exception handling. Raw token/claim/provider text must be absent from response and logs.
- All bearer rejection cases continue to share the generic HTTP 401 body and Bearer challenge.

Rebuild claim tests from one fully valid claim baseline that includes valid `sub`. Mutate one field at a time. Required cases include missing/blank/padded/control/non-ASCII/structured `sub`; uid absent; uid valid/equal; uid invalid; uid mismatch; a valid dotted subject; non-mapping claims; malformed email; verification flag identity; audience; issuer; revoked/provider failure; and exception/log scrubbing. A positive uid-only fixture is forbidden.

## 5. Backend/static test validity

Repair false-positive tests before trusting counts:

- In `backend/tests/test_cloud_run_readiness.py`, every root `.env.example` positive baseline must contain canonical `TARS_RUNTIME_MODE=local` and all other required safe lines before one mutation is applied.
- Reject missing, duplicate, case-colliding, wrong, quoted, `export`-prefixed, whitespace-around-assignment, padded-value, or later-overriding runtime-mode assignments for the exact reason under test.
- Every Task-07 Docker positive baseline must independently satisfy all earlier Docker contracts, including effective final-stage `RUN useradd`, `USER appuser`, command, dependencies, and safe ENV. The safe-only-before-later-`FROM` mutation must fail because final-stage runtime/bypass bindings are missing, not because `useradd` or `USER` is absent.
- Keep case-insensitive Docker directive parsing and raw non-ASCII rejection from Task 06.
- For table-driven mutations, first call the validator on the baseline and assert success, then mutate one property and assert the expected fixed failure category. Do not accept a generic exception from an unrelated missing prerequisite.
- Expand setup-checker tests to start from a complete valid injected environment and independently remove/collide/mutate each required binding, including all three project equality edges, bypass values, runtime mode, count/domain, nonblank public fields, and `K_SERVICE` hosted enforcement.
- Keep the provider-chain scrub and existing owner/org tests. Complete both list-endpoint adapter matrices with non-mapping, unowned, wrong-owner, and wrong-org records; assert fixed 404 before field access/deserialization and absence of a metadata sentinel from responses and captured logs.
- Remove the trailing blank line in `backend/config.py`; the designer will run `git diff --check` after the builder finishes.

## 6. Exact frontend public runtime configuration

Rebuild `frontend/src/lib/runtimeConfig.ts` and its tests around one non-throwing module-level result plus fixed-error accessors:

- Direct statically named `process.env.NEXT_PUBLIC_*` reads remain confined to this module.
- Export a safe resolved `Result` at module scope. Importing the module with missing/bad configuration must not throw.
- `requirePublicRuntimeConfig()` may throw only a fixed content-free configuration error. Do not include a field value or raw parser error.
- Do not trim a supplied Firebase or URL value into validity. Every supplied value must already be unpadded, ASCII, control-free, and exact.
- Implement the six exact Firebase grammars from the original brief. DNS hostnames require 1–63-character ASCII labels, no empty label/underscore/leading-or-trailing hyphen, at least two labels, and total length at most 253. Apply placeholder rejection case-insensitively. App ID must allow the specified `[A-Za-z0-9_-]` suffix rather than a hex-only subset.
- Development defaults are exactly `http://127.0.0.1:8000`, `ws://127.0.0.1:8000/ws`, and `ws://127.0.0.1:8000/api/stream/native`.
- Before parsing each of the three raw public API/WS base configuration values with `new URL`, reject padding, non-ASCII/IDNA-changing input, controls, backslash, percent encoding, protocol-relative syntax, empty/non-empty userinfo, any query/fragment delimiter (including empty `?` or `#`), empty port, and malformed port.
- Production requires `https:` for API, `wss:` for both sockets, no loopback, API root path only, exact `/ws`, exact `/api/stream/native`, and same normalized hostname/effective port.
- Non-production explicit `http:`/`ws:` is permitted only on `localhost`, `127.0.0.1`, or `[::1]`; explicit `https:`/`wss:` synthetic sources are permitted when the three URLs are otherwise consistent. Enforce the same exact paths, no-userinfo/query/fragment, and same hostname/effective-port rules in every mode.
- Normalize only the one allowed API root slash. Do not strip arbitrary repeated slashes from WebSocket paths.
- `apiUrl(path)` accepts only a relative input beginning `/api` whose canonical resolved pathname is `/api` or `/api/...`. It may preserve a normal query used by an API endpoint, but must reject absolute/protocol-relative input, credentials, fragments, backslashes, traversal that canonicalizes outside `/api`, and non-API paths.
- The destination validator accepts `RequestInfo | URL`, resolves it, and requires exact configured API origin, no credentials, and canonical `/api` pathname before any token/provider/network call.
- The raw-base percent ban does not reject ordinary canonical percent-encoded API path or query data passed to `apiUrl` or the destination validator. Those helpers must still reject encoded authority separators, backslashes, fragments, and dot-segment/traversal forms that canonicalize outside `/api`. Test preservation of a normal encoded query and rejection of encoded traversal.

Use table-driven boundaries and mutations covering all cases from original brief section 10 plus the independently reproduced cases: padded Firebase and URL values, leading-hyphen/empty-label DNS, single-label/underscore bucket, production API path/userinfo/query, wrong WS paths, remote insecure development URLs, inconsistent development hosts/ports, userinfo whose URL `origin` appears trusted, and a non-API `apiUrl` argument.

## 7. Firebase client initialization

Repair `frontend/src/lib/firebase.ts`:

- Remove eager top-level browser calls to `getFirebaseApp()` or `getFirebaseAuth()` and remove compatibility exports that trigger SDK initialization at import.
- Missing/invalid public configuration must remain a normal non-throwing result until the auth UI renders a fixed Portuguese configuration-error state.
- Do not start Firebase, an auth listener, popup, API request, or WebSocket when configuration is invalid.
- Continue using exactly one task-owned named app.
- When the named app already exists, compare all six relevant Firebase options exactly with the validated public configuration. Any missing or mismatched option produces one fixed content-free configuration error; do not reuse the app.
- Never reuse a default/first/arbitrary app. Initialize only App and Auth, not Firestore.

Expose a pure/injected seam sufficient for table-driven tests. Start from one matching existing named-app baseline, mutate each of the six options separately, and prove rejection without initialization/auth effects. Also prove missing config import is safe and zero SDK functions run.

Also test `getApps()` containing only a default app and unrelated named apps: initialize exactly one app under the task-owned name and call `getAuth` only with that returned app. Prove no arbitrary app is reused. Retain the matching named-app baseline and assert that every missing/mutated option causes zero initialize/auth/provider effects.

## 8. `apiFetch` destination, redirect, and principal-bound retry

Repair `frontend/src/lib/auth.ts` and add executable tests in an already authorized Task-07 test file:

- Resolve and validate `RequestInfo | URL` before reading Firebase auth, calling `getIdToken`, or calling `fetch`.
- An untrusted/cross-origin/non-API/credential-bearing/malformed destination throws a fixed local error and produces zero token, provider, refresh, or network calls. Do not merely omit the token and continue fetching.
- Preserve local synthetic bypass as the explicit tokenless exception: after the same trusted-destination validation, a bypass request may fetch without Firebase App/Auth/token work, must still force `redirect: "error"`, and must never perform a refresh retry.
- Force `redirect: "error"` after all caller/input options on the first request and retry; neither a `Request` nor `RequestInit` may override it.
- Treat redirect/3xx failure as fixed and do not follow, refresh, or retry.
- Snapshot the initiating Firebase user object and UID before the first token. On trusted 401 only, re-check that the exact same user object/UID is still current, revalidate the exact original destination, and call forced refresh on that initiating user only.
- After the initial non-forced token acquisition settles and before the first fetch, re-check that `auth.currentUser` is still the exact initiating user object with the exact initiating UID. Account loss or replacement at this point must reject with a fixed content-free cancellation error and perform zero fetches. Add separate pending-token replacement and pending-token account-loss tests.
- If account state changes before refresh or retry, cancel with no second token/request. Never obtain a token from a replacement current user.
- Preserve one refresh retry maximum.
- Do not return or display token/provider exception text.
- For `Request` input, after destination validation but before token/network effects, reject `bodyUsed` or non-cloneable input with a fixed error. Preserve untouched replay material before the first dispatch and construct the one retry from it with caller headers/body/method intact, the refreshed Authorization header, and forced `redirect: "error"`. Test a trusted POST `Request` with a body across a 401 retry, plus consumed/non-cloneable rejection with zero token/network effects.

Required tests use injected auth/token/fetch seams and assert exact call order/count: untrusted before token/network; local bypass trusted/tokenless/no-refresh; caller redirect override rejected; trusted success; pending-token account replacement/loss before first fetch; same-user 401 refresh once; account replacement between 401 and refresh; destination invalidated before retry; replayable POST `Request`; consumed/non-cloneable `Request`; same-origin 302 and external redirect rejection with zero follow-up/refresh; provider sentinel absent.

## 9. Whole-operation admission deadline and principal binding

Rebuild `frontend/src/lib/authAdmission.ts` so one operation race begins before destination validation/token acquisition and remains active through body parsing:

- Default deadline is exactly 10,000 ms; tests may inject a shorter duration.
- Pre-aborted external signal returns `cancelled` before any dependency call.
- External abort during destination resolution, token, fetch, or JSON parsing returns `cancelled` promptly.
- Deadline during any stage returns fixed `retryable` promptly.
- Define deterministic precedence: if the external signal is already aborted when the terminal race is resolved, `cancelled` wins; otherwise the deadline outcome is `retryable`.
- Use one timer and one external listener, remove both in `finally`, and make late resolve/reject settlements harmless with no fetch/state change or unhandled rejection.
- Validate the trusted destination before token acquisition; force `redirect: "error"` after all options.
- Admit every HTTP 2xx only when JSON is a plain/non-null object with nonblank, unpadded, ASCII/control-free string `uid`, `email`, and `org_id`; validate expected UID/email too. Parse email as exactly one canonical address, compare UID exactly and email case-insensitively after canonicalization.
- 401/403 alone are `denied`.
- Identity mismatch, blank/invalid principal, malformed body, 3xx, 408/425/429/other non-2xx/5xx, token/provider/network failure, and timeout are `retryable`.
- External abort/generation replacement is `cancelled`.
- All outcomes/errors are fixed and content-free: no token, response body, principal, raw URL, status text, or dependency exception text.

The checked-in suite must cover valid 200 and another valid 2xx such as 201; malformed/non-object/missing/blank/control-bearing principal; UID/email mismatch; 401/403; every retryable status class; redirect rejection; network/provider/malformed JSON; hung token/fetch/body; pre/mid-stage abort; simultaneous abort/timeout; late resolve and late reject; no-unhandled-rejection; timer/listener cleanup; stale success/denial/timeout; and trusted-destination-before-token call order.

## 10. Real controller ownership and UX wiring

The first-pass `authController.ts` is not a production controller because `useAuth` does not use it and it owns no listener/admission/generation effects. Replace it with the actual dependency-injected state/effect owner and make `useAuth` a thin React/Firebase adapter.

Required controller responsibilities:

- configuration-valid/invalid start;
- optional local synthetic bypass start;
- auth listener subscribe/unsubscribe and fixed listener-error handling;
- current Firebase principal, account generation, admission AbortController, and stale-result fencing;
- popup single-flight;
- admission start/result handling;
- same-principal retry without popup;
- denied-account sign-out-before-chooser ordering;
- synchronous data hiding and abort before provider sign-out;
- account replacement/loss during every async phase;
- cleanup/dispose.

Behavioral requirements:

- Invalid config transitions immediately to `config_error`, leaves `user=null`, and starts zero Firebase listener/popup/API/WS effects.
- Duplicate `signIn()` activations while a popup/admission is in flight open exactly one popup. The operation exposes busy state and settles to a usable fixed state on cancellation/failure.
- Popup cancellation and popup-blocked/network/provider failures map to fixed Portuguese copy without raw exception text.
- Admission success alone exposes the admitted user. Denied/retryable/cancelled never expose interview data.
- Retry calls admission for the same still-current principal and never opens a popup.
- `useAnotherAccount()` synchronously clears visible user and aborts old work, awaits provider sign-out, and only then opens the chooser. If sign-out fails, show a fixed recovery state, open no chooser, and never restore the old user.
- `signOut()` and auth loss synchronously clear the visible user before awaiting provider work. Provider rejection keeps data hidden and uses fixed copy.
- Every account observation advances/fences generation. Late work from account A produces zero state/effects after account B, sign-out, auth loss, or disposal.
- Preserve the page-level active-session confirmed-stop prohibition and `key={uid}` reset.

`AuthControls` must render distinct states:

- configuration error: fixed Portuguese alert, no normal sign-in/retry action;
- denied: fixed alert plus `Usar outra conta`;
- retryable: fixed alert plus `Tentar novamente` wired to controller retry, not sign-in;
- sign-out/provider error: fixed recovery copy without raw text and no old user;
- pending popup/admission/sign-out: visible status, disabled duplicate action, and `aria-busy`;
- signed out: normal Google sign-in.

Add an `onRetry` prop and wire it through `page.tsx`. Keep authenticated content unmounted whenever controller `user` is null.

Controller tests must execute the complete matrix from original brief section 8, including: initial/config error; listener not started for config error; popup single-flight; cancellation/provider failure; popup-to-admission; admitted/denied/retryable/cancelled; same-principal retry without popup; denied sign-out then chooser; sign-out failure no chooser; synchronous hiding before unresolved sign-out; account replacement during popup/token/fetch/body; auth loss/listener error; dispose; stale success/denial/timeout; and provider sentinels absent from every state/message. Test effects/call order, not only reducer return values.

Add a local synthetic-bypass controller test proving immediate fixed synthetic admission with zero Firebase app/auth/provider initialization, listener subscription, popup, or backend-admission calls.

Add an executable production-wiring regression. `useAuth` must instantiate and subscribe exactly one production `createAuthController` path and delegate sign-in, retry, account switch, sign-out, state subscription, and cleanup/dispose to it; it must not retain a second listener/admission/generation state machine. Exercise the same dependency-binding factory used by `useAuth` from `authController.test.ts`, and add a TypeScript-AST topology assertion that fails if `useAuth` does not use that factory. The topology assertion supplements rather than replaces the behavioral controller tests.

## 11. Conforming offline auth browser gate

Replace the current false-positive Playwright scenario.

`frontend/playwright.auth.config.ts` must:

- keep dedicated loopback port 3105;
- set `NEXT_PUBLIC_AUTH_BYPASS: "0"`;
- set all six `NEXT_PUBLIC_FIREBASE_*` fields explicitly to `""`;
- set only the safe loopback API/WS values needed to boot the page;
- set `NEXT_TELEMETRY_DISABLED: "1"` in `webServer.env`;
- use `reuseExistingServer: false`;
- run only `auth-source-readiness.spec.ts`.

`frontend/e2e/auth-source-readiness.spec.ts` must:

- install request/WebSocket observers before navigation;
- abort and record every non-loopback request;
- record/fail every same-loopback `/api` request;
- record/fail `/ws`, `/ws/...`, and `/api/stream/native` application WebSockets;
- permit only loopback document/static requests and the loopback Next `/_next` HMR socket;
- assert the fixed visible Portuguese configuration error;
- assert the authenticated interview tree is absent;
- assert there is no enabled normal sign-in or retry button;
- assert all recorded API, application-gateway WebSocket, and external-request lists remain empty after the UI settles;
- assert there are no uncaught page errors.

This gate must never supply a valid Firebase config or invoke Google. Keep `frontend/playwright.config.ts`'s bounded `testIgnore`, so the existing bypass-enabled 19-test suite remains separate.

## 12. Documentation and report truthfulness

Update both launch-readiness documents only after the repaired gates pass.

`docs/launch/firebase-auth-pilot-source-readiness.md` must include:

- the exact contracts actually proven and the fresh exact commands/counts;
- final state `SOURCE PASS / OWNER REAL-AUTH NOT RUN` only if every repaired gate passes;
- owner gate: privately verify five exact identities; run one allowed and one denied real sign-in; account switch; same-account retry; logout; revocation/refresh; real `/api/me`; record exact candidate SHA, operator, and UTC timestamp;
- evidence privacy: record only labels/counts/PASS-FAIL, never identities, tokens, claims, local environment, or identity-bearing screenshots;
- Task-08-only project/provider/authorized-domain/API-key/Firebase Hosting/Cloud Run binding and deployment;
- Next public config is bound at build time; every public-config change requires a clean rebuild and requalification, not a container/runtime variable edit;
- `firebase.json`, `.firebaserc`, and Firebase Hosting configuration are intentionally absent/not created in Task 07.

`docs/launch/cloud-run-pilot-source-readiness.md` must describe Task 07 as source-only and synthetic/mock proven only after the repaired gates. Real identity spelling/existence and login remain owner-only; Firebase/Hosting/deployment remain Task 08.

Regenerate `docs/builder/task-07-report.md` rather than patching isolated claims:

- state prerequisite `ffd6d1f` and the original brief child as designer assertions, not builder Git verification;
- say this repair packet is designer-provided; do not invent a SHA for it;
- list the exact 38 expected builder-owned paths above and identify the two explicitly authorized scope additions;
- confirm `next-env.d.ts` and `AudioDeviceSelector.tsx` were restored, subject to designer Git verification;
- disclose the canceled prohibited `npx` attempt and earlier nonconforming/canceled auth-E2E attempts as discarded, non-qualifying evidence;
- include fresh RED evidence for these repairs and fresh GREEN commands/output from the exact clean-room wrapper;
- include focused backend, full backend, py_compile, frontend unit, local-binary typecheck, auth E2E, core E2E, production build, `swift test`, and `swift build` separately;
- record the actual Swift test count produced now; do not reuse `39` or another stale count;
- enumerate actual checked-in adversarial cases and derive the count from the tests. Do not reuse unsupported `58`;
- describe the email grammar as the exact Task-07 grammar, not full RFC 5322;
- do not claim OS-level zero network connectivity. State that qualifying npm commands used offline mode, the browser gate aborted/recorded external attempts, and no provider/cloud/login/deploy action was performed;
- preserve all owner/provider/deploy/device evidence ceilings;
- use `SOURCE INCOMPLETE / OWNER REAL-AUTH NOT RUN` until every required repair and clean-room gate passes. Change to exact `SOURCE PASS / OWNER REAL-AUTH NOT RUN` only at the end.

## 13. Qualification sequence

1. Add the missing regression/mutation tests and capture a concise repair RED result from a fresh clean-room mirror. Do not count old/canceled/nonconforming results.
2. Implement the repairs.
3. Restore the two out-of-scope files and keep only the two explicitly authorized harness additions.
4. From a fresh clean-room mirror, run the hostile-parent isolation probe and every exact command in original brief section 12. Use `task07_run`; use the local TypeScript binary and package scripts; never use `npx`.
5. Ensure the auth-offline browser gate actively observes/blocks external attempts and proves the missing-config state.
6. Update report/readiness documents with only the fresh outputs.
7. Stop and report completion. Do not use Git or extend scope.

The designer will then run `git diff --check`, inspect exact scope/tree identity, replay the clean-room gates, and obtain fresh independent backend, frontend, and evidence-surface review. A green builder run is a candidate, not final approval.
