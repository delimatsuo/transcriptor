# Task 07 — Firebase authentication source hardening and recruiter sign-in recovery

Read `docs/builder/README.md` first. Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote the path). The exact prerequisite commit is `ffd6d1f`: Task 06 bound native frames to their route identity and added the source-only Cloud Run readiness contract.

Task 07 has two deliberately separate outcomes:

1. **Builder-owned source outcome:** make the hosted-pilot authentication configuration fail closed, prove the `AUTH_BYPASS=false` path with synthetic/mocked inputs, bind frontend admission to the backend principal, validate every token/ticket destination, and give the recruiter a deterministic sign-in/retry/account-switch experience.
2. **Owner-only live outcome:** verify the five real Google/Firebase identities and exercise real allowed/denied sign-in. This is **not authorized in this builder task** and must remain `OWNER REAL-AUTH NOT RUN`.

The only honest successful Task-07 builder result is:

> `SOURCE PASS / OWNER REAL-AUTH NOT RUN`

Do not claim that any real account exists, that any real token verifies, that Firebase or Google OAuth is configured, that Hosting is ready, or that a deployed tenant boundary works.

This is a source/test/documentation task only. Do not use Git. Do not manually open, inspect, print, copy, rename, or edit `.env`, `frontend/.env.local`, or any other non-example `.env*` file. Required verification must run from the clean-room mirror defined in section 12, which excludes those files before any Python, Next, or Playwright process starts. Do not contact Firebase, Google, GCP, Cloud Run, Firestore, GCS, STT, Gemini, Apple, or any deployed service. Do not launch a real login popup.

## Exact file plan

Modify only:

- `Dockerfile`
- `.env.example`
- `backend/config.py`
- `backend/auth.py`
- `backend/main.py`
- `backend/scripts/check_auth_setup.py`
- `backend/tests/test_auth.py`
- `backend/tests/test_auth_matrix.py`
- `backend/tests/test_cloud_run_readiness.py`
- `backend/tests/test_startup_credentials.py`
- `scripts/run_staging_preflight.py`
- `frontend/.env.example`
- `frontend/package.json`
- `frontend/src/lib/firebase.ts`
- `frontend/src/lib/auth.ts`
- `frontend/src/lib/authAdmission.ts`
- `frontend/src/lib/authAdmission.test.ts`
- `frontend/src/components/AuthControls.tsx`
- `frontend/src/app/page.tsx`
- `frontend/src/hooks/useWebSocket.ts`
- `frontend/src/hooks/useBrowserAudioCapture.ts`
- `frontend/src/components/CompanionCommand.tsx`
- `frontend/src/components/RecentInterviews.tsx`
- `frontend/src/components/NoteChips.tsx`
- `frontend/src/components/SessionControls.tsx`
- `frontend/src/components/InterviewReportReview.tsx`
- `frontend/e2e/fixtures.ts`
- `docs/launch/cloud-run-pilot-source-readiness.md`

Create only:

- `backend/tests/test_task07_auth_source_readiness.py`
- `frontend/src/lib/runtimeConfig.ts`
- `frontend/src/lib/runtimeConfig.test.ts`
- `frontend/src/lib/authController.ts`
- `frontend/src/lib/authController.test.ts`
- `frontend/e2e/auth-source-readiness.spec.ts`
- `frontend/playwright.auth.config.ts`
- `docs/launch/firebase-auth-pilot-source-readiness.md`
- `docs/builder/task-07-report.md`

Designer-owned verification input (read-only; do not modify):

- `docs/builder/task-07-source-manifest.txt`

Do not touch Firebase deployment manifests, `.firebaserc`, `firebase.json`, `next.config.ts`, Firestore rules/indexes, workflows, `DEPLOY-SETUP.md`, `.claude/deploy-config.yaml`, rollback automation, companion code, live-audio scripts, generated evidence, or the three protected untracked instruction files (`AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`). If another path appears necessary, stop and report it instead of changing it.

Do not copy, restate, log, fixture, screenshot, or report the named real pilot identities already present in historical design documents. All tests must use obviously synthetic identities. The corporate domain is a public configuration constraint; individual addresses remain outside this task.

## Current source facts and why this task exists

- The existing backend tenancy substrate is strong: Firebase Admin ID tokens use `check_revoked=True`; verified email, allowlist, audience, issuer, server-derived `uid`/`org_id`, non-enumerating ownership checks, one-time WebSocket tickets, bounded stop capabilities, and disabled-by-default extension routes are already present.
- `AUTH_BYPASS` is currently a normal Pydantic boolean. Values such as `1`, `yes`, `on`, and mixed-case truthy strings can become `True`. Nothing refuses a true bypass in Cloud Run, so lifespan can become ready with a token-free synthetic principal.
- `AUTH_ALLOWED_EMAILS` is an unvalidated string. Empty entries are silently discarded and malformed configuration is not rejected before readiness.
- `backend/scripts/check_auth_setup.py` substitutes a fake default when the allowlist is missing and can print PASS without proving bypass-off or a five-account configuration. `scripts/run_staging_preflight.py` then overstates that result as verified Firebase/GCP auth.
- `verify_bearer_token` accepts a session-cookie issuer shape even though it calls the Firebase ID-token verifier. Provider exceptions are chained, and claim values are coerced with `str(...)` rather than requiring strings.
- Session list routes trust the storage adapter's owner/org filter without checking every returned raw record before returning/deserializing it.
- The frontend enables its test bypass only for exact `NEXT_PUBLIC_AUTH_BYPASS=1` outside production, and it hides the application tree until `/api/me` succeeds. However, any HTTP 2xx is accepted without validating the response body against the Firebase `uid` and email.
- Admission has no deadline. Every non-2xx is described as an unauthorized account, including outages. A denied/transient Firebase principal has no explicit retry-versus-use-another-account action.
- Missing Firebase public configuration looks like an ordinary signed-out state until the user clicks. Sign-in/sign-out have no single-flight UI state, and provider sign-out rejection is unhandled after local data is hidden.
- Public API/WS URLs are read independently in many modules and fall back to localhost. `apiFetch` does not verify the destination before obtaining and attaching an ID token. A bad production variable could send a bearer token, WebSocket ticket, or stream key to an unintended origin.
- Existing Playwright configuration always forces the synthetic bypass. Only three pure stale-admission predicates cover frontend authentication today.
- `frontend/src/lib/firebase.ts` initializes an unused Firestore client. Client Firestore access remains deny-all and is not needed for this cockpit auth flow.
- Firebase Hosting is deliberately not configured: no `firebase.json` or `.firebaserc` exists, and deployment/project binding belongs to Task 08.

## 1. Exact runtime-mode and bypass contract

Add a backend setting with the exact field/environment contract:

```python
tars_runtime_mode: Literal["local", "hosted-pilot"] = "local"
```

Use the environment name `TARS_RUNTIME_MODE`. Do not add synonyms.

`AUTH_BYPASS` input must be canonical:

- Python `True`/`False` values remain accepted for tests and direct construction.
- Environment/string values accept only exact lowercase `"true"` or `"false"`.
- Reject `1`, `0`, `yes`, `no`, `on`, `off`, `TRUE`, `False`, whitespace-padded forms, quoted forms, blank, NBSP, controls, and every other spelling before Pydantic can coerce them.

Update the image source to include exact safe defaults in its existing `ENV` instruction:

```dockerfile
TARS_RUNTIME_MODE=hosted-pilot
AUTH_BYPASS=false
```

Keep the existing safe capture/audio/Python defaults. Extend the Task-06 Docker static parser/guard so both new logical keys:

- are present exactly once with exact uppercase source spelling and exact safe value;
- are matched case-insensitively for multiplicity;
- reject lowercase/mixed-case duplicates or overrides;
- cannot be satisfied by comments, `RUN echo`, inert values, or later instructions.

Evaluate effective instructions in the final Docker stage, not a flat aggregation across stages. Safe values that exist only before a later `FROM` must fail. Preserve the existing case-insensitive Docker directive parser and raw non-ASCII rejection.

`.env.example` remains local development and must contain:

```dotenv
TARS_RUNTIME_MODE=local
AUTH_BYPASS=false
```

Do not add real account values.

Extend the root example static guard: `TARS_RUNTIME_MODE=local` must appear exactly once with exact uppercase source spelling and canonical value. Missing, wrong, duplicate, lowercase/mixed-case, quoted/export-confused, or later overriding forms must fail.

### Hosted-runtime binding

Cloud Run injects `K_SERVICE`. Treat hosted mode as true when either:

- `K_SERVICE` is a non-empty string; or
- `TARS_RUNTIME_MODE` is `hosted-pilot`.

Before `get_settings()` performs permissive environment resolution, inspect the supplied/process environment with a small pure validator. In hosted mode it must fail closed unless:

- exactly one case-insensitive logical `TARS_RUNTIME_MODE` key exists, its source spelling is exactly uppercase, and its value is exactly `hosted-pilot`;
- exactly one case-insensitive logical `AUTH_BYPASS` key exists, its source spelling is exactly uppercase, and its value is exactly `false`;
- exactly one case-insensitive logical binding exists for each of `GOOGLE_CLOUD_PROJECT`, `FIREBASE_PROJECT_ID`, `AUTH_ORG_ID`, and `AUTH_ALLOWED_EMAILS`, with exact uppercase source spelling;
- no lowercase/mixed-case collision exists for any of those six protected logical keys.

`K_SERVICE` must not permit an operator override back to local mode. If `K_SERVICE` is set and runtime mode is missing, local, malformed, or duplicated, startup fails.

The pure environment validator must return its raw hosted/local classification. Apply this exact acceptance matrix after `Settings` resolves process environment and any framework-configured dotenv source, before provider initialization:

- `K_SERVICE` non-empty **or resolved `hosted-pilot`**: the raw mapping must be exact hosted mode with every protected uppercase binding present once, and every resolved value must equal its raw value.
- Raw exact local/absent plus resolved `hosted-pilot`: reject. Hosted mode can never come only from dotenv/default/secondary resolution.
- Resolved local: dotenv/default/direct-construction local development remains supported. For each protected logical key present in the raw mapping, require its exact uppercase spelling, no case collision, and equality with the resolved value. Raw-absent local keys may resolve from the existing local dotenv/default source.
- Dotenv-only local `AUTH_BYPASS=true` remains the existing bounded local capability; it is never accepted when `K_SERVICE` is non-empty or resolved mode is hosted.

Reject lowercase-only hosted mode, uppercase-local plus lowercase-hosted, and every matrix mismatch. Add synthetic secondary-source tests for each accepted and rejected row. This closes the gap where Pydantic's case-insensitive or dotenv resolution could bypass hosted source binding without breaking deliberate local development.

Use fixed reason codes/messages. Never echo environment values or the allowlist.

Local mode may still use explicit `AUTH_BYPASS=true` for the existing synthetic developer and live-harness paths. Do not remove that bounded local capability.

## 2. Strict allowlist parsing and startup validation

In `backend/auth.py`, add a pure allowlist parser used by both runtime admission and the offline setup checker.

Required parser contract:

- Input is a comma-separated string.
- Trim only surrounding ASCII spaces/tabs on each item.
- Require at least one item when bypass is false.
- Every item must be at most 254 ASCII characters and contain exactly one `@`.
- The local part is 1–64 characters from ASCII letters, digits, and ``!#$%&'+=?^_`{|}~.-``; it must not start/end with `.` or contain `..`. Apostrophe is allowed as an ordinary atom character; double-quoted-string syntax is not. `*` and `/` are never allowed.
- The domain is at most 253 characters, contains at least two dot-separated labels, and each 1–63-character label matches ASCII letters/digits with optional internal `-`; a label must not start/end with `-`.
- Reject wildcard, display-name syntax, URI syntax, quotes, slash/backslash, whitespace/control characters, empty labels, and every character outside that exact grammar.
- Normalize admitted addresses to lowercase.
- Reject empty items, leading/trailing/double commas, case-insensitive duplicates, Unicode/confusables, NBSP/zero-width characters, CR/LF injection, multiple `@`, wildcard, quoted, URL-like, or display-name forms.
- Return an immutable set/tuple suitable for exact membership checks.
- Every exception string, repr, chained exception, log, and CLI result must be content-free: fixed reason only, never the raw input or individual address.

Add a runtime validator with these rules:

- Local `AUTH_BYPASS=true`: preserve the existing synthetic principal and do not require an allowlist.
- Any bypass-off runtime: require a valid non-empty allowlist and an unpadded 3–63-character lowercase org slug matching `^[a-z][a-z0-9-]{1,61}[a-z0-9]$`.
- Hosted pilot: require exactly five unique addresses, all on the exact corporate domain `ellaexecutivesearch.com`; require `AUTH_ORG_ID` to be exactly `ella-internal`; require explicit nonblank `FIREBASE_PROJECT_ID`; require it to equal `GOOGLE_CLOUD_PROJECT`.
- Before any provider call in every runtime mode, require `GOOGLE_CLOUD_PROJECT` and every non-`None` `FIREBASE_PROJECT_ID` to be unpadded 6–30-character lowercase project IDs matching `^[a-z][a-z0-9-]{4,28}[a-z0-9]$`. Hosted mode requires both explicitly and equal. Reject equal-but-malformed, padded, uppercase, slashed, URL-like, control-bearing, Unicode, too-short, and too-long values.
- Do not require or embed the five local parts in source. Actual identity/spelling verification remains owner-only.

Call the environment-binding validator and runtime auth validator in `backend/main.py::lifespan` while `app.state.ready` is false and **before** ADC, Firebase Admin, Firestore, GCS, Gemini, orphan detection, or any provider/network initialization. A failure must leave readiness false and perform zero provider/storage calls.

Use a small fixed `AuthConfigurationError` (or equivalent) whose messages contain only stable reason codes. Do not use Pydantic errors to interpolate the allowlist.

## 3. Firebase Admin and ID-token admission hardening

Preserve `firebase_auth.verify_id_token(token, check_revoked=True)`.

Tighten the post-verification contract:

- Require the decoded verifier result to be a `Mapping` before accessing a field. Require `sub` to be an unpadded 1–128-character ASCII string with no whitespace/control characters. If `uid` is present, it must satisfy the same grammar and equal `sub` exactly. Do not coerce numbers, lists, dicts, or other values with `str(...)`.
- Require `email` to be an actual non-empty ASCII string with no surrounding whitespace. Canonicalize it as exactly one address under the same grammar before membership checking; comma-bearing or multi-address claims are invalid.
- Require `email_verified is True` exactly.
- Require `aud` to equal the expected Firebase project exactly.
- Accept only the ID-token issuer `https://securetoken.google.com/<expected-project>`. Remove the session-cookie issuer; session cookies have a separate Admin API and are not this product's bearer format.
- Catch Firebase/provider failures without retaining the provider exception as `__cause__` or `__context__`: record only a boolean failure, leave the active `except` block, and then raise fixed `AuthenticationError("invalid bearer token")`. A provider exception containing a distinctive sentinel must not survive recursive inspection of `__cause__`/`__context__`, `str`, `repr`, `traceback.format_exception`, response data, or logs.
- Do not log or return token contents, claims, allowlist entries, provider exception text, or real identity values.

When Firebase Admin is already initialized, verify that the default app's project binding matches the expected project. Perform this local binding check before ADC/provider initialization using only the app's explicitly stored local `projectId` option (or an equivalent task-owned pure binding record). Never access `App.project_id`, its credential, or another lazy accessor that can load ADC. Missing explicit local binding or mismatch fails with a fixed content-free configuration error; do not silently reuse a wrong-project app or include either project value in the exception. Test with a sentinel `project_id` accessor and prove it is never touched.

Add a positive ASGI `/api/me` test with `AUTH_BYPASS=false` where only the Firebase verifier is mocked. It must prove the middleware calls the real admission function with revocation checking and returns the server-derived synthetic principal. Also prove missing/revoked/unverified/unallowlisted/wrong-audience/wrong-issuer cases share the same generic HTTP 401 surface and Bearer challenge.

Update `backend/tests/test_startup_credentials.py` fixtures to supply a fully synthetic valid bypass-off local auth configuration. Add ordering assertions proving both new pure validators run, and can fail, before the ADC probe or any other provider/storage constructor.

## 4. Owner/org postconditions on list results

The storage query remains owner/org filtered, but treat its result as untrusted adapter output.

Before `list_sessions` accesses or returns any raw record, require it to be a `Mapping` and assert its `ownerId` and `orgId` against the authenticated principal.

Before `list_recent_interviews` accesses any field, inspects mode, ID, title, corruption, or deserializes a record, require it to be a `Mapping` and assert that record's persisted scope against the authenticated principal.

A non-mapping, foreign, wrong-org, or unowned record from a malicious/faulty fake adapter must produce the existing non-enumerating 404 and must not leak any record field in response or logs. Preserve legacy direct-call behavior only where auth enforcement is genuinely absent in old unit tests; real ASGI requests remain enforced.

Add malicious-adapter regressions for both endpoints and a distinctive foreign metadata sentinel that must not appear anywhere observable.

## 5. Truthful offline auth-setup checker

Refactor `backend/scripts/check_auth_setup.py` around a pure function that validates an injected `Mapping[str, str]`.

The command must read only the current process environment. Remove `load_dotenv()` and every implicit/default identity value. Do not read `.env`, `frontend/.env.local`, or another file, and do not add an option that silently does so.

Require exact, case-collision-free source bindings for:

- `TARS_RUNTIME_MODE=hosted-pilot`
- `GOOGLE_CLOUD_PROJECT`
- `FIREBASE_PROJECT_ID` (must equal the Google project for this pilot)
- `AUTH_ORG_ID=ella-internal`
- `AUTH_ALLOWED_EMAILS` (strict parser, exact count five, corporate domain only)
- `AUTH_BYPASS=false`
- `NEXT_PUBLIC_AUTH_BYPASS=0`
- all six `NEXT_PUBLIC_FIREBASE_*` public fields from `frontend/.env.example`
- `NEXT_PUBLIC_API_URL`
- `NEXT_PUBLIC_WS_URL`
- `NEXT_PUBLIC_WS_STREAM_URL`

Require `NEXT_PUBLIC_FIREBASE_PROJECT_ID`, `FIREBASE_PROJECT_ID`, and `GOOGLE_CLOUD_PROJECT` to be exactly equal. If an injected non-empty `K_SERVICE` is present, the same hosted-mode binding remains mandatory; it never excuses a missing, local, malformed, or case-colliding runtime mode.

Apply the exact backend project grammar from section 2 to `GOOGLE_CLOUD_PROJECT` and `FIREBASE_PROJECT_ID` before comparing them. Equal malformed values never pass.

This Python checker needs to prove presence and exact runtime/bypass/count/project/org invariants, not duplicate the full TypeScript URL/Firebase grammar. The TypeScript runtime-config tests own detailed public URL validation.

On success, print only fixed status plus the count `5`, followed by an explicit line that no provider or real account was contacted. On failure, print only fixed field/reason codes. Never print values, addresses, tokens, claims, paths to local secret files, or provider remediation commands.

Do not run the checker against the builder machine's current environment. Test its pure function and `main()` only with a fully injected synthetic environment mapping.

Update `scripts/run_staging_preflight.py` so it no longer says Firebase/GCP auth was “verified.” Its success label must say only that the offline auth configuration source preflight passed, and that provider/real-account verification was not performed. Do not otherwise modernize this older script or its stale suite counts in this task.

## 6. One validated frontend public runtime configuration

Create `frontend/src/lib/runtimeConfig.ts` as the only product-code source of frontend Firebase/API/WS **public build configuration**. Next freezes direct `NEXT_PUBLIC_*` reads into the artifact during `next build`; changing container/runtime variables later does not reconfigure that artifact.

Because Next.js inlines only statically named public variables, construct the raw input object with direct property reads such as `process.env.NEXT_PUBLIC_API_URL`; do not dynamically index `process.env` by a computed key.

Export pure parsing helpers for unit tests and one module-level resolved result for production consumers. Never throw at import time: a bad configuration must render the explicit auth configuration-error UI, not crash the page.

### Bypass policy

- Development/test bypass is enabled only by exact `NEXT_PUBLIC_AUTH_BYPASS === "1"` and only when `NODE_ENV !== "production"`.
- In production, every value including `1` leaves bypass disabled.
- Reject ambiguous/noncanonical public-bypass values in the source preflight; the runtime itself must never enable them.

### URL policy

For production:

- `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, and `NEXT_PUBLIC_WS_STREAM_URL` are mandatory; no localhost fallback.
- Reject `localhost`, `127.0.0.1`, and `[::1]` in production even when supplied with `https:`/`wss:`.
- API must be an absolute `https:` origin with no credentials, query, fragment, or non-root path. Normalize one trailing slash away.
- Browser WS must be `wss:` with the exact path `/ws`, no credentials/query/fragment.
- Native stream WS must be `wss:` with the exact path `/api/stream/native`, no credentials/query/fragment.
- All three must have the same hostname and effective port.
- Before URL canonicalization, require an unpadded, entirely ASCII raw value. Reject `http:`, `ws:`, protocol-relative URLs, backslashes, percent-encoded authority/path delimiters, empty query/fragment delimiters, empty or non-empty userinfo, empty ports, controls, every Unicode/IDNA-changing character, and malformed ports.

For non-production only:

- Missing values may use one consistent loopback default: API `http://127.0.0.1:8000`, browser WS `ws://127.0.0.1:8000/ws`, native WS `ws://127.0.0.1:8000/api/stream/native`.
- Explicit `http:`/`ws:` values are allowed only for `localhost`, `127.0.0.1`, or `[::1]` and must still satisfy paths/no-userinfo/no-query/no-fragment/same-host rules.
- Explicit `https:`/`wss:` source-test values remain allowed when consistent.

### Firebase public config policy

- When bypass is disabled, all six fields are mandatory and must already be trimmed, ASCII, nonblank, and match the exact grammars below without controls, whitespace, URL userinfo, angle-bracket placeholders, or case-insensitive `your-`/`example` placeholders.
- API key: exactly 39 characters matching `^AIza[A-Za-z0-9_-]{35}$`.
- Auth domain and storage bucket: 1–253-character ASCII DNS hostnames using the same label grammar as the backend parser; at least two labels, no scheme/path/port.
- Project ID: 6–30 lowercase characters matching `^[a-z][a-z0-9-]{4,28}[a-z0-9]$`.
- Messaging sender ID: 6–20 ASCII digits.
- App ID: `^1:[0-9]{6,20}:web:[A-Za-z0-9_-]{8,128}$`.
- These public values are not server credentials, but do not log or display their values.
- When the local synthetic bypass is enabled, missing Firebase public config is permitted because the Firebase SDK is not used.

Export helpers/accessors used by all frontend modules:

- a safe resolved config/result;
- `requirePublicRuntimeConfig()` (fixed content-free error only);
- an `apiUrl(path)` builder that accepts only `/api` or `/api/...` paths;
- a destination check that resolves `RequestInfo | URL` and requires the exact configured API origin plus `/api` path before a token can be obtained.

Update every listed frontend consumer to remove its independent `process.env` read and localhost fallback. API calls must use `apiUrl(...)`; browser/native sockets and companion join links must use the validated WS bases.

In `apiFetch`, validate the destination **before** calling `getIdToken()`. Reject a cross-origin, non-API, credential-bearing, query-confused base, or malformed destination with a fixed local error and make zero token/provider/network calls. Force `redirect: "error"` on the initial request and retry, after all caller options are applied; callers cannot override it. Treat 3xx/redirect rejection as a fixed failure and make no follow-up or refresh request.

Preserve the one forced-refresh retry for a trusted 401, but bind it to the initiating Firebase principal: snapshot the initiating user and UID, and refresh only that same user if it is still the current user. If the account changes between the 401 and refresh, cancel without obtaining a new token or sending a second request. Revalidate the same original trusted destination before any retry.

Remove the unused Firestore initialization/export from `frontend/src/lib/firebase.ts`. Initialize only Firebase App and Auth after the public configuration result is valid. Use one task-owned named Firebase app; if that named app already exists, compare every relevant option with the resolved public configuration and surface a fixed configuration error on mismatch. Never reuse an arbitrary default/first app. Do not add client Firestore access.

## 7. Bounded, principal-bound admission request

Expand `frontend/src/lib/authAdmission.ts` into a pure/testable async admission operation while preserving `admissionIsCurrent`.

The operation must accept injected fetch/token dependencies and an external abort signal for unit tests. Production default deadline: exactly `10_000` ms.

One deadline and the external abort signal race the **entire** operation from before trusted-URL resolution and token acquisition through fetch and response-body parsing. A hung token promise, fetch, or `response.json()` cannot keep the operation pending. Pre-abort and abort during any stage return promptly. Late promise settlement must make no fetch/state change and cause no unhandled rejection. Define timeout-versus-external-abort precedence deterministically, and clear every timer/listener in every race outcome.

The admission fetch also forces `redirect: "error"` after injected/caller options, and no dependency may override it. A 3xx or redirect rejection is `retryable` and cannot trigger a followed request or a token refresh.

Required outcomes:

- `admitted`: only HTTP 2xx plus valid JSON object with nonblank ASCII string `uid`, `email`, and `org_id`; expected Firebase UID/email must themselves be nonblank canonical ASCII values; returned `uid` equals the Firebase user's UID exactly; returned email normalized to lowercase equals the Firebase user's normalized email; org is nonblank. No other 2xx can admit.
- `denied`: only HTTP 401 or 403.
- `retryable`: 408/425/429/5xx/other non-2xx, network/provider-token failure, timeout, malformed JSON, missing fields, wrong UID, wrong email, or invalid org.
- `cancelled`: external abort/account-generation replacement. It must not alter visible state.

The function must clear its timer/listeners in every terminal path. No result/error may contain the ID token, response body, returned principal fields, raw URL, or provider exception text.

Tests must prove:

- exact valid principal admits;
- malformed/non-object/missing-field 2xx rejects;
- wrong UID and case-normalized wrong email reject;
- 401/403 deny;
- 429/5xx/network/malformed JSON retry;
- never-resolving token, fetch, and response-body promises are each bounded by the deadline and return retryable;
- a pre-aborted signal and external abort during token, fetch, or body parsing return cancelled;
- simultaneous timeout/abort, late settlement, no-unhandled-rejection, and timer/listener cleanup follow the defined deterministic policy;
- stale success, stale denial, and stale timeout cannot commit after a newer generation/account;
- the trusted URL is resolved before token acquisition.

## 8. Recruiter sign-in, retry, account-switch, and sign-out UX

Create `frontend/src/lib/authController.ts` as a pure, dependency-injected state/effect controller with a pure reducer, and make `useAuth` a thin React/Firebase adapter over it. Refactor `AuthControls` without rendering interview data before admission. The controller must make every transition and effect order below executable in `authController.test.ts`; do not leave critical behavior only in JSX callbacks or string-search tests.

Use explicit, testable statuses sufficient to distinguish:

- initial Firebase state;
- signed out;
- opening Google sign-in;
- checking backend access;
- signed in;
- configuration error;
- denied account;
- retryable backend/network error;
- provider/sign-out error.

Exact behavior:

- Invalid/missing public runtime or Firebase configuration is visible immediately in pt-BR, exposes no normal sign-in action, starts no Firebase listener, opens no popup, makes no API/WS call, and renders no authenticated tree.
- Sign-in is single-flight. Disable duplicate activation, set `aria-busy`, and use visible status copy while the popup/access check is pending.
- Configure `GoogleAuthProvider` with `prompt: "select_account"`. Do not add `hd` as an authorization control; the backend allowlist is authoritative.
- Backend 401/403 shows fixed denied-account copy and an explicit **“Usar outra conta”** action. That action must clear/release the current denied Firebase principal before opening the chooser.
- Retryable admission shows fixed connection/unavailable copy and **“Tentar novamente”**. Retry the same current Firebase principal's `/api/me` check without opening another popup.
- Popup cancellation returns to a usable signed-out state with non-alarming fixed copy. Popup-blocked/network/provider errors are caught and mapped to fixed actionable pt-BR messages without exception text.
- Account changes abort/fence old work exactly as today. A late result from account A can never render after account B.
- Sign-out and auth loss synchronously hide/unmount the entire authenticated tree before awaiting Firebase. Preserve the active-interview prohibition: the recruiter must obtain confirmed stop before sign-out.
- Provider sign-out rejection is caught, leaves interview data hidden, shows a fixed recovery message, and never restores the old principal.
- Preserve token refresh-once behavior, WebSocket/capture cleanup on authenticated-tree unmount, stop capability behavior, and the `key={uid}` account-state reset.

All new user-facing copy is Brazilian Portuguese. Use `role="status"`, `aria-live`, `role="alert"`, `disabled`, and `aria-busy` appropriately. Do not display UID, org ID, tokens, raw backend responses, configuration values, or the allowlist.

Controller tests must execute at least: initial/config-error/signed-out transitions; popup single-flight; popup cancellation/provider failure; checking-to-admitted/denied/retryable/cancelled; denied → provider sign-out → chooser ordering (including sign-out failure, which must not open the chooser); same-principal retry without popup; account replacement during every async phase; auth-listener error/loss; synchronous data hiding before provider sign-out; provider sign-out failure with old-principal non-restoration; and late/stale generations producing zero state/effects.

## 9. Offline bypass-off browser evidence

Add `frontend/playwright.auth.config.ts` and one narrow `frontend/e2e/auth-source-readiness.spec.ts`.

The config must:

- use a dedicated loopback port different from the existing bypass suite;
- set `NEXT_PUBLIC_AUTH_BYPASS=0` explicitly;
- explicitly blank all six Firebase public variables and supply only safe loopback API/WS values;
- set `NEXT_TELEMETRY_DISABLED=1` in the web-server environment;
- set `reuseExistingServer: false` so it cannot attach to an already-running bypass-enabled server;
- run only the auth source-readiness spec.

The test must prove the missing-Firebase configuration state:

- fixed visible configuration error;
- no authenticated interview tree;
- no enabled Google sign-in button;
- zero `/api/` requests;
- zero application gateway WebSockets. Next development HMR may create only its loopback `/_next` socket; fail any `/ws` or `/api/stream/native` socket and any non-loopback socket;
- zero external request. Abort any attempted non-loopback request and fail the test.

This is not a Firebase login test. Never configure real Firebase values or interact with Google.

Add a package script named exactly:

```json
"e2e:auth-offline": "playwright test --config=playwright.auth.config.ts"
```

Preserve the existing bypass-enabled Playwright suite and configuration.

Update its shared `frontend/e2e/fixtures.ts` so the more-specific synthetic `POST /api/sessions/<id>/ws-ticket` branch returns a fixed non-secret ticket before the generic session-creation POST branch. This keeps its mocked WebSocket route reachable without any real backend or token.

## 10. Tests and adversarial mutation corpus

Write the failing tests first and capture the RED result in `task-07-report.md` before implementation.

Backend checked-in adversarial cases must include:

- bypass values `1`, `0`, `yes`, `on`, `TRUE`, `False`, padded, quoted, NBSP, blank, and control-bearing;
- hosted `K_SERVICE` with local/missing/malformed runtime mode; lowercase-only hosted mode; uppercase-local plus lowercase-hosted; and raw-local/absent resolving to hosted through a secondary settings source;
- lowercase/mixed-case duplicate runtime/bypass/project/Firebase-project/org/allowlist environment keys and raw/resolved value divergence;
- allowlist leading/trailing/double comma, blank, case-duplicate, wildcard, display name, URI, slash/backslash, multiple `@`, quote, CR/LF, NBSP, zero-width, non-ASCII, four entries, six entries, and wrong domain;
- blank/slashed/wrong/padded/control/Unicode org and blank/mismatched/equal-but-malformed/padded/uppercase/slashed/control/Unicode/boundary-length project binding, all failing before ADC;
- Firebase provider exception containing a distinctive secret sentinel absent from recursively inspected `__cause__`/`__context__`, formatted traceback, response, and logs;
- non-mapping (`None`/list/string) decoded claims; numeric/list/dict UID/email; blank/padded/control/non-ASCII subject; blank/padded/comma-bearing email; missing `sub`; and mismatched `uid`/`sub`, all sharing the generic 401 surface;
- session-cookie issuer rejected;
- wrong or missing explicit-project existing Firebase Admin app whose lazy `project_id` accessor is a sentinel that must not be touched;
- non-mapping/foreign/unowned/wrong-org records returned by both list adapter paths;
- setup checker missing each required binding, missing/local/malformed/case-colliding runtime mode, hosted `K_SERVICE` without hosted mode, true/ambiguous bypass, public bypass not exact `0`, wrong count, duplicates, any of the three project IDs mismatching, and sentinel-bearing values absent from output;
- Docker safe values only before a second `FROM`, plus final-stage missing/duplicate/case-colliding/unsafe runtime and bypass values;
- root `.env.example` missing/duplicate/case-colliding/wrong `TARS_RUNTIME_MODE=local`.

Frontend checked-in cases must include:

- production bypass disabled for every input including `1`; development bypass enabled only for exact `1`;
- missing/partial/blank/whitespace/placeholder/malformed Firebase fields;
- exact Firebase public-field boundary lengths/grammars and table-driven reused named-app mismatch tests that mutate each of API key, auth domain, project ID, storage bucket, sender ID, and app ID individually;
- production missing URLs, secure loopback, HTTP/WS schemes, empty/non-empty userinfo, empty/non-empty query or fragment delimiters, padding, wrong paths, inconsistent hosts/ports, empty/malformed ports, percent/backslash/control/IDNA-changing Unicode tricks;
- allowed loopback dev defaults and explicit consistent production-like synthetic URLs;
- cross-origin/non-API `apiFetch` rejected before token acquisition; same-origin and cross-origin redirects rejected with no follow-up/refresh; account switch between 401 and refresh cancels the retry;
- admission valid/malformed/mismatch/status/network plus hung-token/fetch/body timeout and abort-race matrix;
- the complete auth-controller transition/effect-order matrix required in section 8.

Do not assert these protections only by searching strings. Tests must execute the pure parser/validator/admission behavior and include mutation-style inputs that would pass if a key guard were removed.

## 11. Documentation and evidence ceilings

Update `docs/launch/cloud-run-pilot-source-readiness.md` so Task 07 is split honestly:

- source hardening and synthetic/mocked bypass-off proof can pass here;
- exact real-account spelling/existence and one real allowed/denied login remain owner-only;
- Firebase Hosting/project binding and deployment remain Task 08.

Create `docs/launch/firebase-auth-pilot-source-readiness.md` with:

- exact source contracts proven;
- commands/counts from this task;
- explicit `SOURCE PASS / OWNER REAL-AUTH NOT RUN` state;
- owner gate requiring five privately verified identities, one allowed and one denied real sign-in, account switch, retry, logout, revocation/refresh, real `/api/me`, exact SHA/operator/timestamp;
- evidence privacy rule: record only labels/counts/PASS-FAIL, never emails, tokens, claims, screenshots with identities, or local env contents;
- Task-08-only Firebase project/provider/authorized-domain/API-key/Hosting/Cloud Run binding and deployment;
- explicit Next build-time binding: Task 08 must inject the real `NEXT_PUBLIC_*` values for each clean frontend build, and any public-config change requires a rebuild/requalification rather than a container/runtime environment edit;
- statement that `firebase.json`, `.firebaserc`, and Hosting configuration are intentionally absent/not created by Task 07.

The builder report must list every changed file, RED and GREEN commands with exact outputs, adversarial probe counts, and every skipped ceiling. It must not reproduce real identities or any local environment value.

## 12. Required verification commands

Run only offline/local commands. Do not run the auth setup CLI against the real process environment; its behavior is covered by injected tests. Do not run `npx`, package installation, or another command that can download a missing tool. Set `NEXT_TELEMETRY_DISABLED=1` for every Next/Playwright process.

### Clean-room source mirror

Before **every RED or GREEN Python, frontend, Playwright, or build gate**, create a fresh ephemeral mirror from the current working tree. This is verification isolation, not a second implementation tree: edit only the real workspace, then copy only the positive paths in the designer-owned `docs/builder/task-07-source-manifest.txt`. The manifest contains the required root/backend/frontend/native source and tests plus five explicitly named Task-07/readiness documents; it deliberately omits historical identity-bearing docs, arbitrary untracked files, recordings/evidence, worktrees, build/dist outputs, caches, hidden configuration, protected instructions, Git metadata, and every non-example env file. Do not add to or modify the manifest.

```bash
set -euo pipefail
TASK07_SOURCE_DIR="/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"
TASK07_VERIFY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tars-task07-verify.XXXXXX")"
TASK07_MANIFEST="$TASK07_SOURCE_DIR/docs/builder/task-07-source-manifest.txt"
TASK07_EXISTING_PATHS="$(mktemp "${TMPDIR:-/tmp}/tars-task07-paths.XXXXXX")"
while IFS= read -r task07_manifest_entry; do
  case "$task07_manifest_entry" in
    ""|/*|../*|*/../*|*/..) echo 'TASK07_VERIFY_MANIFEST_INVALID'; exit 1 ;;
  esac
  if [ -L "$TASK07_SOURCE_DIR/$task07_manifest_entry" ]; then
    echo 'TASK07_VERIFY_SOURCE_SYMLINK_REJECTED'
    exit 1
  fi
  if [ -e "$TASK07_SOURCE_DIR/$task07_manifest_entry" ]; then
    if [ ! -f "$TASK07_SOURCE_DIR/$task07_manifest_entry" ]; then
      echo 'TASK07_VERIFY_SOURCE_TYPE_REJECTED'
      exit 1
    fi
    printf '%s\n' "$task07_manifest_entry" >> "$TASK07_EXISTING_PATHS"
  fi
done < "$TASK07_MANIFEST"
rsync -a --files-from="$TASK07_EXISTING_PATHS" "$TASK07_SOURCE_DIR/" "$TASK07_VERIFY_DIR/"
if [ ! -d "$TASK07_SOURCE_DIR/.venv" ] || [ ! -d "$TASK07_SOURCE_DIR/frontend/node_modules" ]; then
  echo 'TASK07_VERIFY_DEPENDENCY_MISSING'
  exit 1
fi
ln -s "$TASK07_SOURCE_DIR/.venv" "$TASK07_VERIFY_DIR/.venv"
ln -s "$TASK07_SOURCE_DIR/frontend/node_modules" "$TASK07_VERIFY_DIR/frontend/node_modules"
if find "$TASK07_VERIFY_DIR" -name '.env*' ! -name '.env.example' -print -quit | grep -q .; then
  echo 'TASK07_VERIFY_ENV_ISOLATION_FAILED'
  exit 1
fi
TASK07_CLEAN_HOME="$TASK07_VERIFY_DIR/.task07-home"
TASK07_CLEAN_TMP="$TASK07_VERIFY_DIR/.task07-tmp"
TASK07_XDG_CONFIG="$TASK07_VERIFY_DIR/.task07-xdg-config"
TASK07_XDG_CACHE="$TASK07_VERIFY_DIR/.task07-xdg-cache"
TASK07_NPM_CONFIG="$TASK07_VERIFY_DIR/.task07-npmrc"
TASK07_PLAYWRIGHT_BROWSERS_DIR="$HOME/Library/Caches/ms-playwright"
mkdir -p "$TASK07_CLEAN_HOME" "$TASK07_CLEAN_TMP" "$TASK07_XDG_CONFIG" "$TASK07_XDG_CACHE"
: > "$TASK07_NPM_CONFIG"
if [ ! -d "$TASK07_PLAYWRIGHT_BROWSERS_DIR" ]; then
  echo 'TASK07_VERIFY_BROWSER_DEPENDENCY_MISSING'
  exit 1
fi
task07_run() {
  env -i \
    PATH="$PATH" \
    HOME="$TASK07_CLEAN_HOME" \
    TMPDIR="$TASK07_CLEAN_TMP" \
    LANG=C \
    LC_ALL=C \
    CI=1 \
    PYTHONUTF8=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XDG_CONFIG_HOME="$TASK07_XDG_CONFIG" \
    XDG_CACHE_HOME="$TASK07_XDG_CACHE" \
    NPM_CONFIG_USERCONFIG="$TASK07_NPM_CONFIG" \
    NPM_CONFIG_OFFLINE=true \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false \
    NEXT_TELEMETRY_DISABLED=1 \
    PLAYWRIGHT_BROWSERS_PATH="$TASK07_PLAYWRIGHT_BROWSERS_DIR" \
    "$@"
}
cd "$TASK07_VERIFY_DIR"
```

Do not use `rm`, `mv`, or temporary renaming on any source env file. Do not preserve/inherit arbitrary auth, public-config, Firebase/GCP/cloud, credential-path, proxy, npm-user, or provider environment variables. A verification process that reports loading a non-example env file is a failed isolation gate and forces `SOURCE INCOMPLETE`; do not inspect that file to diagnose it. Record the ephemeral mirror path and fixed isolation PASS/FAIL labels only—never source env filenames or contents.

Before the test suites, prove hostile parent variables do not cross the `env -i` boundary:

```bash
AUTH_BYPASS=hostile K_SERVICE=hostile GOOGLE_APPLICATION_CREDENTIALS=/hostile \
NEXT_PUBLIC_API_URL=https://hostile.invalid HTTP_PROXY=http://hostile.invalid \
task07_run .venv/bin/python -c 'import os; blocked=("AUTH_","NEXT_PUBLIC_","GOOGLE_","FIREBASE_","CLOUDSDK_","AWS_","AZURE_"); exact={"K_SERVICE","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY"}; assert not any(k.startswith(blocked) or k in exact for k in os.environ); print("TASK07_CLEAN_ENV_PASS")'
```

The designer, not the builder, owns Git/tree identity. At handoff the designer attests that `ffd6d1f` is the product-code prerequisite and that the committed brief is its direct documentation child. The builder must report this as “asserted by designer; not builder-verified” and must not run Git.

From the clean-room mirror root:

```bash
task07_run .venv/bin/python -m pytest backend/tests/test_auth.py backend/tests/test_auth_matrix.py backend/tests/test_task07_auth_source_readiness.py backend/tests/test_cloud_run_readiness.py backend/tests/test_startup_credentials.py -q
task07_run .venv/bin/python -m pytest backend/tests -q
task07_run .venv/bin/python -m py_compile backend/config.py backend/auth.py backend/main.py backend/scripts/check_auth_setup.py scripts/run_staging_preflight.py
```

From the clean-room mirror `frontend/`:

```bash
task07_run npm test
task07_run ./node_modules/.bin/tsc --noEmit --incremental false
task07_run npm run e2e:auth-offline
task07_run npm run e2e
task07_run env \
  NEXT_PUBLIC_API_URL=https://backend.invalid \
  NEXT_PUBLIC_WS_URL=wss://backend.invalid/ws \
  NEXT_PUBLIC_WS_STREAM_URL=wss://backend.invalid/api/stream/native \
  NEXT_PUBLIC_AUTH_BYPASS=0 \
  NEXT_PUBLIC_FIREBASE_API_KEY=AIzaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA \
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=source-auth.invalid \
  NEXT_PUBLIC_FIREBASE_PROJECT_ID=source-auth-pilot \
  NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=source-bucket.invalid \
  NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=123456789012 \
  NEXT_PUBLIC_FIREBASE_APP_ID=1:123456789012:web:abcdef0123456789 \
  npm run build
```

The production build is clean-room compile/source evidence only. It does not prove a real Firebase app, real public build configuration, Hosting compatibility, deployment, or hosted behavior.

Standing untouched-surface gates from the clean-room mirror root:

```bash
(cd companion/native-macos && task07_run swift test)
(cd companion/native-macos && task07_run swift build)
```

Do not run Docker, Firebase CLI, gcloud, ADC, browser login, live audio, provider, deployment, or network-dependent audit commands.

## 13. Mandatory report format

Create `docs/builder/task-07-report.md` containing:

1. exact prerequisite `ffd6d1f` stated as “asserted by designer; not builder-verified” (do not run Git to rediscover it);
2. changed-file list and confirmation that only allowlisted paths changed;
3. RED tests and why they failed before implementation;
4. implementation summary by backend runtime/auth, frontend config/destination, admission/UX, and evidence boundary;
5. exact GREEN command outputs/counts;
6. adversarial mutation inventory/count;
7. explicit skipped/not-run list;
8. exact final label `SOURCE PASS / OWNER REAL-AUTH NOT RUN` only if every source gate passes;
9. confirmation that no real identity was copied, logged, tested, or placed in examples/report;
10. confirmation that no Git command or manual local-env inspection/copy occurred; all executable gates ran in an env-excluding clean-room mirror; no provider/network/cloud/login/deploy/live-audio action occurred.

If any required gate cannot run or pass, report `SOURCE INCOMPLETE / OWNER REAL-AUTH NOT RUN` and explain the exact blocker. Never convert a skipped or mocked check into live proof.
