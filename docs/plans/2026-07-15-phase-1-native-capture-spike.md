# Phase 1 Plan: macOS Native Capture and Privacy Spike

**Status:** Phase 1A offline protocol/state-machine conformance passed at `9f3f3a0`. Phases 1B, 1C, and 1D remain blocked.

**Date:** 2026-07-15

**Depends on:**

- `docs/plans/2026-07-15-native-companion-and-note-first-interviews.md`
- `docs/architecture/0001-native-companion-cloud-stt.md`
- `docs/architecture/0002-companion-stream-protocol.md`
- `docs/privacy/data-flow-retention-contract.md`
- `docs/product/companion-web-state-contract.md`
- `docs/test-fixtures/phase-1a-synthetic-byte-manifest.md`

## 1. Objective

First prove the protocol offline with deterministic synthetic bytes and networking disabled. Separately prove the hosted gateway with allowlisted fixtures and prove native capture into a local null sink in a controlled fixture-routing environment. Only after those independent gates pass may a separately authorized integration connect native capture to the hosted STT path.

This is a technical and privacy spike, not a production application.

## 2. Hypotheses

1. Native macOS APIs can replace mandatory BlackHole configuration for supported macOS versions.
2. Mic and system audio can remain separate through capture, transport, and transcript labeling.
3. A 30-second-per-source in-memory buffer is sufficient for ordinary transient network loss without unacceptable memory use.
4. Cloud STT latency and Portuguese quality remain useful when audio originates in the native companion rather than the Python `sounddevice` pipeline.
5. At-least-once delivery, stable event IDs, per-source sequence numbers, distinct admission/forwarding/durable-transcript acknowledgements, and an attempt-independent coverage ledger can produce exactly one non-overlapping observable terminal transcript-or-gap outcome per range.
6. The spike can leave no persistent raw-audio artifact after normal stop or forced termination.

## 3. Non-goals

- Production UI polish.
- App Store distribution.
- Windows support.
- On-device STT implementation.
- Full organization administration.
- Final Firestore schema migration.
- Full recruiter notepad or competency coverage.
- Production rollout or external pilot traffic.
- Provider-specific participant naming.

## 4. Gate model and authorization boundary

Phase 0B containment and the panel re-review are complete. The panel decision was **approve with conditions: Phase 1A offline only**, the user explicitly authorized that gate, and its reviewed guard plus conformance exit criteria passed at `9f3f3a0`. Later gates still require their own explicit authorization.

| Gate | Allowed work | Prohibited work | Entry evidence | Exit evidence | Authorization and stop behavior |
| --- | --- | --- | --- | --- | --- |
| **1A: offline protocol conformance** | Canonical schema, generated or validated Swift/Python bindings, deterministic provider simulator, auth/ownership logic with fake identities, conformance tests, metadata-only diagnostics | Network access, ADC, secrets, real identity provider, provider calls, cloud mutation, native permission/capture APIs, candidate/customer fields, push, deploy | Clean docs-amendment baseline descending from `1d2ad13`; WIP commit `f5fc9f6` recoverable; no ignored credentials or real data; fixed manifest `docs/test-fixtures/phase-1a-synthetic-byte-manifest.md`; planned test process network-disabled; explicit user authorization for 1A | Deterministic tests prove fencing, bounds, retry, crash points, and one terminal transcript-or-gap outcome per attempt-independent range; artifact scan and evidence report tied to commit | Stop on any network attempt, credential lookup, non-fixture input, persistent payload, or mixed worktree. No cloud rollback is needed because 1A cannot mutate cloud state. |
| **1B: hosted allowlisted fixtures** | Isolated authenticated gateway and STT forwarding for server-allowlisted fixture bytes only | Native capture, ambient/human audio, real identities/data, legacy-project access, implicit project/ADC selection, push-triggered deployment | Separate user authorization; reviewed gateway threat model; exact project/account/runtime identity attestation; lower STT/Vertex quotas; least-privilege roles; populated secrets; protected-environment approval; fresh containment readback | Negative auth/tenant/replay/limit tests; allowlisted fixture digest/range enforcement; provider/cost limits; network/log/artifact evidence; independently executable kill switch tested | Stop and execute the kill switch on project/identity/config mismatch, unexpected traffic/content, quota failure, or public/cross-tenant access. Runtime identity, API enablement, endpoint creation, secret population, deployment, and quota changes remain an explicitly documented mutation set. |
| **1C: offline native capture** | macOS permissions and capture into an in-memory/null protocol sink using controlled generated-fixture routing | Hosted gateway/provider access, ambient or human speech, persistent audio, real interview UI/data | Separate user authorization; supported macOS/test-Mac inventory; Python baseline and numeric thresholds; controlled fixture-routing procedure; persistent test-mode label; fixture-use attestation distinct from participant consent; local kill control | Compatibility, resource, privacy, accessibility, English/PT-BR, multi-window, and comprehension evidence; post-run artifact scan; contamination tests | Stop, clear memory, and mark the run invalid on ambient/unrelated-audio contamination, unknown routing, persistent artifacts, or capture outside the test indicator. |
| **1D: integrated synthetic end-to-end** | Native generated-fixture capture through the reviewed hosted gateway to STT | Ambient/human audio, real data, production or legacy mutation, external traffic | Separate user authorization after 1B and 1C pass; every 1B control re-read and every 1C fixture/privacy control active | Repeatable 60/90-minute matrix, transcript quality/latency/resource report, network evidence, terminal-coverage evidence, no-persistent-audio proof, go/no-go recommendation | Inherits both 1B cloud kill switch and 1C local contamination stop. Any failed inherited control blocks the run. |

**Phase 1A execution result:** Passed at implementation tip `9f3f3a0`. The guarded suite runs 54 Python and 4 Swift tests twice, including deterministic identities, fake-identity ownership, fencing, reconnect, crash points, exact/unknown gaps, and logical 60/90-minute bounded-memory streams. The final artifact/scope scan reports zero artifacts, forbidden imports, or out-of-scope paths. Direct evidence is in `docs/current-state/phase-1a-conformance-evidence.md`.

Every gate must name an owner, pass/fail criterion, evidence artifact, and stop condition in its implementation checklist. A gate approval does not authorize a later gate, branch push, merge, deployment outside the precisely documented mutation set, real data, or legacy-data mutation.

## 5. Proposed spike structure

Recommended location after implementation approval:

```text
companion/
  macos/
    TarsCaptureSpike/
      Capture/
      Protocol/
      Transport/
      Diagnostics/
      Tests/
```

The spike should be isolated from the production entry point and guarded from accidental deployment.

## 6. Work packages in required gate order

### 1A. Protocol schema and offline conformance harness

Implement the normative behavior in `docs/architecture/0002-companion-stream-protocol.md` against a fixed, provenance-recorded synthetic-byte fixture manifest and deterministic provider simulator with process networking disabled. Fixture checksums belong in the test manifest and evidence report, not runtime logs.

- One canonical versioned schema with generated or validated Swift and Python bindings.
- Deterministic client event identities and attempt-independent audio-coverage identities.
- Independent per-source sequence, sample, and time ranges.
- Capture lease and fencing generations.
- Separate `audio.admitted`, `audio.forwarded`, and `transcript.durable` watermarks.
- Client raw-audio release only after the highest contiguous `audio.forwarded` watermark.
- Exactly one non-overlapping terminal transcript or gap outcome for each known range; an honest unknown-boundary outcome where the client cannot prove the end boundary.
- Idempotent note, transcript, lifecycle, and gap events.
- Reconnect negotiation with authoritative watermarks and exact resend ranges.
- Crash-point tests before and after simulated provider write, forwarding-journal commit, transcript commit, reconnect, and attempt rotation.
- Explicit protocol-version rejection and structured retry guidance.
- Bounded message sizes and content-safe transport logs.

### 1B. Minimal identity, ownership, and isolated gateway

This gate is separately authorized and may not begin as part of Phase 1A.

- Define and review the gateway threat model: ingress/IAM topology, enrollment and renewal, issuer, audience, expiry, Keychain storage, revocation propagation, WebSocket credential transport without URL tokens, Origin/replay protection, session binding, server-derived membership, leases/fencing, and non-enumerating failures.
- Authenticate the spike client with short-lived, revocable credentials and no permanent Google Cloud credential.
- Derive user and organization membership on the server; do not trust identity fields in payloads.
- Validate session/stream ownership and one fenced capture lease.
- Enforce audio size, rate, duration, concurrency, and quota limits.
- Fail closed unless the execution matches project ID `transcriptor-dev-20260715`, project number `570346565602`, the reviewed runtime identity, and the approved configuration. Reject `transcriptor-490222`; do not use implicit project or default-ADC selection. Mutable labels are supporting evidence only.
- Remove or separately justify unused `roles/aiplatform.user` and GCS access before runtime enablement.
- Apply and verify lower STT/Vertex quotas before runtime enablement; the BRL 250 budget is an alert, not a spend cap.
- Accept only server-issued fixture manifests and expected chunk digests/ranges, then forward those fixtures into Google STT and translate results into versioned transcript events. A client-supplied `synthetic` flag is never authority.
- Journal content-free forwarding ranges before advancing the client release watermark.
- Persist final transcript and gap events with stable IDs.
- Inject controlled disconnects, throttling, stale leases, cross-tenant requests, and STT errors.

The gateway must fail closed. It cannot be exposed as an unauthenticated endpoint or deployed by a push to a release branch. Its independently executable kill switch must stop invocation and provider access without requiring a new deployment while preserving content-free audit evidence.

### 1C. Native permissions and local capture

After 1A passes, and only under a separately authorized controlled-fixture procedure, capture into an in-memory/null sink with networking and provider access disabled. Phase 1C does not depend on activating 1B.

- Request microphone permission.
- Request macOS system-audio/screen-capture permission required by the selected API.
- Capture system audio without retaining video or screen frames.
- Capture the selected microphone independently.
- Detect permission denial and revocation.
- Detect device loss and switching.
- Expose source health, sample rate, channel count, and monotonic capture time.
- Implement the authoritative capture states and pause/stop boundaries in the product-state contract.
- Keep a persistent non-production/test-mode indicator and immediate local kill control visible whenever capture may continue.
- On possible ambient or unrelated-system-audio contamination, stop, clear memory, record a content-free invalid-run result, and do not use the run as evidence.

Candidate implementation direction:

- ScreenCaptureKit for system audio.
- AVAudioEngine or the appropriate Core Audio path for microphone audio.
- A virtual-audio device only as a documented fallback.

The spike report must document the final API choice and supported OS constraints rather than treating this candidate direction as predetermined.

### 1C. Audio normalization and bounded memory

- Normalize each source to the STT format expected by the gateway.
- Preserve source identity through the full pipeline.
- Frame chunks with stream ID, capture generation, stable event ID, sequence, sample range, and capture timestamp.
- Use a bounded in-memory ring or queue per source.
- Retain raw chunks through admission and release only after contiguous provider forwarding.
- Surface overflow as an exact, visible gap and metric.
- Never spill audio to disk in the default spike path.

### 1C. Diagnostics, state truthfulness, and privacy evidence

- Record timings, counts, queue depth, gaps, status codes, and resource use.
- Do not log audio, transcript text, participant names, notes, or credentials.
- Provide a diagnostic bundle that contains configuration and metadata only.
- Scan application storage, OS temporary/cache locations, unified logs, crash reports, core dumps, diagnostic uploads, CI artifacts, Firestore, GCS, and Cloud Logging using unique synthetic canary markers where the active gate has access.
- Record network destinations and payload classes for the data-flow report.
- Verify that companion and web state never claim active, paused, stopped, completed, or deleted before the authoritative event.
- Verify browser-close, multi-tab, permission-loss, device-switch, reconnect, and degraded-gap behavior.

### 1D. Integrated comparison harness

After 1B and 1C pass, use the same fixed fixtures to compare the current and native paths. Record fixture provenance and checksums, supported macOS versions, actual test Macs/routes, current Python measurements, numeric WER/latency/resource thresholds, and the repeatable 60/90-minute injection and oracle procedure before the run. Consented human audio remains out of scope for this spike.

Measure:

- Time to first interim transcript.
- Time to final transcript.
- Word error rate or reviewed transcription error rate.
- Self/remote source attribution.
- Missing and duplicate final segments.
- CPU and memory.
- Network bytes and reconnect behavior.
- Audio queue depth and discarded duration.

## 7. Test matrix

### Meeting applications

- Google Meet in the primary supported browser.
- Zoom desktop.
- Microsoft Teams desktop or browser.
- Browser-based media outside a meeting application.
- Slack Huddles if available in the test environment.

### Audio routes

- Built-in microphone and speakers.
- Built-in microphone with wired headphones.
- USB headset.
- Bluetooth headset.
- External microphone plus separate output device.
- A changed default device during the session.

### Lifecycle and failure cases

- First-run permission approval.
- Permission denial.
- Permission revoked during capture.
- Meeting application starts before the companion.
- Companion starts before the meeting application.
- Pause/resume.
- 10-second network interruption.
- 30-second network interruption.
- Interruption longer than the approved in-memory buffer.
- Gateway restart.
- STT stream rotation.
- STT error and recovery.
- Companion forced termination.
- Sleep/wake and screen lock.
- Stop while disconnected.

### Generated content fixtures for Phase 1D only

These are generated speech fixtures with documented provenance and fictional identifiers. They are not Phase 1A byte fixtures and contain no recorded human speech or real candidate/customer data.

- Brazilian Portuguese with at least two regional accents.
- Portuguese/English code-switching.
- Fictional names, company names, job titles, numbers, and dates.
- Quiet speech, interruptions, and limited overlap.
- Music or unrelated system audio during a call to document mixed-system-audio behavior.

## 8. Acceptance gates

The requirements below apply only when their named gate is authorized. Phase 1A exits on offline conformance evidence; hosted, native-capture, and integrated criteria do not become 1A scope.

### Phase 1A offline clearance

- Test execution has no network route and aborts on network or credential access.
- Inputs resolve only through the committed/generated fixture manifest and contain no recruiter, candidate, customer, employee, or participant identifiers.
- Transport is at least once; processing is idempotent; every attempt-independent known coverage range has exactly one non-overlapping terminal transcript-or-gap result.
- Unknown forced-termination boundaries are represented as unknown coverage, never fabricated exact gaps.
- Canonical-schema conformance, crash-point, reconnect, fencing, bounds, and artifact-scan tests pass in both Swift and Python validation paths.
- The evidence report records owner, commit, fixtures, commands, pass/fail results, and any stop condition encountered.

### Functional

- Offline protocol conformance passes before either hosted fixture forwarding or native capture work begins.
- Hosted allowlisted-fixture and offline native-capture gates pass independently before integrated capture is connected.
- A 60-minute synthetic call succeeds on Meet, Zoom, Teams, and browser media on the approved test Mac.
- Microphone and system audio remain distinguishable in emitted transcript events.
- Start, pause, resume, and stop behave deterministically.
- The user receives an actionable error for missing permissions or devices.
- No video or screen-frame content enters the application pipeline.

### Privacy

- No FLAC, WAV, temporary audio file, crash attachment, or encoded audio payload remains after normal completion.
- No raw-audio artifact remains after forced termination.
- Logs and diagnostics contain no audio, transcript text, participant names, or credentials.
- Network inspection shows audio only to the authenticated T.A.R.S. gateway and approved STT path.
- The isolated project's STT data-logging and applicable Vertex AI settings are captured as evidence.
- Ambient, consented, or other human audio is not used in this spike. A fixture-use attestation is not participant consent.

### Reliability

- A gateway-admission acknowledgement never releases the companion's raw audio.
- A provider-forwarding acknowledgement advances only across exact contiguous journaled ranges and is the only raw-audio release watermark.
- A durable-transcript acknowledgement preserves stable final IDs and coverage ranges independently of raw-audio release.
- A reconnect within the 30-second buffer resumes from authoritative watermarks and exact resend ranges without duplicate durable final transcript events.
- An outage longer than the buffer produces an explicit, measured transcript gap rather than silent data loss or disk spooling.
- Gateway restart, ambiguous forwarding, and STT stream rotation create exactly one non-overlapping stable durable transcript result or gap for every affected known range. A boundary that cannot be proven is explicitly unknown.
- Stop while disconnected clears audio memory and leaves a recoverable session state.

### Quality and performance

- Before Phase 1C, the supported macOS versions, actual test Macs/routes, fixed fixture manifest, current Python baseline, and numeric go/no-go thresholds are recorded.
- Native-path transcript quality meets the numeric threshold against the current Python baseline on the identical approved fixtures.
- Time-to-first-interim and time-to-final distributions are reported for every application/audio route.
- Proposed beta p95 latency thresholds are based on measured results, with a recommended go/no-go value.
- Average and peak CPU, memory, and network usage are reported.
- The 30-second buffer's measured memory cost and loss tradeoff are documented.

### Security

- The companion contains no permanent Google Cloud credential.
- Expired or revoked enrollment credentials cannot open or resume a stream.
- A user cannot attach a stream to a session owned by another user or organization.
- A stale companion or stale fencing generation cannot control or append to the session.
- Oversized, out-of-order, duplicated, and unsupported-version messages are rejected predictably.
- Hosted execution aborts unless exact project number, runtime identity, configuration, fixture manifest, quotas, and protected approval match the reviewed values.
- Negative tests cover unauthenticated, expired, revoked, wrong-audience, replayed, cross-tenant, stale-fence, conflicting-session, oversized, out-of-order, and unsupported-version requests.

### Product truth, accessibility, and language

- Physical capture, transport/connectivity, microphone health, system-audio health, transcript coverage, finalization, and deletion are independent authoritative axes.
- Any combination in which physical capture may continue uses the same prominent recording treatment in companion and web; color alone is insufficient.
- English and Brazilian Portuguese task tests cover permissions, denial/revocation, one-source and total loss, reconnect/overflow, stop while offline, send/discard, browser close, companion loss, forced quit/relaunch, and deletion partial failure.
- VoiceOver, keyboard-only, multi-window, menu-bar, focus restoration, shortcut-conflict, and comprehension checks pass before Phase 1C can exit.
- Phase 1A deletion is conformance-harness simulation only; it exposes no user-facing decorative deletion control.

## 9. Required deliverables

- Isolated spike source and tests.
- Protocol schema and example event traces without sensitive content.
- Conformance report covering admission, forwarding, release, durable transcript, retry, fencing, exact gaps, and idempotency.
- Current-versus-native benchmark report.
- Capture compatibility matrix.
- Privacy verification report.
- Data-flow update based on observed network traffic.
- Resource-usage and latency report.
- Known limitations and fallback behavior.
- Recommendation: proceed, revise, or stop.
- Proposed scope and acceptance gates for the secure internal alpha.

## 10. Stop conditions

Stop and review rather than expanding the spike if:

- Native capture cannot reliably obtain system audio on the proposed supported macOS range.
- The only reliable path requires capturing or retaining screen/video content.
- The gateway requires permanent cloud credentials in the client.
- Raw-audio deletion cannot be demonstrated.
- Portuguese quality or latency regresses enough to make live interviewing impractical.
- Network recovery requires persistent audio despite the approved default.
- The spike begins absorbing production UI, Windows, provider integrations, or unrelated backend migrations.

## 11. Approval boundary

Phase 0B containment and panel review are complete, and the separately authorized Phase 1A offline protocol/state-machine gate passed at `9f3f3a0`. Phases 1B, 1C, and 1D remain blocked behind their named evidence and separate authorization. No gate approval implicitly authorizes a push, merge, deployment beyond an explicitly approved 1B mutation set, external traffic, ambient/human audio, real candidate/customer data, or legacy-data mutation.
