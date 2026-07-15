# Companion Streaming Protocol Contract

**Status:** Normative target; Phase 1A offline implementation is panel-approved with conditions but requires explicit user authorization. Phases 1B-1D remain blocked.

**Date:** 2026-07-15

**Depends on:**

- `docs/plans/2026-07-15-native-companion-and-note-first-interviews.md`
- `docs/architecture/0001-native-companion-cloud-stt.md`
- `docs/privacy/data-flow-retention-contract.md`
- `docs/product/companion-web-state-contract.md`

## 1. Purpose and boundary

This contract defines the minimum transport behavior between the macOS companion and the authenticated T.A.R.S. streaming gateway. It exists to make audio custody, retries, gaps, and user-visible state testable. Transport is at least once and processing is idempotent; the observable invariant is exactly one non-overlapping terminal transcript-or-gap outcome for every attempt-independent known coverage range, not impossible end-to-end exactly-once delivery.

It is a target requirement, not a description of the current Python prototype. It does not authorize implementation, hosted audio, branch pushes, or deployment.

## 2. Preconditions for accepting audio

The gateway must reject audio until all of the following are true:

1. The client has a short-lived, revocable enrollment credential.
2. The server has derived the user and organization from authentication.
3. The session and both source streams are owned by that organization.
4. Consent acknowledgement and the applicable disclosure version are recorded.
5. Message-size, bitrate, duration, concurrency, and quota limits are active.
6. The isolated development project and Google STT data-logging setting have been verified.
7. One companion instance holds the active capture lease and fencing generation for the session.

Request-supplied user or organization identifiers are never authority.

## 3. Identifiers and ordering

Every connection carries a supported `protocolVersion`. Every client event carries:

- `sessionId`
- `streamId`
- `deviceId`
- `captureGeneration`
- `eventId`
- `capturedAtMonotonicNs`
- `capturedAtWallClock`

Audio chunks additionally carry:

- `source`: `microphone` or `system_audio`
- `sequence`: a zero-based, strictly increasing integer within a stream generation
- `firstSample` and `lastSampleExclusive`
- sample rate, channel count, encoding, and duration
- a payload digest used for retry validation, not as a content log

Each chunk also maps to an attempt-independent `coverageId` derived from protocol version, session, stream, capture generation, source, sequence, and sample range. STT attempt generation is processing metadata and is not part of coverage identity.

Identity rules:

- `eventId` is deterministically derived from protocol version, session, stream, capture generation, event type, and source sequence or client mutation ID.
- Retrying an event uses the same ID and identical payload. The gateway rejects an ID reused with different content.
- A new capture after stop, permission loss, or device re-enrollment uses a new `captureGeneration` and new stream IDs.
- Final transcript IDs are deterministically derived from attempt-independent coverage identity and result ordinal. STT attempt generation is recorded as provenance but cannot create a second terminal outcome for the same coverage. Once committed, the same final event ID is used on replay.
- Notes and bookmarks use client-generated stable mutation IDs and server-assigned stable record IDs returned by the first durable acknowledgement.

The server processes each source independently but preserves ordering within a source. It must not infer that microphone and system-audio sequence numbers share one clock or counter.

## 4. Audio custody and acknowledgements

The protocol has no ambiguous generic `audio.ack`. It uses three distinct acknowledgement events and watermarks per source.

### 4.1 Gateway admission: `audio.admitted`

Meaning:

- The gateway authenticated and authorized the chunk.
- Schema, limits, digest, stream lease, and ordering checks passed.
- The exact contiguous range is present in the gateway's bounded transient queue.

It does **not** mean the audio reached Google STT. It does **not** make the client copy eligible for release.

### 4.2 Provider forwarding: `audio.forwarded`

Meaning:

- The exact contiguous range was written to the active Google STT stream under the current fenced attempt.
- A content-free forwarding record containing the source, range, attempt, and time is durable.

This is the client audio-release watermark. The companion may release only chunks at or below the highest contiguous `audio.forwarded` sequence for that source. Admission alone never releases raw audio.

Provider forwarding does not promise that final transcript text will exist. A provider or gateway failure after forwarding can still create a declared transcript gap.

### 4.3 Durable transcript: `transcript.durable`

Meaning:

- A final transcript event and its audio coverage range were committed to the durable event store.
- Reconnect and replay return the same final event ID.

This watermark is for transcript completeness and UI reconciliation. It is not the raw-audio release watermark.

### 4.4 Required acknowledgement fields

Each acknowledgement includes:

- session, stream, source, capture generation, and STT attempt generation
- highest contiguous sequence for the named acknowledgement stage
- the corresponding sample and capture-time range
- server event ID and server time
- any missing or rejected ranges

An acknowledgement must never advance past a gap. A sparse success is represented as explicit ranges, not as a misleading high-water mark.

## 5. Retry, reconnect, and idempotency

1. The companion retains each raw chunk in bounded memory until its `audio.forwarded` watermark advances past that chunk.
2. On reconnect, the companion sends its last observed admission, forwarding, and durable-transcript watermarks.
3. The server returns the authoritative watermarks, active fencing generation, and exact resend range.
4. The client resends only ranges not authoritatively forwarded. Retries reuse their original event IDs and payload digests.
5. The gateway deduplicates already admitted or forwarded events and never creates a second terminal transcript or gap outcome for the same coverage identity.
6. A stale connection or stale fencing generation cannot admit, forward, pause, resume, or stop a session.
7. Text/metadata mutations remain in a durable encrypted outbox until `event.durable`; raw audio never enters that outbox.

If a failure leaves forwarding status ambiguous, the server must use its stream lease and content-free forwarding journal to resolve the range. If it can prove the affected sequence/sample boundaries but cannot prove safe replay or successful forwarding, it declares that exact range as a gap. If forced termination means the end boundary itself is unknowable, it records an honest unknown-coverage interval and reason instead of fabricating precision.

## 6. Gap contract

A `capture.gap` event is durable, user-visible, and contains:

- `gapId`
- session, stream, source, capture generation, and STT attempt generation
- `coverageId` when both boundaries are known
- `firstSequence` and nullable `lastSequenceInclusive`
- `firstSample` and nullable `lastSampleExclusive`
- monotonic start and nullable end timestamps
- known duration or an explicit `unknown_end` boundary status
- reason code
- whether raw audio was never captured, discarded before forwarding, or forwarded without a durable transcript
- whether retry is possible

Initial reason codes include:

- `permission_revoked`
- `device_unavailable`
- `buffer_overflow`
- `network_timeout`
- `gateway_rejected`
- `stt_stream_failed`
- `process_terminated`
- `user_discarded_pending_audio`
- `unknown_forwarding_state`

Gaps cannot be edited away. A user may add an explanation or exclude the affected passage from assessment, but the coverage record remains.

Terminal transcript and gap coverage for the same source/generation must not overlap. A transaction or equivalent single-writer projection enforces terminal uniqueness by coverage identity. Crash tests cover failure before and after simulated/provider write, forwarding-journal commit, transcript commit, reconnect negotiation, and STT-attempt rotation.

## 7. Pause, stop, and finalization protocol

- `capture.pause.requested` is a command. Physical capture stops only when the companion emits `capture.paused` with the authoritative capture generation and last captured sequence per source.
- Pre-pause chunks may continue through admission, forwarding, and transcript finalization. The UI must say that previously captured audio is finishing.
- Resume creates no overlapping sequence range and is rejected if consent, permission, lease, or device health is invalid.
- `session.stop.requested` causes the companion to stop acquiring new audio immediately and emit `capture.stopped` with final captured ranges.
- The user chooses whether still-unforwarded in-memory audio is sent or discarded. Discarding creates an exact gap only when the companion can prove both boundaries; otherwise it creates unknown-end coverage.
- `session.finalizing` continues until every captured range is represented by a durable transcript coverage event or a durable gap.
- `session.completed` is emitted only after finalization. Completion is not assessment approval.

Web commands are requests. Only companion-originated capture events can assert that physical capture started, paused, resumed, or stopped.

## 8. Minimum event set

- `session.enrolled`
- `session.start.requested`
- `capture.starting`
- `capture.active`
- `audio.chunk`
- `audio.admitted`
- `audio.forwarded`
- `transcript.interim`
- `transcript.final`
- `transcript.durable`
- `capture.gap`
- `capture.pause.requested`
- `capture.paused`
- `capture.resume.requested`
- `capture.active`
- `session.stop.requested`
- `capture.stopped`
- `session.finalizing`
- `session.completed`
- `note.create`, `note.update`, and `note.delete`
- `event.durable`
- `error`

Each message has an explicit schema version. Unsupported versions fail closed with retry guidance that does not reveal cross-tenant resource existence.

## 9. Protocol conformance gates

Before native audio reaches any hosted endpoint, automated tests must prove:

- unauthenticated, expired, revoked, cross-tenant, and stale-fence messages are rejected;
- an admission acknowledgement never releases client audio;
- a forwarding acknowledgement advances only over contiguous, journaled ranges;
- final transcript replay preserves IDs and does not duplicate projections;
- reconnect produces exact resend ranges;
- buffer overflow and ambiguous forwarding create exact durable gaps where boundaries are known and explicit unknown-boundary coverage where they are not;
- pause and stop ranges contain no newly captured audio after the companion's authoritative boundary;
- logs contain no audio, transcript, notes, documents, credentials, or payload digests usable as content identifiers;
- a 60- and 90-minute synthetic test stays within approved memory and message limits.

Phase 1A runs these semantics against one canonical versioned schema, Swift and Python binding validation, a deterministic provider simulator, fixed synthetic-byte manifests, and process networking disabled. It must abort on credential lookup, implicit environment/project selection, network access, non-fixture input, or persistent audio. Hosted authentication and provider tests belong to separately authorized Phase 1B.

## 10. Unresolved implementation choices

- Binary framing and schema language.
- Maximum chunk duration and byte size.
- STT stream-rotation interval.
- Fencing and forwarding-journal storage implementation.
- The measured bounded-memory duration after the spike.

These choices may be resolved during the spike if they preserve the semantics above. Changing the audio-release watermark or weakening the gap contract requires a new architecture decision and security review.
