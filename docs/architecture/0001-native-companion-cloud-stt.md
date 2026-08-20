# ADR 0001: Native Companion with Cloud Speech-to-Text

**Status:** Architecture decision accepted. Phase 1A offline conformance passed at `9f3f3a0`; Phases 1B-1D remain blocked.

**Date:** 2026-07-15

**Decision owners:** Product and engineering

**Governing plan:** `docs/plans/2026-07-15-native-companion-and-note-first-interviews.md`

**Protocol contract:** `docs/architecture/0002-companion-stream-protocol.md`

This ADR describes the approved target direction. It is not a claim about current prototype behavior and does not clear the active deployment/security gate.

## Decision

T.A.R.S. will use a macOS-first native companion to capture microphone and system audio at the device level. The first product release will stream transient audio to an authenticated cloud service that uses Google Cloud Speech-to-Text. Raw audio will not be persisted by T.A.R.S. by default.

The product remains executive-search-first. The existing Next.js interview workspace and Python cloud intelligence are reused where practical. Provider-specific extensions remain optional enrichment rather than a capture dependency.

On-device speech recognition is deferred until a measured benchmark or a customer requirement justifies the additional product and engineering cost.

## Context

The current application already captures two local sources:

- The default microphone for the recruiter.
- BlackHole system audio for the remote side of the call.

That architecture works independently of Zoom, Meet, Teams, or another meeting provider, but device capture is embedded in the FastAPI process. This creates a conflict with the Cloud Run deployment target because a remote container cannot access the user's local audio devices.

The current pipeline also writes all audio to local FLAC files. This is useful as prototype crash insurance but is incompatible with the proposed no-persistent-audio default.

Device-level capture and on-device transcription are separate decisions. Granola's published documentation says its desktop application captures audio locally but passes the audio to transcription providers; the defining privacy property is that no recording persists after transcription, not that the speech model necessarily runs on the user's computer.

References:

- Granola transcription data flow: https://docs.granola.ai/help-center/taking-notes/transcription
- Granola security overview: https://www.granola.ai/security
- Google Cloud STT data usage: https://docs.cloud.google.com/speech-to-text/docs/v1/data-usage-faq
- Vertex AI zero-data-retention guidance: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/vertex-ai-zero-data-retention

## Architecture boundary

### Native companion owns

- macOS microphone and system-audio permissions.
- Audio-device discovery, selection, and health.
- Separate microphone and system-audio capture channels.
- Resampling, framing, timestamps, and monotonic sequence numbers.
- A bounded in-memory buffer.
- Start, pause, resume, stop, and visible capture state.
- Authenticated enrollment and secure token storage.
- A durable outbox for text and metadata events only.

### Cloud service owns

- User and organization authentication and authorization.
- Session ownership and lifecycle.
- The authenticated audio streaming gateway.
- Google Cloud STT stream lifecycle.
- Transcript normalization, idempotency, and persistence.
- Recruiter notes, competency evidence, and assessment versions.
- Gemini suggestions and report generation.
- Retention, deletion, audit, quota, and operational controls.

### Web workspace owns

- Interview preparation.
- Live transcript, recruiter notes, and competency coverage.
- Speaker correction and evidence review.
- Report editing and approval.
- Export, retention visibility, and deletion controls.

## Why cloud STT is the first-release choice

- It is already integrated and exercised by the prototype.
- It provides streaming interim and final results.
- It avoids distributing service credentials or large speech models.
- It avoids making CPU, memory, battery, thermal behavior, model packaging, and model updates part of the first native release.
- It keeps the first vertical slice focused on capture reliability, privacy, recruiter notes, and interview intelligence.
- Google states that streaming audio is processed in memory and not stored when the project has not opted into data logging. T.A.R.S. must verify the intended project's settings rather than relying on a documentation claim alone.

## Consequences

### Positive

- Provider-independent capture without a meeting bot.
- Faster migration from the existing prototype.
- Consistent STT behavior across supported Mac hardware.
- Smaller and simpler native companion.
- Centralized STT configuration, observability, vocabulary, and model updates.
- A credible no-recording posture if transient processing and deletion are verified.

### Negative

- Audio leaves the user's device transiently.
- The product cannot claim that transcription is entirely on-device.
- Network loss can cause transcription gaps unless bounded buffering and recovery are designed explicitly.
- Cloud cost and provider availability remain product dependencies.
- Privacy claims depend partly on verified Google Cloud project configuration and contract terms.

## Guardrails

1. Do not create raw-audio files by default.
2. Do not ship permanent Google Cloud credentials in the companion.
3. Do not make the capture gateway externally accessible without authentication and ownership checks.
4. Do not describe the product as local-only or entirely on-device.
5. Verify STT data logging and Vertex AI retention/cache settings before publishing privacy claims.
6. Show a visible capture indicator and immediate pause/stop controls.
7. Treat provider adapters as optional.
8. Require evidence and human approval for candidate assessments.

## Alternatives considered

### Keep the Python backend as the local capture application

Rejected as the long-term product architecture. It is useful for prototype validation but creates installation, permissions, credentials, updates, diagnostics, and Cloud Run boundary problems.

### Use mandatory BlackHole capture

Rejected as the default user experience. It adds setup and routing failure modes. It remains a compatibility fallback if native capture cannot support a specific environment.

### Require on-device STT in the first release

Deferred. It could improve offline operation and reduce audio egress, but it introduces model-quality, hardware, battery, packaging, and update work before the native capture and note-first workflow are proven.

### Make the Google Meet extension foundational

Rejected. It would discard provider independence and rely on brittle provider DOM behavior. It remains useful for participant-name enrichment.

## Revisit triggers

Reopen the STT decision if any of the following becomes true:

- A design partner requires that audio never leave the device.
- Cloud STT cannot meet Portuguese accuracy or latency targets.
- Per-session cloud cost exceeds the approved business threshold.
- A local engine reaches acceptable quality and performance across the supported Mac matrix.
- Offline or air-gapped operation becomes a committed product requirement.
- Vendor retention or contract terms become incompatible with the privacy contract.

Any reconsideration requires a benchmark against the same Portuguese interview fixtures, device matrix, latency definitions, and report-quality downstream tests.

## Approval boundary

This ADR approves only the architecture direction. Phase 0B containment is complete, and the separately authorized Phase 1A offline protocol gate passed at `9f3f3a0`. Phase 1B hosted fixtures, Phase 1C offline native capture, and Phase 1D integrated synthetic testing remain blocked behind their named evidence and separate authorization. This ADR does not authorize pushes, merge, deployment, hosted/native/integrated work outside its gate, ambient or human audio, real candidate/customer data, external pilot traffic, or migration of existing data.
