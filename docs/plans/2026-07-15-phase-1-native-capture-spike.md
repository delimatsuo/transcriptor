# Phase 1 Plan: macOS Native Capture and Privacy Spike

**Status:** Blocked by the 2026-07-15 panel review; do not implement until Phase 0 containment is verified and a new review explicitly clears this plan

**Date:** 2026-07-15

**Depends on:**

- `docs/plans/2026-07-15-native-companion-and-note-first-interviews.md`
- `docs/architecture/0001-native-companion-cloud-stt.md`
- `docs/architecture/0002-companion-stream-protocol.md`
- `docs/privacy/data-flow-retention-contract.md`
- `docs/product/companion-web-state-contract.md`

## 1. Objective

First prove the protocol, minimal authentication/ownership boundary, and isolated streaming gateway with synthetic fixtures. Then prove that a thin macOS companion can capture microphone and system audio from common meeting applications, stream both sources to Google Cloud STT without persistent raw audio, and recover predictably from short network interruptions.

This is a technical and privacy spike, not a production application.

## 2. Hypotheses

1. Native macOS APIs can replace mandatory BlackHole configuration for supported macOS versions.
2. Mic and system audio can remain separate through capture, transport, and transcript labeling.
3. A 30-second-per-source in-memory buffer is sufficient for ordinary transient network loss without unacceptable memory use.
4. Cloud STT latency and Portuguese quality remain useful when audio originates in the native companion rather than the Python `sounddevice` pipeline.
5. Stable event IDs, per-source sequence numbers, distinct admission/forwarding/durable-transcript acknowledgements, and exact gap ranges can produce deterministic recovery without duplicate final events.
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

## 4. Blocking prerequisites

- Complete the separately authorized Phase 0 containment change: push-triggered deployment is disabled or explicitly gated, unauthenticated deployment is removed, and live GitHub/GCP state is inventoried and contained.
- Preserve the current dirty speaker-correlation and extension work, including ignored `extension/manifest.json`, without mixing it into the spike.
- Begin from a clean, explicitly named implementation baseline only after the user authorizes the branch/worktree operation.
- Use a verified isolated development GCP project with separate identity, datastore, bucket, secrets, quotas, and provider settings.
- Verify that STT data logging is disabled and Vertex AI settings are inventoried in that exact project.
- Define minimal authenticated companion enrollment, server-derived user/organization ownership, revocation, stream leases, and limits before accepting hosted audio.
- Record the current Python pipeline's latency, accuracy sample, CPU, memory, and failure behavior for comparison.
- Define the minimum supported macOS versions and available test hardware.
- Re-run the review panel and obtain an explicit proceed decision for Phase 1.

Until every prerequisite has direct evidence, only documentation and offline synthetic-fixture design are permitted. Do not push, deploy, open a hosted audio endpoint, or use candidate, customer, employee, or other consented human audio.

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

## 6. Work packages in required order

### A. Protocol schema and offline conformance harness

Implement the normative behavior in `docs/architecture/0002-companion-stream-protocol.md` against synthetic byte fixtures before connecting native capture or a hosted provider.

- Deterministic client event and final transcript identities.
- Independent per-source sequence, sample, and time ranges.
- Capture lease and fencing generations.
- Separate `audio.admitted`, `audio.forwarded`, and `transcript.durable` watermarks.
- Client raw-audio release only after the highest contiguous `audio.forwarded` watermark.
- Exact durable gaps for overflow, rejection, provider failure, process loss, and ambiguous forwarding state.
- Idempotent note, transcript, lifecycle, and gap events.
- Reconnect negotiation with authoritative watermarks and exact resend ranges.
- Explicit protocol-version rejection and structured retry guidance.
- Bounded message sizes and content-safe transport logs.

### B. Minimal identity, ownership, and isolated gateway

- Authenticate the spike client with short-lived, revocable credentials and no permanent Google Cloud credential.
- Derive user and organization membership on the server; do not trust identity fields in payloads.
- Validate session/stream ownership and one fenced capture lease.
- Enforce audio size, rate, duration, concurrency, and quota limits.
- Run only in the verified isolated development project.
- Forward synthetic audio into Google STT and translate results into versioned transcript events.
- Journal content-free forwarding ranges before advancing the client release watermark.
- Persist final transcript and gap events with stable IDs.
- Inject controlled disconnects, throttling, stale leases, cross-tenant requests, and STT errors.

The gateway must fail closed. It cannot be exposed as an unauthenticated endpoint or deployed by a push to a release branch.

### C. Native permissions and local capture

After packages A and B pass with synthetic fixtures:

- Request microphone permission.
- Request macOS system-audio/screen-capture permission required by the selected API.
- Capture system audio without retaining video or screen frames.
- Capture the selected microphone independently.
- Detect permission denial and revocation.
- Detect device loss and switching.
- Expose source health, sample rate, channel count, and monotonic capture time.
- Implement the authoritative capture states and pause/stop boundaries in the product-state contract.

Candidate implementation direction:

- ScreenCaptureKit for system audio.
- AVAudioEngine or the appropriate Core Audio path for microphone audio.
- A virtual-audio device only as a documented fallback.

The spike report must document the final API choice and supported OS constraints rather than treating this candidate direction as predetermined.

### D. Audio normalization and bounded memory

- Normalize each source to the STT format expected by the gateway.
- Preserve source identity through the full pipeline.
- Frame chunks with stream ID, capture generation, stable event ID, sequence, sample range, and capture timestamp.
- Use a bounded in-memory ring or queue per source.
- Retain raw chunks through admission and release only after contiguous provider forwarding.
- Surface overflow as an exact, visible gap and metric.
- Never spill audio to disk in the default spike path.

### E. Diagnostics, state truthfulness, and privacy evidence

- Record timings, counts, queue depth, gaps, status codes, and resource use.
- Do not log audio, transcript text, participant names, notes, or credentials.
- Provide a diagnostic bundle that contains configuration and metadata only.
- Add a filesystem verification script or test that detects unintended audio artifacts.
- Record network destinations and payload classes for the data-flow report.
- Verify that companion and web state never claim active, paused, stopped, completed, or deleted before the authoritative event.
- Verify browser-close, multi-tab, permission-loss, device-switch, reconnect, and degraded-gap behavior.

### F. Comparison harness

Use synthetic fixtures to compare the current and native paths with identical audio where possible. Consented human audio remains out of scope until the isolated environment, STT settings, auth/ownership, protocol, and privacy gates pass and its use is separately approved.

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

### Content fixtures

- Brazilian Portuguese with at least two regional accents.
- Portuguese/English code-switching.
- Names, company names, job titles, numbers, and dates.
- Quiet speech, interruptions, and limited overlap.
- Music or unrelated system audio during a call to document mixed-system-audio behavior.

## 8. Acceptance gates

### Functional

- Offline protocol conformance and isolated-gateway synthetic tests pass before native capture is connected.
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
- Consented human audio is not used before every prerequisite and synthetic gate passes.

### Reliability

- A gateway-admission acknowledgement never releases the companion's raw audio.
- A provider-forwarding acknowledgement advances only across exact contiguous journaled ranges and is the only raw-audio release watermark.
- A durable-transcript acknowledgement preserves stable final IDs and coverage ranges independently of raw-audio release.
- A reconnect within the 30-second buffer resumes from authoritative watermarks and exact resend ranges without duplicate durable final transcript events.
- An outage longer than the buffer produces an explicit, measured transcript gap rather than silent data loss or disk spooling.
- Gateway restart, ambiguous forwarding, and STT stream rotation create either one stable durable transcript result or one exact durable gap for every affected range.
- Stop while disconnected clears audio memory and leaves a recoverable session state.

### Quality and performance

- Native-path transcript quality is not materially worse than the current Python baseline on the approved fixtures.
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

This plan defines the spike but is currently blocked. Implementation begins only after Phase 0 containment and live-state verification are complete, existing dirty/ignored work is preserved, a clean baseline is selected, and both a new panel review and the user explicitly authorize Phase 1 execution. That authorization does not implicitly authorize a push, deployment, external traffic, or real candidate data.
