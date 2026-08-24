# Builder Task 07 — Verification Repair Packet 2

Date: 2026-08-24

This packet is an additive correction to `docs/builder/task-07-brief.md` and `docs/builder/task-07-fixes-1.md`. Read all three completely before editing. This packet wins wherever they conflict. Every role, privacy, provider, clean-room, no-Git, no-credential, no-login, no-deploy, and evidence-ceiling boundary from the earlier documents remains in force.

The designer has rejected the second Task-07 candidate. Its clean-room suites passed, but fresh independent mutation probes reproduced security and contract failures that the checked-in suites did not detect. Passing the existing 231 focused backend, 475 full-backend, 85 frontend, one auth-E2E, 19 core-E2E, build, and Swift gates is therefore non-qualifying evidence. Repair the existing candidate; preserve valid work.

## 0. Builder and evidence boundaries

- Do not run any Git command.
- Do not run `npx`, install or update packages, download tools or browsers, enable Next telemetry, use Docker, or run Firebase CLI, gcloud, ADC, live audio, a real login, a provider call, deployment, or another network-dependent command.
- Do not manually inspect, print, copy, rename, or edit any non-example `.env*` file.
- Do not touch `AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`, `docs/builder/task-07-source-manifest.txt`, or this repair packet.
- Do not copy, reproduce, log, or test any named pilot identity from historical documents. Use only obviously synthetic local parts under the public corporate-domain constraint where that constraint is under test.
- Edit only the real workspace. Run every RED and GREEN executable command from its own newly copied positive-manifest mirror through the original section-12 `task07_run` wrapper. Record a distinct mirror path for each command; never run two qualifying commands in the same mirror or reuse one after Python, Next, Playwright, build, or Swift output has been generated. Never run Next, Playwright, a frontend build, Python tests, or a repair probe directly in the live workspace.
- Do not claim OS-level network isolation. Qualifying npm commands use offline mode. The auth browser gate must intercept forbidden HTTP and WebSocket attempts before contact and record them.
- Add checked-in failing regressions before changing the corresponding implementation. A mutation case qualifies only when its complete unmodified baseline first passes, exactly one property is changed, and the test proves the intended failure stage/category rather than accepting an unrelated exception.
- Keep the final label `SOURCE INCOMPLETE / OWNER REAL-AUTH NOT RUN` until every item and gate in this packet passes in a fresh clean room.

## 1. Exact scope and prerequisite restoration

The exact builder-owned inventory remains the 38 paths listed in repair packet 1. There is no new product, test, harness, or readiness-document path in this packet.

- `frontend/next-env.d.ts` is not one of the 38 changed paths. Restore it exactly to the prerequisite form and do not run Next in the live workspace:

  ```ts
  /// <reference types="next" />
  /// <reference types="next/image-types/global" />
  import "./.next/types/routes.d.ts";
  import "./.next/types/root-params.d.ts";

  // NOTE: This file should not be edited
  // see https://nextjs.org/docs/app/api-reference/config/typescript for more information.
  ```

  The final designer diff must show no change to this file.
- `frontend/src/components/AudioDeviceSelector.tsx` is also not one of the 38. It is already restored and must remain byte-for-byte unchanged from the prerequisite. Do not list it as builder-owned or changed.
- `frontend/e2e/recent-interviews.spec.ts` remains authorized only for its bounded synthetic `/ws-ticket` route. `frontend/playwright.config.ts` remains authorized for exactly two harness boundaries: the dedicated auth-spec `testIgnore`, and `reuseExistingServer: process.env.CI !== "1"` so qualifying `CI=1` core-E2E runs must start the current mirror's server and fail if the port is occupied. No other change there is authorized.
- Put every new frontend unit regression in an already authorized unit-test file: `runtimeConfig.test.ts`, `authAdmission.test.ts`, or `authController.test.ts`. Section 8's browser-gate and guard-strength cases go in the already authorized `frontend/e2e/auth-source-readiness.spec.ts`. Do not create another test path.
- If any other path appears necessary, stop and report `SOURCE INCOMPLETE`; do not expand scope.

## 2. Backend runtime and configuration repairs

### 2.1 Exact project and organization grammars

Use the same true-full-match ASCII patterns in runtime validation and the setup checker:

```text
project: \A[a-z][a-z0-9-]{4,28}[a-z0-9]\Z   # 6-30 characters
org:     \A[a-z][a-z0-9-]{1,61}[a-z0-9]\Z   # 3-63 characters
```

- A project or org must begin with a lowercase letter, not a digit.
- `GOOGLE_CLOUD_PROJECT` is always required and valid. Every non-`None` `FIREBASE_PROJECT_ID`, including `""`, is independently grammar-validated.
- Project equality is required only in `hosted-pilot`. A valid local configuration may use distinct valid Google and Firebase project IDs.
- Add passing minimum/maximum boundaries and one-property rejects for digit-led, hyphen-led, too-short, too-long, 64-character org, uppercase, padding, control, Unicode, slash, and URL-like values. Exercise both runtime and setup-checker paths from complete valid baselines.

### 2.2 No authenticated API service before readiness

- For every HTTP `/api/` request other than an actual CORS preflight, before bypass or bearer handling, require both `request.app.state.ready is True` and a non-`None` initialized global `settings`. Otherwise return only fixed HTTP 503 content and perform zero settings resolution, Firebase/token, route, storage, or other downstream effects.
- Define the only `OPTIONS` exception by the actual CORS-preflight shape: method `OPTIONS` with both an `Origin` header and an `Access-Control-Request-Method` header present. That request may produce only the established CORS middleware's allowlisted/disallowed preflight response and must perform zero auth, protected-route, settings-resolution, provider, storage, or other application effects. Bare `OPTIONS`, `Origin`-only, and access-control-request-method-only requests are not preflights; while unready they receive the same fixed 503 as other protected API requests. No `OPTIONS` response is authentication or readiness proof.
- Apply the same readiness/global-settings boundary at the first line of both WebSocket families, `/ws/{session_id}` and `/api/stream/native/{session_id}`. When not ready, deny the upgrade before `accept()` with a fixed HTTP 503 denial response and fixed content such as `{"detail":"Service unavailable"}` using Starlette's supported WebSocket denial-response surface. Perform zero ticket/token/stream-key lookup, authentication, accept, session/provider construction, registry mutation, or downstream work. Do not expose configuration/provider detail. Do not claim that a pre-accept denial delivers a WebSocket close frame/code.
- Delete the middleware fallback that resolves settings while readiness is false. A local bypass must not make `/api/me` return 200 before lifespan completes.
- At lifespan entry, before the first gate, clear stale global settings/provider handles and keep readiness false. On any startup failure and on shutdown, leave readiness false and clear globals so a previous successful lifespan cannot authorize a later failed epoch.
- Remove every direct `get_settings()` call from `backend/main.py`, including the remaining deep request/helper call sites. Request paths use the initialized global or fail fixed 503. Any non-request resolution that is still genuinely required must use `resolve_settings_safely()` and preserve the content-free exception-chain contract.
- Add isolated ASGI regressions with a complete local-bypass environment and `ready=false`: `GET /api/me` returns fixed 503; fully shaped allowed and disallowed CORS preflights produce only the established preflight surfaces; bare, `Origin`-only, and access-control-request-method-only `OPTIONS /api/me` return fixed 503; and a production-observable handshake through the ASGI/TestClient denial-response extension for each WebSocket family receives the fixed HTTP 503 denial body before upgrade. Assert zero route/auth/settings/ticket/key/accept/session/provider/registry effects. An assertion against only an internal `websocket.close` ASGI event is not qualifying. Add stale-global startup-failure and shutdown tests.
- Add a synthetic invalid settings value at each former direct-resolution site and prove it is absent from `str`, `repr`, recursive cause/context, formatted traceback, response, and logs.

### 2.3 Content-free existing Firebase binding inspection

- Keep the pre-ADC check local: inspect only the default app's explicit local `_options.get("projectId")`; never access `app.project_id`, credentials, or another lazy property.
- Treat the expected no-default-app exception as the no-op case. Convert every other default-app lookup, `_options` access, shape, or `get` failure into one fixed `AuthConfigurationError` raised outside the active `except` block.
- A throwing non-dict options object must not leak its sentinel through `str`, `repr`, recursive `__cause__`/`__context__`, `traceback.format_exception`, response, or logs. Lazy project/credential sentinels remain untouched. Failure occurs before ADC and leaves every later counter zero.
- Preserve the matching non-dict options-object positive case and the fixed missing/mismatch negatives.

### 2.4 Preserve the Task-06 local CORS source policy

- Keep the raw auth-process gate before any BaseSettings construction.
- After that raw gate, resolve `CorsSettings` through a content-free boundary so its established local `.env` source remains supported. Do not replace it with an `os.environ`-only read.
- Parse the resolved CORS value with the existing fail-closed Task-06 parser. Invalid settings/parser data must never be interpolated into errors or logs.
- Add app-wiring/order tests, not only class tests: raw gate first; then safe CORS resolution; then parser/middleware binding. Prove an injected secondary local source is honored and malformed settings are scrubbed. Qualifying gates still run in a mirror containing no non-example env file.

## 3. Backend regression and mutation-harness validity

Repair the false-positive harnesses before using their counts.

### 3.1 Root example parser and mutations

- For every non-comment assignment, accept only exact `KEY=value` source syntax. Reject `export`, leading/trailing assignment whitespace, whitespace around `=`, padded values, quotes used to disguise a required value, non-ASCII/NBSP characters, invalid key syntax, duplicate logical keys, and case collisions. Do not call `strip()` to turn any of these into valid input.
- The complete positive root baseline must contain canonical `TARS_RUNTIME_MODE=local` plus every other required safe root binding. The frontend baseline must also be independently complete and valid.
- Call the validator on both unmodified baselines and assert success before each table. Then create each mutation from that baseline, change exactly one property, and assert the expected fixed category.
- Add explicit regressions for:
  - missing, wrong, duplicate, lower/mixed-case collision, quoted, and later-overriding `TARS_RUNTIME_MODE`;
  - `export TARS_RUNTIME_MODE=local`;
  - spaces before/after the key, around `=`, or around `local`;
  - trailing NBSP and other non-ASCII assignment characters;
  - the corresponding required bypass, allowlist, public-bypass, URL, CORS, and credential-indicator protections preserved from Tasks 06/07.

### 3.2 Docker final-stage mutations

- Start every Docker mutation from a complete passing multi-stage baseline whose effective final stage independently contains the required dependencies, `RUN useradd`, `USER appuser`, single safe command, and every safe ENV binding.
- For the “safe only before later `FROM`” case, retain every unrelated final-stage prerequisite and remove only final-stage runtime/bypass bindings. Prove the failure category is the missing final-stage binding, not missing `useradd`, `USER`, dependency, or command.
- Independently mutate final-stage runtime and bypass for missing, duplicate, case-collision, unsafe value, safe-early-only, and multi-assignment forms. Preserve Task-06 case-insensitive directive parsing and raw Unicode rejection.

### 3.3 Bearer and adapter mutations

- Build every claim mutation from one fully valid claim containing mandatory valid `sub`, verified email, audience, issuer, and all other prerequisites. Numeric/structured `uid`, email, audience, issuer, or other field cases must mutate only that field; they must not fail earlier for a missing `sub`.
- Instrument or assert the exact intended validation boundary so a mutation cannot pass merely because another field is invalid.
- For both session-list adapter paths, execute the full independent matrix: non-mapping (`None`, list, string), missing owner, wrong owner, missing org, and wrong org. Start from a valid owned/org-bound mapping, mutate one property, assert fixed non-enumerating 404 before field access/deserialization, and prove the metadata sentinel is absent from response and logs.

## 4. Exact frontend runtime configuration and Firebase initialization

### 4.1 Public-value grammars

Implement the original exact grammars literally:

```text
API key:      ^AIza[A-Za-z0-9_-]{35}$
project ID:   ^[a-z][a-z0-9-]{4,28}[a-z0-9]$
sender ID:    ^[0-9]{6,20}$
app ID:       ^1:[0-9]{6,20}:web:[A-Za-z0-9_-]{8,128}$
```

- Auth domain and storage bucket both use the same 1-253-character ASCII DNS grammar: at least two labels; every label 1-63 characters; no underscore, empty label, or leading/trailing hyphen; no scheme, userinfo, path, port, control, Unicode, or padding.
- Reject angle-bracket placeholders and case-insensitive `your-`/`example` placeholder shapes across all six Firebase fields. Do not display the field value.
- Add exact minimum/maximum positives and one-step below/above negatives. Include the independently reproduced digit-led project, one-label bucket, placeholder domain, and valid eight-character App-ID suffix cases.

### 4.2 API and WebSocket base URLs

- The only insecure development hosts are exact `localhost`, `127.0.0.1`, and `[::1]`/hostname `::1`. `0.0.0.0` is not an authorized browser destination.
- In every mode the API scheme is exactly `http:` or `https:` and WebSocket schemes are exactly `ws:` or `wss:`. Development does not admit FTP or another unlisted scheme. Insecure schemes are loopback-only; explicit secure synthetic sources remain allowed when the three values are otherwise consistent.
- Before `new URL`, continue rejecting padding, non-ASCII, controls, backslash, percent encoding, protocol-relative syntax, userinfo, and every query/fragment delimiter. Also reject an authority ending in an empty port delimiter such as `host:` or `[::1]:`; malformed and out-of-range ports remain invalid.
- Preserve exact root, `/ws`, and `/api/stream/native` paths and exact normalized hostname/effective-port agreement.
- Add the complete three-URL matrix for FTP/other schemes, `0.0.0.0`, empty port, padded/Unicode/control input, remote insecure input, wrong path, query/fragment, and inconsistent host/port.

### 4.3 `apiUrl` and trusted-destination helpers

- `apiUrl` requires the raw argument itself to begin with `/api` and resolve canonically to `/api` or `/api/...`; never prepend a slash to make `api/me` valid.
- Reject absolute/protocol-relative input, raw or encoded backslashes/authority separators, credentials, any fragment delimiter including empty `#`, and dot-segment/traversal forms that canonicalize outside or disguise the `/api` tree.
- Preserve ordinary canonical percent-encoded API path/query data that does not encode a separator, authority, backslash, or traversal trick.
- The destination predicate applies the same canonical path and confusion checks to string, `URL`, and `Request` inputs, requires exact configured origin, and rejects any fragment. It must not accept a destination merely because `URL.origin` appears trusted.
- Add one-property positives/negatives for slashless `api/me`, encoded backslash, encoded traversal, empty fragment, normal encoded query, credential-bearing input, and cross-origin input.

### 4.4 Firebase App/Auth initialization matrix

The repair-packet-1 Firebase initialization matrix is still mandatory and currently has no executable coverage. Put it in an existing authorized test file.

- Missing/bad public configuration imports safely and calls zero Firebase SDK functions.
- A matching task-owned named app is reused only after all six options match exactly.
- Mutate or remove each of the six options independently; each case returns/throws only a fixed content-free configuration failure and calls zero `initializeApp`, `getAuth`, listener, popup, or provider effects.
- When only the default and unrelated named apps exist, initialize exactly one app with the task-owned name and call `getAuth` only with that returned app. Never reuse the first/default/arbitrary app.
- Catch injected `getApps`, option-access, `initializeApp`, and `getAuth` sentinel failures at the controller/configuration surface without exposing dependency text.

## 5. Immutable, replay-safe `apiFetch`

Rebuild `apiFetch` around a prepared immutable request snapshot.

- Resolve the configured base and convert `string | URL | Request` input plus `RequestInit` into immutable, replayable request material before the first await. Validate that exact canonical snapshot before Firebase/Auth/token/network work. Never retain a caller-mutable `URL` or request-like object's destination for dispatch.
- Force `redirect: "error"` after all caller options while preparing the snapshot. Dispatch only a clone/new request derived from the trusted snapshot on both attempts.
- Honor `RequestInit` override presence, not truthiness. In particular, explicit `body: ""` overrides an original `Request` body and must not cause the original body to be sent. Preserve method, headers, and a non-empty replayable body exactly across one 401 retry.
- Reject already-consumed or non-cloneable request input before token/network effects. Preserve untouched replay material before the first dispatch.
- Revalidate the same immutable destination after the first token settles, before forced refresh, after refresh, and before retry. At every point require the identical initiating user object and UID. Account loss/replacement or trust invalidation throws a fixed cancellation/failure and makes no next token/request; do not return the original 401 as if the boundary passed.
- If an injected fetch returns any 3xx despite `redirect: "error"`, throw the fixed redirect failure immediately. Do not return it, refresh, retry, or follow a `Location` header. Apply this in bypass, unauthenticated, initial-authenticated, and retry paths.
- Provider/token/fetch failures are fixed and content-free.

Required injected tests include exact call order and zero-effect assertions for mutable `URL` changed during token wait; trusted immutable success; untrusted before token; explicit empty-body override; replayable POST `Request` across 401; consumed/non-cloneable input; caller redirect override; injected same-origin and external 302; same-user one-refresh success; account replacement/loss after token and around refresh; destination invalidation after token and after refresh; bypass trusted/tokenless/no-refresh; and dependency sentinels absent from every surfaced error/state/log.

## 6. Whole-operation admission race and exact principal binding

The current timer only aborts a signal; dependencies that ignore it remain pending. Replace it with a real terminal race.

- Create one deadline timer and one external-abort listener before destination validation. Race the entire operation, including destination work, token acquisition, fetch, and `response.json()`, against terminal timeout/cancellation outcomes.
- The terminal promise must settle promptly even when a dependency ignores `AbortSignal`. Abort the internal controller as cleanup/help, but never depend on that abort for settlement.
- If the external signal is already aborted when the terminal outcome is chosen, `cancelled` wins; otherwise the deadline is fixed `retryable`. Remove the one listener and timer in every path.
- Attach handlers to losing/late dependency promises so late resolve/reject produces zero follow-up fetch, state change, or unhandled rejection.
- Validate expected and returned UID/email before admission. UID must be a nonblank, unpadded, ASCII/control/whitespace-free string. Email must parse as exactly one canonical Task-07 address; strings such as `not-an-email`, comma-bearing values, or structured values are invalid even if expected and returned strings happen to match. Compare UID exactly and canonical email case-insensitively. Keep returned `org_id` nonblank, unpadded, ASCII, and control-free.
- Only a valid principal on any HTTP 2xx, including 201, admits. Only 401/403 deny. Every 3xx, other non-2xx, malformed/invalid/mismatched principal, redirect error, provider/network failure, and timeout is retryable. External abort/generation replacement is cancelled.
- Force redirect error after every injected/caller option and validate destination before token acquisition.

Checked-in tests must include never-settling token, fetch, and body; abort during each; pre-abort; simultaneous timeout/abort; late resolve and reject; unhandled-rejection trap; exact timer/listener cleanup; valid 200 and 201; invalid expected UID/email; blank/malformed/comma/control/structured returned fields; UID/email mismatch; status classes; redirect rejection; destination-before-token order; and fixed sentinel-free results. Explicitly assert each hung/aborted case settles within a bounded test window rather than leaving a pending promise.

## 7. Controller ownership, single-flight, StrictMode, and UX

### 7.1 One operation fence independent of listener state

- Own one controller-level operation epoch/lock for popup, admission, retry, account switch, and sign-out. A provider auth-listener callback must not clear that lock or re-enable another operation.
- During `useAnotherAccount()`, synchronously hide the user, abort/fence old work, remain visibly busy, and await provider sign-out. A null-user event caused by that sign-out may update the tracked principal but must not transition to idle or permit `signIn()` while the account-switch operation owns the lock. Only after successful sign-out and a still-current epoch may exactly one chooser open.
- A sign-out failure opens no chooser, preserves hidden data, clears busy into the fixed recovery state, and never restores the old user.
- Duplicate activation during popup, admission, retry, sign-out, or account switch produces exactly one provider/API operation. Add the reproduced null-listener-during-sign-out race and assert one popup total.
- Handle `cancelled` admission and every thrown dependency path without leaving `checking_access`/busy stuck. Fence stale results; map current-operation cancellation to a usable fixed state without exposing old data.
- Catch `getRuntimeConfig`, `getAuth`, listener setup/callback, provider factory, popup, sign-out, API URL, and admission exceptions. Store only fixed pt-BR copy; dependency sentinels never enter state, rendered UI, logs, or an unhandled rejection.
- Distinguish user popup cancellation with non-alarming fixed copy from blocked/network/provider failures with fixed actionable copy.

### 7.2 Retry must use the same principal

- `page.tsx` must pass `onRetry={auth.retry}` in every unauthenticated `AuthControls` rendering.
- Make `onRetry` required for the retryable state. Remove the fallback from retry to `onSignIn`; `Tentar novamente` must never open a popup.
- Test the actual page/controller wiring: retry makes one same-principal `/api/me` admission, zero popup calls, and cannot use a replaced/lost principal.

### 7.3 React StrictMode lifecycle

The current persistent ref is disposed by StrictMode's first effect cleanup and reused by the second setup. Repair the lifecycle adapter:

- Construct a fresh controller inside each effect setup.
- Store that live instance in the ref used by action callbacks.
- Cleanup unsubscribes/disposes only that exact instance and clears the ref only by identity comparison.
- A disposed instance must never receive or commit a later listener/admission/popup result.

Add an executable setup -> cleanup -> setup regression using the same lifecycle factory/adapter used by `useAuth`: two sequential controller instances, exactly one active Firebase subscription after the second setup, normal second-listener transitions/actions, zero effects from late first-listener/results, and exactly-once second cleanup.

### 7.4 Accessible pending and error states

- Pending popup, admission, sign-out, and account-switch states expose visible fixed status, `role="status"`, `aria-live`, and `aria-busy`; duplicate actions are disabled.
- Configuration error has no sign-in or retry action. Denied has only `Usar outra conta`. Retryable has `Tentar novamente`. Provider/sign-out error keeps data hidden and exposes only the intended recovery action.
- Preserve active-session confirmed-stop enforcement and `key={uid}` reset.

Rebuild `authController.test.ts` around complete valid baselines and exact effect order. The production-wiring test must exercise the same dependency/lifecycle factory used by `useAuth`, not merely instantiate a controller or search for a symbol.

## 8. Mutation-resistant auth-offline browser gate

Replace the current observer-only test.

- Set `use.serviceWorkers: "block"` in `playwright.auth.config.ts` so a service worker cannot bypass routing.
- Define one in-spec `installAuthSourceNetworkGuards(context, initialPage, recorders)` seam and use that exact implementation unchanged in both the real missing-config page case and the controlled guard-strength case. Bespoke routing/recording logic in the strength case is forbidden.
- Before navigation, that shared installer must install `browserContext.route("**/*", ...)` or an equivalent context-wide all-request interceptor so the initial request of every page/popup is covered. Parse URLs structurally:
  - allow only the dedicated loopback document and required `/_next/` static assets;
  - record and abort every loopback `/api` request;
  - record and abort every non-loopback HTTP request, including an external path named `favicon.ico`;
  - do not use substring exemptions.
- Before navigation, the same shared installer must install `browserContext.routeWebSocket(...)` for all sockets. Connect only the dedicated loopback Next `/_next` HMR socket. Record and close/mock without server contact every `/ws`, `/ws/...`, `/api/stream/native`, other application socket, wrong-port socket, and non-loopback socket.
- Record every new page/popup. The missing-config page case requires no unexpected popup. The guard-strength case must deliberately attempt an external popup and prove its first navigation is intercepted/aborted before contact.
- Keep page error collection. After the UI settles and a short deterministic quiescence window passes, assert the fixed configuration alert, no authenticated tree, no enabled `Entrar com Google`, no enabled `Tentar novamente`, empty app API/socket/external-attempt lists, and no page errors.
- Add a separate controlled guard-strength case in the same authorized spec that uses the shared installer and attempts a synthetic external `favicon.ico`, external popup, same-loopback `/api` request, application WebSocket, and non-loopback WebSocket. Assert each is recorded and blocked/closed before contact. Do not connect to an external server.
- Keep `AUTH_BYPASS=0`, all six Firebase fields explicitly blank, only safe loopback API/WS boot values, telemetry disabled, dedicated port 3105, `reuseExistingServer=false`, and separation from the 19-test bypass suite.

The qualifying browser claim is: forbidden attempts were intercepted before contact and the actual missing-config page emitted none. Do not claim that an observer proved zero contact.

## 9. Documentation and report repair

Regenerate `docs/builder/task-07-report.md` and both readiness documents only after the repaired candidate passes.

### 9.1 Report

- Until final GREEN, label it `SOURCE INCOMPLETE / OWNER REAL-AUTH NOT RUN`.
- Identify `docs/builder/task-07-fixes-2.md` as designer-provided with no builder-verified SHA. Keep it outside the 38 builder-owned paths and do not invent a commit identifier for it.
- List exactly the 38 builder-owned paths from repair packet 1. Do not list `AudioDeviceSelector.tsx` or `next-env.d.ts` as changed. State that both are prerequisite restorations subject to designer Git verification.
- Keep the disclosed prohibited/canceled `npx` and direct-workspace Next/Playwright attempts as discarded, non-qualifying evidence.
- Add exact fresh clean-room RED commands and concise output showing the newly added regressions failed against the rejected implementation. Prose saying a case was tested is not RED evidence.
- Add exact fresh GREEN commands/output only after repairs. Keep focused backend, full backend, py_compile, frontend unit, local-binary typecheck, auth E2E, core E2E, build, Swift test, and Swift build separate.
- Derive the adversarial inventory mechanically from actual checked-in executable cases. List each test function/title or parameter table, its collected case cardinality, positive-baseline assertion, and arithmetic. Count each collected case once. Do not reuse `70`, infer ranges from comments, or claim an absent mutation.
- Use only actual exported symbol names. Do not document nonexistent `getPublicRuntimeConfigResult`.
- State precisely: qualifying npm commands used offline mode; the browser test intercepted forbidden attempts before contact; no provider/cloud/login/deploy/live-audio action was performed. Do not claim OS-level network isolation.
- The final label may become `SOURCE PASS / OWNER REAL-AUTH NOT RUN` only after all repaired gates pass and the inventory/restoration claims are true.

### 9.2 Firebase auth readiness

- Correct the email grammar. The exact allowed local-part characters are:

  ```text
  ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&'+=?^_`{|}~.-
  ```

  `*` and `/` are forbidden. Describe this as the exact Task-07 grammar, not RFC 5322.
- Separate the owner-only real-auth qualification from Task 08 provider/deployment mutations.
- Owner-only real-auth qualification remains not run and requires private verification of five identities, one allowed and one denied real sign-in, account switch, same-account retry, logout, revocation/refresh, real `/api/me`, and exact candidate SHA/operator/UTC timestamp. Record only labels/counts/PASS-FAIL, never identity or token material.
- Task 08 alone owns project/provider selection, authorized domains/redirects, API-key restrictions, Hosting/Cloud Run binding, real public build configuration, and deployment.
- Preserve build-time public-config/rebuild qualification and the intentional absence of Firebase deployment manifests in Task 07.
- Describe only implemented/tested exports and evidence. Remove whole-operation deadline, network, or source-pass claims until proven by repaired gates.

### 9.3 Cloud Run readiness

- Keep Task 07 explicitly source-only and synthetic/mock-qualified.
- Real identity spelling/existence and real login are owner-only. Provider/project/API-key/authorized-domain/Hosting/deployment effects remain Task 08.
- Do not conflate either gate or imply that a source test proves a live tenant.

## 10. Required RED/GREEN qualification sequence

### 10.1 Immutable executable-source binding

Every GREEN command must run against identical source/test/harness bytes even though it uses a new mirror. `frontend/next-env.d.ts` is verified at its exact prerequisite hash before each command but excluded from the post-command digest because Next dev legitimately regenerates it inside the disposable mirror. The three evidence documents are also excluded for the self-reporting reason below. Define this fail-fast digest function in each fresh mirror:

```bash
task07_executable_source_digest() (
  set -euo pipefail
  while IFS= read -r task07_digest_path; do
    case "$task07_digest_path" in
      frontend/next-env.d.ts|docs/builder/task-07-report.md|docs/launch/cloud-run-pilot-source-readiness.md|docs/launch/firebase-auth-pilot-source-readiness.md)
        continue
        ;;
    esac
    if [ ! -f "$task07_digest_path" ]; then
      echo 'TASK07_EXECUTABLE_SOURCE_MISSING' >&2
      exit 1
    fi
    shasum -a 256 "$task07_digest_path"
  done < docs/builder/task-07-source-manifest.txt \
    | shasum -a 256 \
    | awk 'NF == 2 {print $1}'
)

TASK07_NEXT_ENV_EXPECTED_SHA256=1862ac4bbbc5192d4bf562161df66ea547ed3e67173100656ab606ae9797db2b
if ! TASK07_NEXT_ENV_PRE_LINE="$(shasum -a 256 frontend/next-env.d.ts)"; then
  echo 'TASK07_NEXT_ENV_HASH_FAILED'
  exit 1
fi
TASK07_NEXT_ENV_PRE_SHA256="${TASK07_NEXT_ENV_PRE_LINE%% *}"
if [ "$TASK07_NEXT_ENV_PRE_SHA256" != "$TASK07_NEXT_ENV_EXPECTED_SHA256" ]; then
  echo 'TASK07_NEXT_ENV_PREREQUISITE_MISMATCH'
  exit 1
fi
if ! TASK07_EXECUTABLE_SOURCE_PRE_SHA256="$(task07_executable_source_digest)" || [ -z "$TASK07_EXECUTABLE_SOURCE_PRE_SHA256" ]; then
  echo 'TASK07_EXECUTABLE_SOURCE_DIGEST_FAILED'
  exit 1
fi
printf 'TASK07_EXECUTABLE_SOURCE_PRE_SHA256=%s\n' "$TASK07_EXECUTABLE_SOURCE_PRE_SHA256"
```

- Immediately after the qualifying command, before accepting its result, recompute with the same function and require equality:

  ```bash
  if ! TASK07_EXECUTABLE_SOURCE_POST_SHA256="$(task07_executable_source_digest)" || [ -z "$TASK07_EXECUTABLE_SOURCE_POST_SHA256" ]; then
    echo 'TASK07_EXECUTABLE_SOURCE_POST_DIGEST_FAILED'
    exit 1
  fi
  if [ "$TASK07_EXECUTABLE_SOURCE_POST_SHA256" != "$TASK07_EXECUTABLE_SOURCE_PRE_SHA256" ]; then
    echo 'TASK07_EXECUTABLE_SOURCE_MUTATED_DURING_GATE'
    exit 1
  fi
  printf 'TASK07_EXECUTABLE_SOURCE_POST_SHA256=%s\n' "$TASK07_EXECUTABLE_SOURCE_POST_SHA256"
  ```

- Record the distinct mirror path and both equal digests beside every GREEN command. Every pre/post digest across every GREEN mirror must be identical.
- Freeze all digest-covered workspace files before the first GREEN command. If any digest-covered file changes, discard every GREEN result and restart the complete GREEN sequence from command one with new mirrors.
- `frontend/next-env.d.ts` must match the hard-coded prerequisite hash before every command and must be unchanged in the real workspace at handoff; any regeneration is confined to that command's disposable mirror.
- The report and two readiness documents are excluded only because they are evidence outputs updated after execution and no qualifying executable reads them. Do not make a source/test/build gate depend on their contents. The designer separately verifies their exact final diff and truthfulness.
- Editing only those three evidence outputs after a digest-bound full GREEN run does not change the executable-source qualification. Any edit to another digest-covered path requires the complete restart above.

### 10.2 Sequence

1. Restore `next-env.d.ts`; keep `AudioDeviceSelector.tsx` unchanged; set all three Task-07 documents to incomplete while repairs are in progress.
2. Add the checked-in regressions from sections 2-8 without changing the corresponding implementation.
3. Using a distinct newly copied positive-manifest mirror and `task07_run` for each executable command, capture concise exact RED output proving failures for at least these independently reproduced defects:
   - digit-led project and digit-led/64-character org;
   - readiness-false local bypass `/api/me`;
   - throwing Firebase options sentinel;
   - strict root assignment/export/padding and validated mutation baseline;
   - causally valid final-stage Docker and bearer/adapter matrices;
   - FTP, `0.0.0.0`, empty port, single-label bucket, digit-led project, placeholder, eight-character App-ID suffix, slashless/encoded/confused API destinations;
   - mutable URL bearer exfiltration, explicit empty-body override, and injected 302;
   - hung/aborted token, fetch, and body;
   - account-switch null-listener double-popup race, same-principal retry wiring, and StrictMode lifecycle;
   - browser interceptor strength.
4. Implement the repairs in the authorized paths only.
5. For the hostile-parent isolation probe and for each exact GREEN command in Task-07 brief section 12, create a separate new clean-room mirror, verify the prerequisite `next-env.d.ts` hash, compute the fail-fast pre-command digest, run only that command through its mirror's `task07_run`, and require the equal post-command digest. Record the distinct mirror path, equal digest, and result for every command. Never reuse a mirror, stale output, or the rejected candidate's counts.
6. Confirm all checked-in positive baselines pass before their individual mutations and every failure is causal.
7. Update the report/readiness evidence from those exact fresh results. Stop and hand back; do not run Git.

The designer will inspect exact scope/tree identity, run `git diff --check`, independently replay the positive-manifest clean-room gates and adversarial probes, and obtain fresh backend, frontend, and evidence-surface reviews. A green builder run is a candidate, not final approval.
