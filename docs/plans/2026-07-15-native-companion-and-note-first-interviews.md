# T.A.R.S. Native Companion and Note-First Interview Plan

**Date:** 2026-07-15

**Status:** Architecture direction accepted. Phase 0B is complete. Phase 1A offline work is explicitly authorized; guard-first commit `f7c16f2` awaits review before conformance. Phases 1B-1D remain blocked.

**Scope:** Governing plan and gate definition; no implementation is authorized by this document

**Primary outcome:** A macOS-first, provider-independent interview companion that captures device audio, retains no raw audio by default, lets recruiters take timestamped notes, and produces evidence-grounded assessments.

## 1. Product decision

T.A.R.S. should adopt a device-level native companion architecture while remaining focused on executive-search interviews.

The product should not compete as a generic meeting notetaker. Provider-independent capture is foundational infrastructure. The differentiating workflow is:

1. Prepare from the CV and job description.
2. Capture any interview without adding a meeting bot.
3. Help the recruiter cover the right competencies.
4. Treat recruiter notes and ratings as high-signal input.
5. Produce a report whose conclusions are traceable to transcript evidence and recruiter judgment.

## 2. Decisions requiring approval

These defaults were accepted as the architecture direction on 2026-07-15. That acceptance does not clear the active containment gate or authorize implementation. Any change to them must update the architecture decision record before implementation proceeds.

| Decision | Recommended default | Consequence |
| --- | --- | --- |
| Initial platform | macOS first | Proves the native capture model before carrying two operating-system stacks. |
| Product focus | Executive search first | Keeps CV/JD preparation, live coaching, and assessment as the differentiator. |
| Speech recognition | Google Cloud STT remains acceptable for the first release | Audio leaves the device transiently; the product must disclose this and verify project data-logging settings. |
| Raw audio retention | No persistent raw audio by default | Audio lives only in a bounded in-memory buffer until acknowledged. Any disk recovery buffer requires a separate opt-in decision. |
| Interface strategy | Reuse the Next.js workspace initially | Avoids a full UI rewrite while introducing a native capture and permission layer. |
| Speaker integrations | Provider-specific integrations are optional enrichment | The baseline works on any call; the Google Meet extension cannot be foundational. |
| Hiring decision posture | Human decision support, not autonomous selection | AI ratings require evidence, uncertainty, recruiter review, and explicit sign-off. |

## 3. Current-state facts

- The Python backend already captures two local audio sources: BlackHole system audio and the default microphone.
- Audio capture is coupled to the FastAPI process, making the working application local-first even though deployment scaffolding targets Cloud Run.
- Every captured chunk is currently written to a local FLAC backup, with no automated deletion path.
- Cloud STT provides the transcript; Gemini provides rolling summaries, interview guidance, and final assessments.
- The frontend REST URL is hardcoded to localhost; only the WebSocket URL is configurable.
- Active session state, interview context, WebSocket replay state, and summary progress are primarily in memory.
- Firebase initialization exists, but user authentication and resource ownership are not enforced by the application.
- The staging checkout contains uncommitted speaker-correlation and Google Meet extension work.
- The Meet extension improves participant naming but depends on provider-specific DOM selectors.
- CI builds the frontend and installs backend dependencies but runs no automated tests.

## 4. Product experience

### 4.1 First-run onboarding

The companion guides the user through:

1. Sign-in and organization selection.
2. Microphone permission.
3. System-audio permission.
4. Device and headset test.
5. Plain-language privacy disclosure explaining what remains local and what is sent to Google Cloud.
6. Consent and notification expectations.
7. A short test transcription that proves both local and remote audio paths.

The user must never need to configure BlackHole for the default path. A virtual device may remain a documented compatibility fallback.

### 4.2 Interview preparation

The existing candidate name, CV, JD, and briefing workflow remains. It expands to include:

- A recruiter-selected interview template or competency model.
- A generated interview agenda.
- Required competencies and evidence targets derived from the JD.
- Questions the recruiter may pin, edit, or remove before the interview.
- Retention settings for the CV, JD, transcript, notes, and report.

### 4.3 Live interview workspace

The live workspace contains four coordinated surfaces:

1. **Capture status:** always-visible indicator, device health, elapsed time, pause/resume, and stop.
2. **Transcript:** live text with `You` versus remote speakers, confidence, and easy correction.
3. **Recruiter notes:** timestamped notes, bookmarks, concerns, ratings, and follow-up reminders.
4. **Interview coverage:** competencies explored, missing evidence, contradictions, and suggested next questions.

Suggested shortcuts:

- Mark the current moment.
- Add a concern.
- Add a strength.
- Add a follow-up.
- Pause or resume transcription.
- Exclude the current passage from the final report.

Suggestions should be event-driven rather than emitted every fixed number of transcript segments. Useful triggers include:

- An answer ends without concrete evidence.
- A JD requirement has not been explored.
- The candidate contradicts the CV or an earlier answer.
- The recruiter marks a concern or follow-up.
- A long period passes without covering a planned competency.

### 4.4 Post-interview review

The final workspace separates:

- Candidate statements supported by transcript excerpts.
- Recruiter-authored notes and ratings.
- AI-generated inference and synthesis.
- Missing or insufficient evidence.

Every competency rating or material conclusion must link to supporting evidence or explicitly say that evidence is insufficient. The recruiter can edit, accept, or reject every section before export.

The product must provide:

- Delete session everywhere.
- Export transcript, notes, and report separately.
- A visible retention date.
- An audit entry for report approval and export.

## 5. Target architecture

### 5.1 Native macOS companion

Responsibilities:

- Application signing, notarization, installation, and updates.
- Microphone and system-audio permission lifecycle.
- Native audio capture using OS-supported APIs.
- Independent mic and system-audio channels.
- Audio resampling and framing.
- A bounded in-memory queue.
- Authenticated session and device identity.
- A durable text/event outbox for notes and final transcript acknowledgements.
- Connection health, retry, and backpressure.
- Visible capture state and user controls.
- Secure token storage in Keychain.

The companion must not contain permanent Google Cloud credentials.

### 5.2 Cloud control and intelligence plane

Responsibilities:

- Authenticate the companion and browser UI.
- Enforce user and organization ownership.
- Accept ordered, idempotent audio chunks and session events.
- Maintain STT streams and return transcript deltas.
- Persist final transcript segments, notes, and lifecycle events.
- Generate interview suggestions and final assessments.
- Run retention and deletion jobs.
- Provide audit events and operational telemetry.

Cloud Run remains appropriate for this plane once device capture is removed from the container.

### 5.3 Web interview workspace

Responsibilities:

- Preparation, live transcript, notes, coverage, and report review.
- Session library and search.
- User-visible retention and deletion controls.
- Evidence-linked rendering.

The REST and WebSocket base URLs must use environment configuration rather than hardcoded localhost values.

### 5.4 Optional provider adapters

The Google Meet extension becomes an optional adapter that may provide:

- Participant names.
- Active-speaker events.
- Meeting metadata.

The product must remain functional if the adapter breaks or is absent. Future Zoom or Teams integrations require evidence that participant naming materially improves recruiter outcomes.

## 6. Session and event contract

The normative transport contract is `docs/architecture/0002-companion-stream-protocol.md`. It must be implemented and tested before native audio reaches a hosted endpoint. Minimum event families include:

- `session.start`
- `audio.chunk`
- `audio.admitted`
- `audio.forwarded`
- `transcript.interim`
- `transcript.final`
- `transcript.durable`
- `capture.gap`
- `note.create`
- `note.update`
- `note.delete`
- `bookmark.create`
- `speaker.correct`
- `capture.pause`
- `capture.resume`
- `session.stop`
- `session.complete`
- `error`

Every client-originated event requires:

- Organization ID and user ID derived from authentication, not trusted from request data.
- Session ID.
- Device ID.
- Stable event ID.
- Monotonic per-source sequence number.
- Capture timestamp.
- Protocol version.

Server processing must be idempotent. A reconnect may resend events without creating duplicate transcript segments, notes, or summaries.

Acknowledgement semantics are deliberately distinct:

- Gateway admission means an authenticated, authorized chunk is present in a bounded gateway queue. It does not permit the companion to release audio.
- Provider forwarding means a contiguous chunk range was written to the active STT stream and its content-free forwarding metadata was journaled. This is the companion's raw-audio release watermark.
- Durable transcript acknowledgement means a final transcript event and its coverage range were committed. It does not control raw-audio release.

Every lost or untranscribed range must become a durable `capture.gap` with exact source, sequence, sample, time, and reason ranges. The full retry, fencing, deterministic-ID, release, and gap rules live in ADR companion contract 0002.

## 7. Data model

Minimum durable entities:

- `organizations`
- `users`
- `memberships`
- `sessions`
- `session_participants`
- `transcript_segments`
- `recruiter_notes`
- `bookmarks`
- `competency_definitions`
- `competency_evidence`
- `assessment_versions`
- `documents`
- `deletion_jobs`
- `audit_events`

Every stored object must carry an owner or organization boundary, creation time, retention policy, and deletion state where applicable.

Final transcript segment IDs and note IDs must be stable across reconnects. Assessment reports are versioned so recruiter edits and AI regenerations do not overwrite the approved record.

## 8. Privacy and security contract

### 8.1 Raw audio

- No local FLAC or other raw-audio file is created by default.
- Audio is held in memory only until the corresponding server/STT acknowledgement.
- Buffers are bounded by duration and memory size.
- On overflow, the product reports degraded capture rather than silently retaining unlimited data.
- Session stop, logout, permission revocation, and process shutdown clear memory buffers.
- Crash tests must prove that no unexpected raw-audio artifact remains.

If an encrypted disk spool is later approved, it requires:

- Explicit product and legal approval.
- Per-device encryption with Keychain-held keys.
- A strict size and age limit.
- Verified deletion after acknowledgement or TTL expiry.
- A user-visible setting and disclosure.

### 8.2 Cloud processing

- Audio transport uses TLS and short-lived authenticated sessions.
- Google Cloud STT data logging is disabled and verified in the intended project.
- Vertex AI retention and cache settings are inventoried and configured before privacy claims are published.
- The privacy disclosure states that audio is transmitted to a cloud transcription provider.
- CV, JD, transcript, notes, and prompts are included in the data-flow inventory.

### 8.3 Consent and user control

- The companion shows an unmistakable active-transcription indicator.
- Session start requires a consent acknowledgement appropriate to the product's legal guidance.
- Pause/resume is immediately available.
- Users can exclude a passage from downstream summarization.
- Users and authorized organization administrators can delete session data.
- T.A.R.S. does not claim that silent capture removes the user's consent obligations.

### 8.4 Authentication and isolation

- All hosted REST and WebSocket operations require authentication before native audio or real user data is accepted.
- The server derives the caller's user and organization membership.
- Every storage read and write is scoped to that membership.
- Extension/session tokens are short-lived, scoped, revocable, and never substitutes for user ownership.
- Cross-tenant access tests cover REST, WebSocket, Firestore, GCS, exports, and deletion.

## 9. Hiring decision safeguards

T.A.R.S. is decision support. It must not silently turn model output into an employment decision.

Required controls:

- Separate candidate evidence, recruiter judgment, and AI inference.
- Require evidence links for ratings and recommendations.
- Support `insufficient evidence` as a first-class outcome.
- Preserve the recruiter-authored value and the AI-suggested value separately.
- Require human approval before a report is marked final.
- Do not rank candidates automatically in the initial scope.
- Add evaluation cases for unsupported claims, contradictions, protected-trait leakage, and overconfident conclusions.

## 10. Delivery phases and gates

### Phase 0A: Reconcile documentation and preserve the repository boundary

Work:

- Classify canonical, target, historical, prototype, generated, and active-configuration surfaces.
- Align status and approval language across the governing plan, ADRs, privacy contract, product-state contract, and phase plans.
- Inventory tracked, untracked, ignored, and generated files, including the ignored extension manifest.
- Record current prototype behavior without representing it as the target product.
- Create a data-flow inventory covering audio, transcript, CV, JD, notes, prompts, and reports.

Exit gate:

- The canonical hierarchy and panel decision are cross-linked from the repository entry point.
- Existing work and ignored source cannot be lost or accidentally mixed with later implementation.
- Current behavior and target requirements are distinguishable.
- Documentation states that it does not neutralize active deployment or data-exposure risk.

### Phase 0B: Contain the prototype and establish an isolated development boundary

This phase changes active deployment/security configuration and requires separate user authorization. Documentation-only approval does not authorize it.

**Execution update, 2026-07-15:** Immediate deployment and anonymous-access paths were contained. The isolated project is billed and configured as an inactive synthetic-only boundary: private empty Firestore/GCS resources, an empty secret container, a disabled least-privilege runtime identity, disabled STT data logging, disabled Vertex cache, a BRL 250 monthly project budget, and no hosted endpoint. The unfinished feature work is preserved on a separate local branch, a clean implementation worktree is named, protected pull-request boundaries are active on `main`/`staging`, and approval-gated GitHub environments exist. The panel re-review completed with no P0 objections and conditionally cleared Phase 1A offline only. Phase 0B is complete. Direct evidence is in `docs/current-state/phase-0b-containment-evidence.md`.

Work:

- Disable push-triggered deployment or require a protected, explicit approval gate.
- Remove unauthenticated access from every deployed environment.
- Inventory live GitHub environment/WIF state and live Cloud Run IAM, ingress, revisions, traffic, identities, environment variables, Firestore, GCS, Firebase, logs, and stored data.
- Contain any discovered public or cross-tenant exposure.
- Establish an isolated development project, service identity, datastore, bucket, quotas, secrets, and provider settings.
- Verify Google STT data logging and Vertex AI retention/cache settings in that exact project.
- Preserve the dirty speaker-correlation and extension work, including ignored source, using a separately authorized branch/worktree operation.
- Select a clean, named implementation baseline.

Exit gate:

- Pushes to `staging` or `main` cannot deploy without the approved release gate.
- Unauthenticated and cross-tenant requests are rejected in direct tests.
- Live-state evidence identifies and contains every current service and data store in scope.
- The development environment is isolated and its STT/Vertex settings are recorded.
- Existing source is recoverable and the clean baseline is named.
- The review panel re-runs and records the exact Phase 1 gate, authorization boundary, and remaining blocks.

### Phase 1: Gated protocol, gateway, native capture, and integration spike

The detailed gate table is normative in `docs/plans/2026-07-15-phase-1-native-capture-spike.md`:

1. **Phase 1A — offline protocol conformance:** fixed synthetic bytes, canonical schema/bindings, deterministic provider simulation, no credentials or network. This gate is explicitly authorized; its guard-first slice awaits review before conformance continues.
2. **Phase 1B — hosted allowlisted fixtures:** isolated authenticated gateway and STT using server-issued fixture manifests only. It remains blocked behind security design, exact-project attestation, lower quotas, least privilege, fresh containment evidence, a tested kill switch, and separate authorization.
3. **Phase 1C — offline native capture:** controlled generated-fixture routing into an in-memory/null sink with network/provider access disabled. It remains blocked behind hardware/OS, benchmark, privacy, accessibility/language, contamination, and separate-authorization gates.
4. **Phase 1D — integrated synthetic end-to-end:** combines passed 1B and 1C controls for native-to-hosted-STT verification. It remains separately blocked and does not permit ambient/human audio.

Shared exit principles:

- Delivery is at least once and processing is idempotent; every attempt-independent known coverage range has exactly one non-overlapping terminal transcript-or-gap outcome.
- Unknown forced-termination boundaries are reported as unknown coverage, not fabricated exact gaps.
- Physical capture, transport, per-source health, coverage, finalization, and deletion remain independent user-visible truths.
- No raw-audio artifact remains after the applicable success, stop, discard, contamination, or forced-termination checks.
- A gate approval never authorizes a later gate, push, merge, deployment beyond an explicitly approved 1B mutation set, real data, or legacy-data mutation.

### Phase 2: Secure internal alpha

Work:

- Build the signed macOS companion shell.
- Expand the spike's minimal authentication, secure token storage, and organization ownership into production-ready account and membership flows.
- Move audio acquisition out of the Cloud Run backend path.
- Add the authenticated streaming gateway and durable transcript event handling.
- Add configurable REST and WebSocket endpoints.
- Add basic timestamped notes and bookmarks.
- Persist session state and recover after process/backend restart.
- Add deletion and retention jobs.

Exit gate:

- Internal users can install, authorize, start, pause, resume, stop, and delete a session.
- Backend or network restart does not lose accepted final transcript segments or recruiter notes.
- Cross-tenant isolation and deletion tests pass.
- No external pilot data is accepted before these conditions pass.

### Phase 3: Note-first interview differentiation

Work:

- Build the full recruiter notepad and shortcuts.
- Add JD-derived competency coverage.
- Replace fixed-interval suggestions with coverage- and evidence-driven triggers.
- Link notes, transcript excerpts, and competencies.
- Version assessment reports and require recruiter approval.
- Add evidence and uncertainty requirements to report generation.

Exit gate:

- Every material report conclusion is linked to evidence or marked insufficient.
- Recruiter notes remain distinguishable from model-generated content.
- Evaluation shows that notes materially influence the correct report sections without rewriting quoted evidence.

### Phase 4: External beta hardening

Work:

- Complete capture, privacy, security, recovery, and quality test matrices.
- Add signed updates and operational rollback.
- Add audit logging, alerts, quotas, and cost dashboards.
- Complete privacy, consent, and customer-facing data-flow documentation.
- Add export and deletion verification.
- Evaluate the Meet extension as an optional speaker-name adapter.

Exit gate:

- Release checklist is satisfied with direct evidence.
- Privacy claims match observed application and vendor behavior.
- Capture success and transcript/report quality meet approved beta SLOs.
- Support can diagnose and recover failed sessions without accessing raw audio.

### Phase 5: Expansion

Candidates for later prioritization:

- Windows companion using native loopback capture.
- Optional on-device STT for regulated or offline customers.
- ATS/CRM integrations.
- Organization templates and policy controls.
- Additional provider adapters.
- Candidate comparison only after a separate fairness, legal, and product review.

## 11. Verification strategy

### 11.1 Native capture matrix

- Meet, Zoom, Teams, Slack Huddles, browser media, and local media.
- Built-in microphone/speakers, wired headset, USB device, Bluetooth headset, and aggregate devices.
- Device switching during an active session.
- Sleep/wake, screen lock, permission revocation, and application restart.
- Headphones versus speakers and echo/feedback behavior.
- 60- and 90-minute sessions.

### 11.2 Reliability and recovery

- Network loss and reconnect.
- STT outage and throttling.
- Companion crash.
- Backend restart.
- Duplicate and out-of-order events.
- Stream rotation.
- Stop while disconnected.
- Delete while a summary job is running.

### 11.3 Privacy and security

- Filesystem scan after success, stop, crash, TTL expiry, logout, and deletion.
- Network inspection confirming the documented data flow.
- Authentication expiry and revocation.
- Cross-tenant access attempts for every resource and transport.
- Retention-job and deletion-job verification.
- Audit-event integrity.

### 11.4 Transcript and speaker quality

- Brazilian Portuguese accents and regional variation.
- English and Portuguese code-switching.
- Executive and technical vocabulary.
- Names, companies, numbers, and dates.
- Interruptions and overlapping speech.
- Self/remote attribution and correction.
- Speaker-name enrichment with and without a provider adapter.

### 11.5 Assessment quality

- Unsupported-claim rate.
- Evidence-link accuracy.
- CV/transcript contradiction detection.
- Correct handling of missing evidence.
- Recruiter-note inclusion and attribution.
- Recruiter edit rate before approval.
- Protected-trait and irrelevant-personal-information leakage.
- Repeatability across identical input fixtures.

## 12. Initial SLO proposals

These are proposed targets to validate during Phase 1, not claims about current behavior.

- Capture starts successfully in at least 98% of runs across the approved beta device matrix.
- No unintended persistent raw-audio artifact is found in any privacy test.
- Accepted final transcript segments and notes are not lost during tested reconnect/restart scenarios.
- Duplicate final transcript segments or notes are not created by retries.
- The live transcript remains useful at the approved p95 latency measured during the spike; establish the numeric threshold from observed endpointing behavior.
- Every final assessment rating has evidence, recruiter input, or an explicit insufficient-evidence state.
- Session deletion reaches a verified terminal state within the approved deletion window.

## 13. Migration strategy

The migration should reuse valuable product work rather than rewrite the entire application.

1. Keep the current Next.js interview experience as the initial workspace.
2. Define an audio-source boundary in the backend so local `sounddevice` capture can be removed from the cloud execution path.
3. Introduce the versioned companion/gateway protocol alongside the current local pipeline behind a feature flag.
4. Disable local FLAC recording by default before any privacy claim or external beta.
5. Add owner/organization fields and authorization before exposing hosted session endpoints.
6. Move in-memory session recovery to durable events and idempotent projections.
7. Migrate interview suggestions and summaries to consume durable transcript/note events.
8. Keep the Meet extension on a separate optional adapter path.
9. Remove the legacy local Python capture path only after the native companion meets parity and rollback criteria.

## 14. Proposed implementation sequence

Each item should remain a small, reviewable pull request with its own verification evidence.

1. Reconcile documentation and inventory all tracked, untracked, ignored, and generated work.
2. Obtain separate authorization and complete the Phase 0B deployment/security containment gate.
3. Preserve the existing dirty speaker-correlation work and select a clean implementation baseline.
4. Re-run the plan panel and record the conditional Phase 1A-only decision.
5. Obtain explicit Phase 1A authorization; add offline test scaffolding with networking disabled and fixed synthetic fixtures.
6. Implement the canonical protocol schema/bindings, acknowledgement/release rules, attempt-independent coverage semantics, and offline conformance tests.
7. After separate Phase 1B authorization, add minimal authenticated companion enrollment, revocation, limits, exact-project enforcement, and organization-scoped sessions.
8. Add the isolated development streaming gateway and durable transcript/gap event store for allowlisted fixtures only.
9. After separate Phase 1C authorization, add an offline macOS fixture-capture spike outside the production path with no-persistent-audio verification.
10. After separate Phase 1D authorization, run integrated native-to-hosted synthetic verification; only later add the signed internal companion shell, permission UX, and authoritative state synchronization.
11. Add timestamped notes and bookmarks with explicit synchronization states.
12. Add durable recovery, retention, deletion, and audit events.
13. Add competency coverage and evidence-linked reports.
14. Run the full internal-alpha gate.
15. Add optional Meet enrichment only after the provider-independent path passes.
16. Prepare external beta deployment and operational runbooks.

## 15. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| macOS permissions or capture APIs behave differently across OS versions | Maintain an OS-version test matrix, explicit diagnostics, and a documented virtual-device fallback. |
| The native-app effort expands into a full rewrite | Keep the native layer thin and reuse the Next.js workspace and cloud intelligence. |
| Network loss causes missing transcript content | Use bounded buffering, ordered event IDs, acknowledgements, and a durable text/event outbox. |
| Raw audio remains after a crash | Default to memory-only audio and verify the filesystem in automated forced-termination tests. |
| Cloud privacy claims exceed vendor configuration | Inventory STT and Vertex settings and make privacy copy conditional on verified configuration. |
| Provider-specific speaker identity becomes brittle | Make adapters optional and retain user correction plus provider-independent self/remote labeling. |
| AI assessments overstate evidence or automate hiring judgment | Separate evidence, recruiter judgment, and inference; require human approval and an insufficient-evidence state. |
| Existing uncommitted work is lost or mixed into the migration | Preserve it before implementation and begin from a clean, explicitly named baseline. |
| Cloud cost grows with continuous audio and frequent LLM calls | Measure per-session cost, use event-driven suggestions, enforce quotas, and add cost dashboards. |

## 16. Non-goals for the first vertical slice

- Windows support.
- Fully on-device speech recognition.
- Autonomous candidate ranking or rejection.
- Video or screen recording.
- Mandatory Google Meet, Zoom, or Teams integrations.
- A complete native rewrite of the interview interface.
- Broad collaboration and enterprise administration beyond what is required for secure ownership and pilot operation.

## 17. Approval boundary

Approving this document approves the architecture direction and gate sequence only. Phase 0B is complete. The panel decision was **approve with conditions: Phase 1A offline only**, and the user later explicitly authorized Phase 1A after the docs-only baseline and preflight were recorded. The guard-first implementation awaits review before conformance. Phases 1B, 1C, and 1D remain blocked behind their named evidence and separate authorization.

No approval here authorizes a branch push, merge, deployment beyond a precisely approved Phase 1B mutation set, hosted/native/integrated work outside its gate, external pilot access, ambient or human audio, real candidate/customer data, or migration or deletion of existing data.
