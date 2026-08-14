# Companion Streaming Protocol Contract

**Status:** Normative target with an owner-authorized G2-A0 amendment candidate
dated 2026-08-13. The amendment is effective only after this exact
documentation tree receives renewed architecture and security/privacy approval;
Phase 1A offline protocol conformance passed at `9f3f3a0`, and Phases 1B-1D
remain blocked.

**Date:** 2026-08-13 amendment candidate; original v1 contract date was 2026-07-15.

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

For protocol-v2 planning, audio ingress is independently bounded before queue
allocation or durable mutation. The source/session defaults are 50/100 audio
events per second/burst, 192,000/384,000 payload bytes per second/burst, and
205,000/410,000 metadata-plus-prefix bytes per second/burst. A two-source
session is capped at 100/200 events, 384,000/768,000 payload bytes, and
410,000/820,000 metadata-plus-prefix bytes per second/burst. Provider attempts
are capped at 1 writable plus 1 draining per source and 2 writable plus 2
draining per two-source session. Tenant scope is
capped at 4 active sessions, 400/800 events per second/burst,
1,536,000/3,072,000 payload bytes per second/burst, and
1,640,000/3,280,000 metadata-plus-prefix bytes per second/burst. Gateway
process scope is capped at 16 active sessions, 1,600/3,200 events per
second/burst, 6,144,000/12,288,000 payload bytes per second/burst, and
6,560,000/13,120,000 metadata-plus-prefix bytes per second/burst. Retries and
identity-rejected duplicates consume tokens, reconnect does not grant a fresh
burst, and an
unavailable shared quota authority fails closed. Control traffic is separately
bounded at 20 events per second with a burst of 40, 32 outstanding mutation IDs,
and one outstanding lifecycle command of each kind. Pending handshakes and
aggregate receive buffers are also bounded before authentication. These are
source/offline target defaults, not hosted-capacity evidence. Tenant and process
resident custody caps are 8,388,608 and 33,554,432 bytes respectively, and
provider-attempt caps are 8 writable plus 8 draining per tenant and 32 writable
plus 32 draining per process. Pending ingress is capped at 64 handshakes per
gateway instance and 16 per source IP, with a five-second handshake deadline,
three-second first-authentication deadline, 16,384-byte headers, 8,192-byte
initial-auth events, 32,768-byte pending receive buffers, 2,097,152 aggregate
pending-buffer bytes, and at most 16 authenticated process connections with one
68,100-byte parser buffer each; the protocol-closure plan Section 3.1 is the normative
source for these edge limits.

The canonical custody bound applies independently to the companion and gateway
for each source: `retainedFrames <= min(96,000, 2 * sampleRate)` and
`retainedPayloadBytes <= min(384,000, retainedFrames * channels * 2)`. Thus
8-kHz mono permits at most 16,000 frames/32,000 bytes, while 48-kHz stereo
permits at most 96,000 frames/384,000 bytes. Mixed-rate sources use independent
rate-derived limits and cannot consume one another's unused capacity. Each
source also has at most 100 queued audio events, 409,600 bytes of serialized
metadata, 100 reservation objects, and 100 queue objects. The two-source session
cap is 192,000 frames and 768,000 raw payload bytes. Resident allocation caps
include parsed objects, reservations, containers, and runtime overhead: 1,048,576
bytes per source and 2,097,152 bytes per session. Implementations fail closed
before those resident ceilings; raw payload bytes alone are not a memory proof.

The protocol-v2 custody target is two seconds of retained audio per source,
derived from sample rate and channel count, with a 30-second absolute raw-custody
expiry. At 10 seconds without forwarding or a terminal release, acquisition
stops and bounded reconciliation begins. The deadline uses a continuous boot
clock where the platform provides one; before intentional suspend the companion
zeroizes, while after an unannounced suspension the first instruction after
resume zeroizes expired or clock-uncertain custody before reading it, opening a
connection, or resuming capture. Evidence must state that scheduling limitation
and may not claim exact physical RAM erasure while the process is frozen.

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

Protocol-v2 admits only signed 16-bit little-endian interleaved PCM, sample
rates from 8,000 through 48,000 Hz, one or two channels, and 20–250 ms chunks
whose sample count and payload bytes align exactly. A payload is at most 64,000
bytes. A format change requires a new stream and capture generation.

Each chunk also maps to an attempt-independent `coverageId` derived from protocol version, session, stream, capture generation, source, sequence, and sample range. STT attempt generation is processing metadata and is not part of coverage identity. The v1 conformance result at `9f3f3a0` predates this amendment and does not qualify v2 semantics.

Identity rules:

- `eventId` is deterministically derived from protocol version, session, stream, capture generation, event type, and source sequence or client mutation ID.
- Retrying an event uses the same ID and identical payload. The gateway rejects an ID reused with different content.
- A new capture after stop, permission loss, or device re-enrollment uses a new `captureGeneration` and new stream IDs.
- Final transcript segment IDs are deterministically derived from protocol-v2
  session/stream/capture/source, the ordered complete atomic chunks they
  intersect, their narrower text sample bounds, provider result ordinal, and
  provider provenance. STT attempt generation is provenance only. A replay uses
  the same segment ID; changed text, bounds, ordinal, or provenance fails closed.
- Notes and bookmarks use client-generated stable mutation IDs and server-assigned stable record IDs returned by the first durable acknowledgement.

The server processes each source independently but preserves ordering within a source. It must not infer that microphone and system-audio sequence numbers share one clock or counter.

## 4. Audio custody and acknowledgements

The protocol has no ambiguous generic `audio.ack`. It uses three watermarks
(`audio.admitted`, `audio.forwarded`, and transcript coverage) plus explicit
terminal-discard, transcript-segment, and transcript-coverage acknowledgements
per source.

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

This is the provider-forwarded success watermark. The companion may classify a
chunk as provider-forwarded and release it through the highest contiguous
`audio.forwarded` sequence for that source. Admission alone never releases raw
audio. Protocol-v2 also defines terminal privacy releases for ranges that will
not be retried; those releases create a durable gap and never advance this
watermark.

Provider forwarding does not promise that final transcript text will exist. A provider or gateway failure after forwarding can still create a declared transcript gap.

### 4.3 Durable transcript segment: `transcript.segment.durable`

Meaning:

- One immutable final-text segment was committed with its provider ordinal,
  provenance, narrower text sample bounds, and ordered intersecting atomic
  `coverageId`s.
- Reconnect and replay return the same stable segment ID. Multiple ordered
  segments may intersect one atomic chunk; a segment does not claim a partial
  audio-custody unit.

### 4.4 Durable transcript coverage: `transcript.coverage.durable`

Meaning:

- A single-writer transaction claimed one or more complete atomic chunks exactly
  once and referenced every durable segment intersecting them.
- If no valid final segment intersects an atomic chunk after bounded attempt
  drain, that chunk receives a durable gap instead.
- Reconnect and replay return the same stable terminal coverage identity. A
  duplicate or conflicting final is rejected; out-of-order finals are sorted by
  start bound, end bound, then provider ordinal before projection.

This watermark is for transcript completeness and UI reconciliation. It is not the raw-audio release watermark.

### 4.4 Required acknowledgement fields

Each acknowledgement includes:

- session, stream, source, and capture generation; STT attempt generation is
  nullable and absent for pre-provider local discard, privacy-timeout, and
  deletion gaps
- highest contiguous sequence for the named acknowledgement stage, or the
  exact terminal coverage ranges for segment/coverage events
- the corresponding sample and capture-time range
- server event ID and server time
- any missing or rejected ranges

For a terminal privacy release, it also includes the release kind, exact `gapId`
when boundaries are known, boundary status when the end is unknown, and the
deletion generation or privacy-deadline reason when applicable.

An acknowledgement must never advance past a gap. A sparse success is represented as explicit ranges, not as a misleading high-water mark.

## 5. Retry, reconnect, and idempotency

1. The companion retains each raw chunk in bounded memory until its
   `audio.forwarded` watermark advances past that chunk, or until a terminal
   privacy release below authorizes or requires zeroization.
2. On reconnect, the companion sends its last observed admission, forwarding,
   transcript-segment, and transcript-coverage watermarks.
3. The server returns the authoritative watermarks, active fencing generation, and exact resend range.
4. The client resends only ranges not authoritatively forwarded. Retries reuse their original event IDs and payload digests.
5. The gateway deduplicates already admitted or forwarded events and never creates a second terminal transcript or gap outcome for the same coverage identity.
6. A stale connection or stale fencing generation cannot admit, forward, pause, resume, or stop a session.
7. Text/metadata mutations remain in a durable encrypted outbox until `event.durable`; raw audio never enters that outbox.

If a failure leaves forwarding status ambiguous, the server must use its stream lease and content-free forwarding journal to resolve the range. If it can prove the affected sequence/sample boundaries but cannot prove safe replay or successful forwarding, it declares that exact range as a gap. If forced termination means the end boundary itself is unknowable, it records an honest unknown-coverage interval and reason instead of fabricating precision.

## 6. Gap contract

A `capture.gap` event is durable, user-visible, and contains:

- `gapId`
- session, stream, source, and capture generation; STT attempt generation is
  nullable and absent for pre-provider local discard, privacy-timeout, and
  deletion gaps
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
- `local_privacy_discard`
- `unknown_forwarding_state`
- `no_durable_transcript`
- `no_speech_final`
- `durable_discard`
- `privacy_timeout_local`
- `deletion_quiescing`

Gaps cannot be edited away. A user may add an explanation or exclude the
affected passage from assessment, but the coverage record remains.

`audio.discard.requested` is accepted only from the active authenticated
companion/fence (or its original fenced recovery lane) and identifies exact
retained ranges that will not be retried. The gateway revalidates ownership,
fence, boundaries, non-overlap, and the per-range effect state; when the discard
claim wins before `prepared`, it commits a durable `capture.gap` and returns
`audio.discard.durable` for that exact gap ID and range. The acknowledgement
authorizes ordinary zeroization of the unforwarded range; it never claims
provider forwarding. An explicit user discard is the named
`local_privacy_discard` release, distinct from ordinary zeroization: when the
gateway is reachable, the companion first performs the per-range discard CAS
and then zeroizes; when it is unreachable, it records the exact requested range
and zeroizes immediately, sending the discard request best-effort. A local
emergency kill, forced
process termination, deletion command, or the 30-second privacy deadline may
also zeroize before acknowledgement. In every pre-ack case the companion
retains only encrypted, content-free boundary/reason metadata. Gateway recovery
creates an exact or honest unknown-end discard gap when no provider effect is
pending, without re-enrolling merely to obtain an acknowledgement; an existing
effect follows the original-owner rule below. Store unavailability cannot extend
the raw-audio deadline, and a late provider callback cannot recreate or persist
content after
deletion quiescence.

Discard and provider effects use one per-range compare-and-set ordering. When
the gateway is reachable, the discard request linearizes before local
zeroization: a `local_privacy_discard` or durable discard claim that wins before
`prepared` prevents preparation and terminalizes the range as a discard gap. If
the compare-and-set sees `prepared` or `invoking`, the discard claim is rejected
as `effect_pending`; the original owner/fence resolves the effect as the
journaled `audio.forwarded` result or an ambiguous-effect gap after positive
provider/owner quiescence. The companion may still honor the user's privacy
action by zeroizing locally, but that range is then an emergency/local privacy
release with the original effect outcome, not a false discard acknowledgement.
If the gateway is unreachable, local zeroization is recorded first; recovery
must treat any existing prepared/invoking intent under its original owner/fence,
or create a discard gap when none exists, and may never prepare a new provider
call from the released range. A discard or timeout never advances
`audio.forwarded` as its own outcome; an already-pending original effect may
still journal `audio.forwarded` or an ambiguous-effect gap. Later callbacks can
write only content-free terminal metadata under the capture/fence generation,
or under the deletion generation when deletion triggered the release.

Terminal transcript and gap coverage for the same source/generation must not overlap. A transaction or equivalent single-writer projection enforces terminal uniqueness by coverage identity. Crash tests cover failure before and after simulated/provider write, forwarding-journal commit, transcript commit, reconnect negotiation, and STT-attempt rotation.

Atomic audio chunks are custody and terminal-claim units, not transcript-result
units. A provider may emit multiple ordered finals inside one chunk or one final
across several chunks. Each final is an immutable transcript segment with its
own text sample bounds and provider ordinal; a later durable coverage projection
claims the complete atomic chunks exactly once and references every intersecting
segment. A segment may have narrower text bounds inside an atomic chunk; only
terminal coverage claims whole chunks, and a second final cannot create a second
terminal audio outcome.

Finals are ordered by text start bound, text end bound, then provider ordinal.
An exact replay is idempotent. Reuse of a segment ID with changed text, bounds,
ordinal, or provenance fails closed; non-monotonic or partially overlapping
provider intervals remain separate segments with their real bounds. A terminal
coverage projection may claim only complete journaled-forwarded chunks, and any
forwarded chunk with no valid intersecting segment after bounded drain receives
`no_durable_transcript` or `no_speech_final` gap coverage.

## 7. Pause, stop, and finalization protocol

- `capture.pause.requested` is a command. Physical capture stops only when the companion emits `capture.paused` with the authoritative capture generation and last captured sequence per source.
- Pre-pause chunks may continue through admission, forwarding, and transcript finalization. The UI must say that previously captured audio is finishing.
- Resume creates no overlapping sequence range and is rejected if consent, permission, lease, or device health is invalid.
- `session.stop.requested` causes the companion to stop acquiring new audio immediately and emit `capture.stopped` with final captured ranges.
- The user chooses whether still-unforwarded in-memory audio is sent or
  discarded. Discard is an explicit local privacy action that clears the bytes
  immediately and records the requested range; `audio.discard.durable` later
  confirms the gateway's exact gap when reachable. A timeout or forced loss may
  zeroize first and later records exact or unknown-end coverage.
- `session.finalizing` is a gateway coverage state, not a companion physical
  state. It continues until every captured range is represented by a durable
  transcript coverage event or durable gap.
- `session.completed` is a derived web display state only after companion
  `capture.stopped`, transport closure, and gateway terminal coverage.
  Completion is not assessment approval.
- `session.delete.requested` first enters gateway `delete_quiescing`: new
  admission, reconnect, provider intents, and content writes are fenced. A
  prepared-but-not-invoking intent becomes a discard gap; an invoking intent
  stays with its original owner/fence and is journaled or gap-terminalized only
  after positive owner/stream quiescence. Every worker and provider callback
  acknowledges the deletion generation before `session.deleting` starts.

Web commands are requests. Only companion-originated capture events can assert that physical capture started, paused, resumed, or stopped.

## 8. Minimum event set

- `session.enrolled`
- `session.start.requested`
- `capture.starting`
- `capture.active`
- `audio.chunk`
- `audio.admitted`
- `audio.forwarded`
- `audio.discard.requested`
- `audio.discard.durable`
- `transcript.interim`
- `transcript.final`
- `transcript.segment.durable`
- `transcript.coverage.durable`
- `capture.gap`
- `capture.pause.requested`
- `capture.paused`
- `capture.resume.requested`
- `session.stop.requested`
- `capture.stopped`
- `session.finalizing`
- `session.completed`
- `session.delete.requested`
- `session.delete.quiescing`
- `session.deleting`
- `session.deleted`
- `session.deletion_failed`
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
- a discard claim that wins before provider preparation, or a local
  emergency/privacy-timeout zeroization with no pending effect, creates only a
  terminal privacy gap; an existing effect retains its original forwarded or
  ambiguous outcome and is never attributed to discard;
- pause and stop ranges contain no newly captured audio after the companion's authoritative boundary;
- deletion fences new effects, waits for positive provider-effect quiescence,
  rejects late callbacks before content persistence, and requires an
  independent absence inventory before `session.deleted`;
- logs contain no audio, transcript, notes, documents, credentials, or payload digests usable as content identifiers;
- a 60- and 90-minute synthetic test stays within approved memory and message limits.

Future G2/v2 source evidence must run these semantics against one canonical
versioned schema, Swift and Python binding validation, a deterministic provider
simulator, fixed synthetic-byte manifests, and process networking disabled. The
existing Phase 1A result is v1-only and does not qualify this amendment. G2 must
abort on credential lookup, implicit environment/project selection, network
access, non-fixture input, or persistent audio. Hosted authentication and
provider tests belong to separately authorized Phase 1B.

## 10. Unresolved implementation choices

- Binary framing and schema language implementation details; the G2-A0 target is
  one length-prefixed binary frame with JSON Schema 2020-12 metadata.
- Provider/runtime implementation of the fixed v2 target bounds: 20–250 ms
  chunk duration, 64,000-byte payload, two-second rate-derived custody, and
  30-second absolute expiry.
- STT stream-rotation interval.
- Fencing and forwarding-journal storage implementation.
- The measured bounded-memory duration after the spike.

These choices may be resolved during the spike if they preserve the semantics above.
The G2-A0 amendment fixes the protocol-v2 custody semantics: `audio.forwarded`
is the only successful provider-forwarding watermark. A discard CAS that wins
before `prepared` creates the durable discard gap; named
`local_privacy_discard`, `audio.discard.durable`, and local
emergency/privacy-timeout zeroization are terminal privacy releases that never
attribute an already-pending provider effect to the discard acknowledgement. If
an effect is already pending, its original owner produces the forwarded or
ambiguous-effect outcome. Changing those semantics,
the bounded ingress budgets, or the deletion quiescence barrier requires a new
architecture decision and security review.

## 11. G2-A0 amendment boundary

This owner-authorized documentation amendment synchronizes ADR 0002 with ADR
0003, the native-capture roadmap, and the privacy contract. It is a design
decision only. It becomes operative for a later source plan only after the four
documents are reviewed together at one exact commit/tree by architecture and
security/privacy. Until that review succeeds, the existing implementation gate
remains closed and no source, hosted, provider, device, credential, capture,
deployment, merge, or release action is authorized.
