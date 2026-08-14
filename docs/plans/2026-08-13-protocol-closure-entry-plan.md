# Protocol Closure Entry Plan

**Status:** Proposed documentation-only G2 plan with a conditional G2-A0
follow-up. Exact-tree staff and security/privacy review is required before any
source corridor. No implementation or live authority is granted by this
document.

**Date:** 2026-08-13

**Governing artifacts:**

- `docs/architecture/0002-companion-stream-protocol.md`
- `docs/architecture/0003-native-capture-launch-boundary.md`
- `docs/plans/2026-08-13-native-capture-launch-roadmap.md`
- `docs/privacy/data-flow-retention-contract.md`
- `docs/reviews/2026-08-13-pr8-native-launch-salvage-audit.md`
- `docs/reviews/2026-08-13-n11dc-native-launch-salvage-audit.md`
- `docs/reviews/2026-08-14-g2a0-final-attestation.md`

## 1. Purpose

Close the remaining wire, custody, fencing, rotation, compatibility, health,
and disclosure choices before a gateway or native companion implementation
branch begins. G2 produces source/offline protocol evidence only. It does not
authorize credentials, network access, a hosted endpoint, native API calls,
permission prompts, device audio, provider calls, or participant data.

The existing protocol v1 evidence remains historical and reproducible. The
closure work creates **protocol v2** rather than silently changing v1 bounds or
wire behavior under an already qualified version.

## 2. Inputs and exclusions

### Inputs

- Existing v1 JSON schema, Python model/simulator, Swift bindings, vectors, and
  60/90-minute generated-byte tests.
- ADR 0002's three watermarks, retry identity, gap, pause/stop, and terminal
  coverage semantics.
- PR #8's negative auth matrix, bounded provider work, STT drain/rotation
  cases, deletion invariants, and WebSocket backpressure tests as behavior
  references only.
- The clean committed native lineage at `12e8b08` as a future consumer, not a
  file-edit destination during G2.
- N11D-C's durable-before-effect and acknowledged-terminal-before-progress
  ordering as a fault-model reference only.

### Exclusions

- No PR #8 cherry-picks.
- No N11D-C staged or unstaged edits.
- No Firebase, Firestore, Google STT, Vertex, Cloud Run, ADC, or secret access.
- No ScreenCaptureKit, AVAudioEngine, CoreAudio, WASAPI, microphone, route,
  process, socket, or browser execution.
- No raw audio fixture file. Payload vectors remain deterministic generated
  bytes held in memory.

## 3. Proposed v2 decisions to freeze

These are the recommended defaults the implementation review must either
approve exactly or replace explicitly before the first source edit.

### 3.1 Transport and framing

- WebSocket subprotocol: `tars.capture.v2`.
- One protocol event per WebSocket message.
- Control events use UTF-8 canonical JSON and no binary payload.
- Audio events use one binary frame:
  1. four-byte unsigned big-endian metadata length;
  2. that many bytes of canonical UTF-8 JSON metadata; and
  3. the raw `pcm_s16le` payload whose exact byte count is declared in metadata.
- Canonical JSON uses the JSON Canonicalization Scheme in RFC 8785 with this
  stricter profile:
  - member names sort by RFC 8785's UTF-16 code-unit order and string escaping,
    literals, and whitespace follow that RFC exactly;
  - every member name and string value must arrive as valid Unicode scalar
    values already in NFC; invalid UTF-8, lone surrogates, and non-NFC strings
    are rejected rather than normalized;
  - duplicate keys are rejected before object materialization and schema-
    unknown fields are rejected;
  - JSON numeric values are non-negative integers from `0` through
    `9007199254740991`; negative values, fractions, exponent notation, `NaN`,
    infinity, and overflow are rejected; and
  - schema fields that require the full unsigned 64-bit domain, including
    monotonic-nanosecond values, use canonical decimal strings matching
    `0|[1-9][0-9]{0,19}` and are rejected above
    `18446744073709551615`.
  A receiver must parse, reserialize, and byte-compare the metadata before any
  mutation. Python, Swift, and C# use the same checked numeric domains rather
  than native-language defaults.
- Control-event metadata is capped at 65,536 bytes. Audio-event metadata has a
  stricter 4,096-byte cap, and its payload is capped at 64,000 bytes. An audio
  WebSocket message is therefore capped at 68,100 bytes including the
  four-byte prefix.
- The payload digest covers payload bytes only. Event identity is derived from
  the protocol's typed identity fields, not from serialized JSON text.
- A retry must reuse the same event ID, typed metadata, payload length, and
  payload digest. Any mismatch is a terminal protocol rejection.
- Admission creates a session-scoped durable retry commitment before returning
  `audio.admitted`. Its exact input is
  `HMAC-SHA256(sessionKey, "tars-retry-v2\0" || uint32be(metadataLength) ||
  canonicalMetadata || uint32be(payloadLength) || payload)`. The typed event
  identity is required inside the canonical metadata. The key is random per
  session with at least 256 bits from the platform cryptographic generator;
  keys are envelope-encrypted under a versioned managed key and HMAC comparison
  is constant-time. Keys and commitments are tenant-scoped, never logged or
  exported, never reused across sessions, and deleted with the session. They
  are candidate-associated security metadata. A restart or reconnect
  recomputes the commitment to distinguish an identical resend from changed
  bytes without creating a cross-session content identifier.

Control traffic is separately bounded:

- at most 65,536 canonical JSON bytes per control event;
- at most 20 client control events per second sustained over ten seconds and a
  token-bucket burst of 40;
- at most 32 unacknowledged client mutation IDs and at most one outstanding
  lifecycle command of each kind;
- exactly one session and capture generation and at most two source streams per
  companion connection; and
- one active companion connection per lease/fence, with a replacement fencing
  and closing the old connection.

Crossing a byte, rate, burst, outstanding-command, stream, or connection limit
fails closed before mutation. Repeated rate violations close the connection
with a non-enumerating retry code; they never evict an earlier authorized
command or audio range.

Audio ingress, including exact retries and resends, is independently token-
bucketed before queue allocation or durable mutation. The four-byte prefix is
charged with metadata bytes. Protocol-v2 source/offline defaults are:

| Scope | Active sessions | Audio events refill / burst | Payload bytes refill / burst | Metadata-plus-prefix bytes refill / burst | Resident gateway custody | Provider attempts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Source | n/a | 50/s / 100 | 192,000/s / 384,000 | 205,000/s / 410,000 | 1,048,576 bytes | 1 writable + 1 draining |
| Session, two sources | 1 | 100/s / 200 | 384,000/s / 768,000 | 410,000/s / 820,000 | 2,097,152 bytes | 2 writable + 2 draining |
| Tenant | 4 | 400/s / 800 | 1,536,000/s / 3,072,000 | 1,640,000/s / 3,280,000 | 8,388,608 bytes | 8 writable + 8 draining |
| Gateway process | 16 | 1,600/s / 3,200 | 6,144,000/s / 12,288,000 | 6,560,000/s / 13,120,000 | 33,554,432 bytes | 32 writable + 32 draining |

Every row is enforced; passing a narrower row never bypasses a broader one.
Duplicates rejected by identity still consume ingress tokens so replay cannot
be a denial-of-service bypass. A two-second resend fits the source/session
burst but then consumes refill capacity. Excess fails before admission, does
not evict retained audio, stops new source acquisition when custody cannot
advance, and follows the terminal gap/discard rules instead of silently
dropping a chunk. A later deployment may lower these ceilings through renewed
review; raising any ceiling or active-session count requires a new exact plan
and evidence.

Source/session buckets bind to capture generation and fence and survive
reconnect; reconnect never grants fresh burst capacity. Tenant counters and
active-session/attempt reservations are authoritative across gateway instances
and use server time. An unavailable or stale shared-quota authority fails
closed before admission or provider effect. Process counters use a monotonic
clock and never substitute for the shared tenant ceiling.

The transport edge is bounded before authentication as well:

- at most 64 pending TLS/WebSocket handshakes per gateway instance and 16 per
  source IP, with a five-second handshake deadline and a further three-second
  deadline for the first authentication event;
- at most 16,384 header bytes, 8,192 bytes for the initial authentication
  event, and 32,768 receive-buffer bytes per pending connection;
- at most 2,097,152 aggregate bytes across pending connection receive buffers;
- binary/audio messages are rejected before authentication without allocating
  the declared metadata or payload body; and
- after authentication, each of the at most 16 process connections has one
  68,100-byte streaming-parser buffer, for an additional exact aggregate cap of
  1,089,600 bytes. These 3,186,752 transport bytes are separate from, and in
  addition to, the 33,554,432-byte process custody ceiling in the table.

The edge proxy and application both enforce the limits; disagreement uses the
lower value. A streaming length-prefixed parser rejects an oversized declared
length before body allocation. Slow, fragmented, unauthenticated, invalid, and
replayed handshakes consume the pending count and deadline and cannot reach the
tenant/session admission path. Hosted work must add an independently enforced
global/distributed connection and request ceiling before public ingress; the
source-only process limits are not a volumetric denial-of-service claim.

The existing schema language remains JSON Schema 2020-12 so Python, Swift,
and Windows bindings can share one reviewable source without a new runtime
serialization dependency. Generated binding types may be handwritten only
when exact cross-language vectors prove equivalent validation.

### 3.2 Audio chunk and memory bounds

- Encoding: signed 16-bit little-endian interleaved PCM.
- Allowed sample rates: 8,000 through 48,000 Hz.
- Allowed channels: one or two.
- Sample rate and channel count are immutable for one source stream and capture
  generation. A format change requires a new stream ID and capture generation;
  mixed-format chunks in one stream fail closed.
- Nominal chunk duration: 100 ms.
- Allowed duration: 20 through **250 ms**, with exact integer alignment among
  samples, rate, channels, duration, and payload bytes.
- Maximum payload: 64,000 bytes, independently enforced even when duration is
  valid.
- Per-source companion custody is a true two-second duration bound:
  `retainedFrames <= min(96_000, 2 * sampleRate)` and
  `retainedPayloadBytes <= min(384_000, retainedFrames * channels * 2)`.
  Reservations and in-flight handoff count before allocation. Thus 8-kHz mono
  permits at most 16,000 frames/32,000 bytes, while 48-kHz stereo permits at
  most 96,000 frames/384,000 bytes.
- Per-session custody is the sum of the two independently derived source
  limits and is also hard-capped at 192,000 frames and 768,000 raw payload
  bytes. Mixed-rate sources use their own rate-derived limits; one source
  cannot consume the other's unused capacity.
- In both companion and gateway, each source may retain at most 100 queued
  audio events, the maximum possible count in two seconds at the 20-ms minimum
  duration. Per source, serialized audio metadata is capped at 409,600 bytes,
  raw payload at 384,000 bytes, reservation objects at 100, and queue objects
  at 100.
- The gateway transient queue applies the same frame, event-count, metadata,
  raw-byte, reservation, and queue-object ceilings. Its total retained
  allocation is capped at 1,048,576 bytes per source and 2,097,152 bytes per
  two-source session, including parsed objects and container overhead. The
  companion and gateway implementations must measure actual object/container
  overhead and fail closed before either resident-allocation ceiling, not
  estimate raw bytes alone.
- The tenant and process allocation ceilings in Section 3.1 include raw bytes,
  metadata, parsed objects, reservations, containers, and attempt state; no
  source/session allocation can be hidden outside those totals.
- Crossing a frame, byte, event, reservation, resident-allocation, rate,
  burst, or duration ceiling rejects further custody, stops new acquisition,
  moves the source to degraded state, and terminalizes the exact captured
  range as a gap when boundaries are known. It never overwrites the oldest
  unforwarded chunk.
- Raw custody also has a 30,000-ms expiry from capture, measured with a
  continuous boot clock that advances across suspend where the platform
  provides one. At 10,000 ms without forwarding or a terminal release,
  acquisition stops and bounded reconciliation begins. Before an intentional
  sleep/suspend acknowledgement, the companion zeroizes retained audio. After
  an unannounced process suspension, the process cannot execute erasure; on its
  first scheduled instruction after resume it must zeroize expired or
  clock-uncertain custody before reading it, opening a connection, or resuming
  capture. Forced termination naturally loses process memory. G2 evidence must
  state this scheduling limitation and cannot claim physical RAM erasure at
  the exact wall-clock deadline while the operating system has frozen the
  process.
- At expiry the companion invokes the proposed local privacy-timeout
  zeroization, retains only encrypted content-free range metadata, and later
  reports an exact or honest unknown-end `privacy_timeout_local` gap; it never
  claims forwarding. Gateway raw bytes obey the same expiry: it durably gaps a
  known range first when that transaction is immediately available, but store
  unavailability cannot extend raw custody. At expiry it zeroizes raw bytes,
  retains only the fenced content-free terminalization obligation, and commits
  the exact/unknown-end gap when durability returns. It never advances
  `audio.forwarded` or reports completion meanwhile.
- These local/gateway terminal releases are non-operative unless the
  prerequisite governing-artifact amendment in Section 3.6 is owner-approved.
  If the amendment is rejected, the G2 design must replace this liveness rule
  before implementation; it may not retain bytes indefinitely under the
  forwarding-only contract.

The two-second bound aligns the existing offline protocol batches and the
native 96,000-frame absolute ledger without mislabeling lower sample rates.
G2 must cover 8-kHz and 48-kHz, mono and stereo, mixed-rate simultaneous
sources, every 20/250-ms boundary, two-source long-duration cases, burst
resends, the 10/30-second custody deadlines, and adversarial 20-ms minimum-
payload/4,096-byte-metadata traffic. The current v1 long-duration test
exercises one source at a time and therefore proves neither the session
aggregate nor metadata/object/rate custody.

### 3.3 Provider attempt rotation

- Default Google STT write window: 270 seconds.
- Stop writing to an attempt only at a complete chunk boundary.
- A replacement attempt increments `sttAttemptGeneration` and starts with the
  next not-yet-forwarded sequence. No audio chunk is deliberately written to
  two attempts.
- At rotation the old attempt becomes read-only/draining and its request is
  half-closed. A replacement attempt becomes the sole writable attempt and may
  accept the next not-yet-forwarded chunk while the old attempt receives a
  bounded ten-second response drain. Per source there is at most one writable
  and one draining attempt; the draining attempt never accepts audio, and its
  response processing shares the same terminal-coverage writer.
- Opening the replacement cannot wait for the old response drain. Audio during
  the bounded open transition remains in the normal queue; reaching any queue
  ceiling creates the exact overflow gap and degraded state rather than
  extending custody.
- Attempt generation is provenance and never changes `coverageId`, event ID,
  or terminal uniqueness.
- If the gateway cannot prove whether a range reached the provider, it does
  not replay that range. It records `unknown_forwarding_state` for the exact
  known range, or an honest unknown-end gap when the end cannot be recovered.
- The old `stt_stream_overlap_seconds` setting is removed from the target
  contract unless a later reviewed design defines actual duplicate-provider
  semantics. A configuration value alone is not overlap evidence.

### 3.4 Capture lease and fencing store

Use a durable Firestore control-plane record for the first gateway slice:

`sessions/{sessionId}/captureControl/lease`

The record contains server-derived organization and user references, companion
enrollment ID, capture generation, monotonic fencing generation, issued/renewed
server times, expiry, protocol version, and state. It contains no token,
payload digest, audio, transcript, note, document text, or raw device name.

- Acquire, renew, revoke, and replace use transactions with a precondition on
  the last observed version.
- Replacement increments the fencing generation; it never reuses one.
- Every connection and event is bound to one authenticated enrollment,
  session, capture generation, and fence.
- The gateway transactionally revalidates the durable fence and creates one
  deterministic, durable forwarding intent before each provider write. A
  provider call without that prepared intent is forbidden.
- Intent creation is a compare-and-create claim. Two workers holding the same
  fence cannot both own or invoke the provider for the same range; a foreign,
  pre-existing, mismatched, or already-terminal intent fails closed.
- Each intent has the state sequence `prepared -> invoking -> journaled` or
  `prepared|invoking -> ambiguous_gap`. `prepared` records the one owning
  runtime instance and runtime epoch. Only that owner may transactionally
  change it to `invoking`, after freshly revalidating the lease, fence,
  attempt, deletion generation, and its serialized provider-effect lane. An
  intent is never reassigned and no recovery worker may invoke the provider
  for it.
- The provider call is permitted only from the uninterrupted call stack that
  successfully performed `prepared -> invoking`, while it holds a non-
  serializable process-local execution token. A restarted runtime, including
  the same logical owner ID, sees `invoking` but can never reconstruct that
  token or issue the call.
- Once an intent is `invoking`, a failed journal write is retried without a
  second provider invocation. Recovery may create an ambiguous-effect gap only
  after positive proof that the owning runtime and provider stream are
  quiescent or a reviewed deployment egress fence makes another call
  impossible. Heartbeat expiry, lease TTL, loss of a Firestore lock, or elapsed
  time alone is not quiescence proof. Without proof, replacement and deletion
  remain fail-closed in `effect_quiescence_required`; liveness does not outrank
  at-most-once effect custody.
- Every provider effect lane has a process-start `runtimeEpoch` and a
  single-use non-serializable call token. Revocation, deletion, deployment, or
  lease replacement first publishes an egress fence for the session/fence and
  runtime epoch, closes or cancels the provider stream, and waits for a
  provider-close acknowledgement plus owner termination acknowledgement. A
  replacement runtime cannot reconstruct the token or invoke an old intent.
  If the egress fence or provider-close acknowledgement cannot be proven, the
  durable state is `effect_quiescence_required` (a non-success operational
  state); it cannot transition to `deleted`, `completed`, an ambiguous gap, or
  a retryable replacement effect merely because a heartbeat or TTL expired.
  Operational escalation may remain indefinite, but it must not imply a privacy
  or provider-deletion success claim.
- Revocation or replacement first closes admission and marks the lease as
  revocation-requested. It cannot increment the fencing generation until every
  range below reaches the required durable terminal state:
  - companion-known but unadmitted ranges become exact discard gaps when the
    companion supplies both boundaries, or one honest unknown-end gap from the
    last server-known boundary when it cannot;
  - admitted but unprepared gateway ranges become exact durable discard gaps
    without a new provider call;
  - prepared or invoking ranges finish under their original owner/fence as a
    journaled forward or, only after positive owner/stream quiescence, an exact
    ambiguous-effect gap and are never replayed or taken over;
  - forwarded but untranscribed ranges finish the bounded response drain and
    become a durable transcript or gap; and
  - every related companion discard receives `audio.discard.durable` before
    ordinary local release.
- Once revocation-requested, no new admission or forwarding intent may start.
  The old fence may perform only the listed terminalization work. After every
  known range is terminal, every effect owner is positively quiescent, and one
  durable unknown-end gap honestly closes any unknowable tail, the transaction
  increments the fence and closes the old enrollment's authority.
- Before an already-authenticated old connection closes, the gateway sends all
  available forwarding/discard receipts for its exact old ranges without
  granting new admission. An unreachable old connection is not re-enrolled to
  fetch an acknowledgement: its companion hits the 30-second privacy deadline,
  zeroes locally under the proposed exception in Section 3.6, and the gateway
  records an exact or unknown-end gap. Fence replacement still waits for
  provider-effect quiescence, not for indefinite raw-byte retention.
- The gateway revalidates the durable fence before any pause, resume, stop, or
  terminal commit.
- Expiry or disagreement closes admission and provider forwarding before any
  reconnect decision. A stale process cannot infer authority from its local
  cache.
- Lease TTL cleanup is housekeeping only. Expiration authority comes from the
  stored server timestamp and transaction checks, not eventual TTL deletion.

Firestore is selected here because the existing product already uses it for
session durability and because the first gateway does not require a second
database solely for control-plane fencing. The hosted plan must benchmark the
exact transactional rate and revisit the implementation before activation if
it cannot sustain the bounded synthetic load.

### 3.5 Content-free forwarding journal

Use immutable deterministic intent and journal documents below the
session/stream/attempt, one pair for each contiguous forwarding batch. A batch
closes at the first of:

- ten chunks;
- one second of audio;
- 64,000 aggregate payload bytes;
- pause, stop, rotation, reconnect, or queue pressure.

Before the provider call, a transaction verifies the active lease/fence and
creates a deterministic `prepared` intent containing only server-derived
tenant/session/stream references, source, capture/fence/attempt generations,
first and last sequence, first and last sample, monotonic coverage bounds,
owner instance/epoch, retry-commitment reference, state, and intent version.
It stores no audio, transcript, payload digest, raw device name, credential,
or candidate content.

These records are content-free but **candidate-associated pseudonymous
metadata**, not anonymous or non-personal data. They require tenant-scoped
authorization, encryption in transit and at rest, access audit, the session's
retention/deletion lifecycle, incident handling, and no use for analytics,
training, support export, or cross-tenant correlation.

The exact owner first performs the `prepared -> invoking` transaction described
in Section 3.4, makes the one provider call from its serialized effect lane,
and then terminalizes the intent and creates the immutable forwarding journal
with the same typed range plus provider-write completion server time and
journal version. If the effect is ambiguous, recovery waits for positive
owner/stream quiescence before terminalizing the intent as a durable exact
gap; the prepared intent already fixes both boundaries. Neither recovery nor a
resumed owner invokes the provider after the intent is terminal.

`audio.forwarded` advances only after the immutable journal document is
created. Deterministic intent/journal identity makes replay idempotent. A
pre-existing document must match the exact typed range and generations or the
operation fails closed. A revocation may block while one already-prepared
batch reaches a terminal journal-or-gap outcome, but it blocks all new
admission and intents immediately. Journal compaction may build derived
watermarks but never deletes the immutable entries before the session's
retention/deletion policy authorizes it.

Every admission and forwarding acknowledgement carries the authoritative
ordered disjoint `stageIntervals` array and a nullable derived contiguous
prefix; reconnect requests and responses carry the same interval representation
plus an exact resend interval set. The array is sorted by
`(firstSequence, firstSample, lastSampleExclusive)`, rejects duplicates and
overlap, and uses canonical unsigned decimal strings for values outside JSON's
safe integer range. A scalar high-water sequence is never a release authority.

The admission ledger, intents, and journals include the session-scoped retry
commitment reference needed to validate an exact resend after restart. The
commitment itself is never exposed in acknowledgements, logs, telemetry,
support output, or analytics and is deleted with all other session metadata.

### 3.6 Proposed terminal gap and raw-audio discard acknowledgement

This section recommends a protocol-v2 architecture change; it is **not** an
operative exception to the current forwarding-only release contract. The
owner-authorized G2-A0 amendment is conditional until the semantic follow-up
and exact-tree review record are complete. Before any G2 source edit, all of
these governing artifacts must be reviewed together:

- `docs/architecture/0002-companion-stream-protocol.md`;
- `docs/architecture/0003-native-capture-launch-boundary.md`;
- `docs/plans/2026-08-13-native-capture-launch-roadmap.md`; and
- `docs/privacy/data-flow-retention-contract.md`; and
- `docs/reviews/2026-08-14-g2a0-final-attestation.md`.

The amendment must name durable discard and local emergency/privacy-timeout
zeroization as terminal privacy releases distinct from successful provider
forwarding, and must reconcile custody, retry, UI, reconnect, deletion,
retention, and claim semantics. It requires renewed architecture and
security/privacy approval. If the owner rejects or has not approved that exact
amendment, `audio.forwarded` remains the exclusive release watermark and G2
implementation stays blocked rather than silently omitting this choice.

`audio.forwarded` remains the only success watermark that releases audio as
provider-forwarded. Protocol v2 additionally needs one explicit terminal
release outcome for ranges that must never be retried:

- `audio.discard.requested` identifies exact retained ranges the companion or
  recovery state proposes to abandon;
- the gateway commits the non-overlapping durable `capture.gap` outcome; and
- `audio.discard.durable` acknowledges that exact gap ID and ranges.

When the gateway is reachable, only the durable discard acknowledgement
authorizes ordinary companion zeroization of an unforwarded range. It does not
advance the forwarded watermark, cannot cover a range already forwarded or
terminalized, and is idempotent on exact event/range identity. A reachable
`effect_pending` response is not a discard acknowledgement: the companion may
zeroize for the user's local privacy action, but the original owner/fence must
later produce the forwarded or ambiguous-effect outcome. When the gateway is
unreachable, `local_privacy_discard` and deletion/privacy-timeout zeroization
are the explicit pre-ack exception: the companion records the exact or honest
unknown-end boundary and zeroizes immediately, and recovery must not prepare a
new provider effect from the released range. Admission, a local timeout, an
error response, or an uncommitted gap never authorizes ordinary durable
discard release.

An explicit local emergency kill, forced process termination, deletion command,
or the 30-second privacy deadline may zero raw bytes before server
acknowledgement to honor the user's privacy action. On recovery the companion
must report the last provable boundary and the gateway must create the
corresponding exact or unknown-end durable gap; it must never claim those bytes
were forwarded. Only encrypted content-free boundary/reason metadata may
survive locally, under the session retention/deletion policy. These behaviors
remain proposed until the prerequisite governing amendment above is approved.

### 3.7 Terminal coverage, transcript identity, and uniqueness

Every admitted audio chunk has one atomic `coverageId` derived from the exact
v2 session, stream, capture generation, source, sequence, and half-open sample
range. A terminal outcome has `terminalCoverageId` equal to `covr_` plus
lowercase SHA-256 over a canonical NUL-separated UTF-8 encoding of:

1. UTF-8 `tars-terminal-coverage-v2`, session, stream, capture generation, and
   source fields separated by one NUL byte;
2. a big-endian unsigned 32-bit count; and
3. for each atomic `coverageId` in order, a big-endian unsigned 32-bit UTF-8
   byte length followed by its UTF-8 bytes.

The ordered list is sorted by `(sequence, firstSample, lastSampleExclusive,
coverageId)` ascending; duplicate tuples, overlapping atomic ranges, embedded
NUL bytes, non-NFC IDs, and lengths above `2^32-1` fail closed. The first/last
sequence and sample values are derived display summaries only and are not
identity inputs. This full-list digest is required because two provider finals
can share one chunk and sparse success can contain a later forwarded range
after an earlier gap. A terminal claim with a different ordered atomic list is
a different claim even when endpoint ranges match. Terminal **audio coverage**
and transcript-segment identity are separate:

- `transcript.segment.durable` stores one immutable final-text segment with a
  `segmentId`, ordered provider-result ordinal, exact provider provenance,
  `textFirstSample`, `textLastSampleExclusive`, and the ordered complete atomic
  chunks it intersects. Its ID is `seg_` plus lowercase SHA-256 over these
  canonical fields: literal `tars-transcript-segment-v2`, session ID, stream ID,
  capture generation, source, the complete ordered atomic `coverageId` list
  encoded with the same count/length-prefix rules, the two text sample bounds,
  and the non-negative result ordinal. The suffix then encodes provider
  provenance as length-prefixed UTF-8 `providerName` and `providerResultId`, a
  one-byte presence flag plus big-endian uint64 `sttAttemptGeneration` when
  present, and no other fields. Text/sample/ordinal values use big-endian
  uint64; every string is NFC, rejects embedded NUL, and is length-capped at
  `2^32-1`. The text is not an ID input; replay with the same ID and different
  text, provenance, bounds, ordinal, or intersected list fails closed.
- Multiple ordered segments may reference the same atomic chunk. A final
  inside one chunk, two finals inside one chunk, and one final spanning several
  chunks are therefore representable without making transcript text itself an
  audio-custody claim.
- After bounded attempt drain, `transcript.coverage.durable` claims one or more
  complete atomic chunks exactly once and references the ordered set of all
  durable segment IDs intersecting them. Its `terminalCoverageId` uses the
  complete ordered atomic-list derivation above. If no valid final segment
  intersects an atomic chunk, that chunk receives a durable gap instead.
- A single transaction or equivalent single-writer compare-and-set claims
  every atomic `coverageId` for either `transcript.coverage.durable` or a gap.
  Any previously terminalized atomic chunk conflicts, even when a different
  range ID partially overlaps. Transcript segment records do not claim atomic
  coverage and cannot make finalization complete by themselves.

Provider offsets are provenance, not authority to invent audio boundaries.
Final text may reference only complete journaled-forwarded chunks it
intersects, while its narrower text sample bounds remain visible. Provider
results are sorted by start bound, end bound, then the provider's stable result
ordinal. An exact duplicate is idempotent; reused identity with different text,
bounds, ordinal, or provenance fails closed. Non-monotonic or partially
overlapping text intervals remain separate segments with their real bounds;
they are never trimmed into fabricated text. The terminal audio projection
still claims only whole atomic chunks.

At attempt drain, pause, stop, forced termination, or finalization, every
forwarded atomic chunk without a durable intersecting final segment becomes a
durable, user-visible gap. Adjacent chunks with the same reason may coalesce.
Silence with a completed provider attempt uses `no_speech_final`; a spoken or
unknown range lacking a final uses `no_durable_transcript`; an unknowable tail
uses the existing unknown-end form. Trailing silence is never silently omitted.
No terminal audio-coverage event or gap may claim only a fraction of an atomic
chunk.

### 3.7.1 Frozen versus unresolved disposition

The following v2 semantics are frozen for any later source corridor: canonical
framing and numeric validation, source/session/tenant/process quotas, the
sample-rate-derived two-second custody bound and 30-second absolute expiry,
`audio.forwarded` as the only provider-success watermark, per-range discard CAS
ordering, full-list terminal coverage identity, positive provider-effect
quiescence, deletion generations, and late-callback fencing. A source
implementation may choose data structures, transaction APIs, or provider
adapter details only when those choices cannot alter these invariants.

Measured allocator overhead, the storage product's performance profile, the
provider adapter's concrete cancellation API, and the exact STT rotation
interval remain implementation-defined and require G2/G3 evidence. Any change
to a frozen invariant, quota, custody deadline, effect-fence rule, or terminal
identity requires a new ADR and renewed architecture/security review; writing a
value in a source plan does not make it hosted-capacity, provider-retention, or
launch evidence.

### 3.8 Compatibility and upgrade rules

- Enrollment returns the gateway's exact supported protocol-version set.
- Capture starts only when companion and gateway agree on one exact version;
  there is no silent downgrade.
- A connection cannot change versions in place.
- Reconnect for an active capture requires the same protocol version, capture
  generation, and compatible event schema.
- A companion upgrade during capture must first reach a companion-confirmed
  stop and terminal coverage. The next start uses a new capture generation.
- V1 reads are confined to an explicit read-only migration/review adapter. It
  reconstructs and validates tenant ownership before returning data and cannot
  create or renew enrollment, disclosure, lease, fence, watermark, custody,
  resend, forwarding, or terminal state for v2. All new capture writes use v2;
  dual-read authority and dual-write are prohibited.
- Unsupported versions fail with a stable non-enumerating error code and
  update guidance that reveals no session or tenant existence.
- Removing a supported version requires evidence that no active lease or
  resumable unforwarded custody remains on it.

### 3.9 Platform-neutral health and lifecycle events

No actor reports one authoritative top-level lifecycle state. Every event has
an explicit `origin`, monotonic `originStateVersion`, session/capture/fence
identity, and server receive time. State authority is separated:

- Companion-originated `physicalCaptureState` is one of `setup_required`,
  `checking_permissions_and_devices`, `ready_both_sources`, `starting`,
  `recording`, `degraded`, `reconnecting`, `paused`, `stopping`, or `stopped`.
  It also carries per-source permission/device/capture health and last captured
  sequence/sample. Only the companion can advance physical boundaries.
- Gateway-originated `transportState` is one of `disconnected`, `admitting`,
  `forwarding`, `draining`, `fenced`, or `closed`, with authoritative
  admission/forwarding interval sets and derived prefixes.
- Gateway-originated `coverageState` is one of `not_started`, `open`,
  `finalizing`, `completed`, `completed_with_gaps`, `delete_quiescing`,
  `deleting`, `deleted`, or `deletion_failed`, with durable transcript/gap
  watermarks and deletion generation. Only the gateway can assert durable
  finalization or completion.

The web derives its display state from the newest compatible versions of all
three axes using a versioned precedence table shipped with the protocol. A
missing, stale, conflicting, or future-version axis fails to the more
conservative visible state: deletion overrides all; a companion degraded or
unknown physical state cannot display healthy recording; gateway finalizing
cannot display completed; and completion requires companion `stopped`, closed
transport, and gateway terminal coverage. Commands remain requests. Source
loss immediately produces companion degradation and a gateway gap where
applicable; the other source cannot make the whole session appear healthy.

### 3.10 Pre-capture disclosure acknowledgement

The gateway stores a server-side acknowledgement containing:

- session;
- server-derived organization and actor;
- exact notice version and locale;
- acknowledgement server time;
- declared legal basis code from an approved closed set;
- acknowledgement status and optional revocation server time; and
- immutable acknowledgement ID.

Start and audio admission require an active acknowledgement whose session,
actor, organization, notice version, and legal-basis policy match the current
lease. Request-supplied ownership is never authority. Revocation stops new
capture admission and follows the explicit pause/stop/gap contract. Neither a
transcript nor a client-only flag is proof of disclosure acknowledgement.

### 3.11 Deletion lifecycle and quiescence boundary

G2 models deletion state and events offline; physical storage deletion remains
a G3A implementation gate.

- `session.delete.requested` atomically increments a durable deletion
  generation, marks `delete_quiescing`, revokes admission/enrollment renewal,
  fences new intents, capture/reconnect, report/AI/export work, and all new
  content writes, and sends companion stop plus privacy zeroization. An exact
  or honest unknown-end deletion gap is retained only until scoped content is
  purged, subject to the prerequisite terminal-release amendment.
- Prepared-but-not-invoking intents become deletion gaps without provider
  effect. Invoking intents retain only their exact old owner/fence lane and may
  journal the already-issued effect or become an ambiguous-effect gap after
  positive owner/stream quiescence; they are never replayed or taken over.
  Forwarded attempts are cancelled/half-closed and receive only their bounded
  drain. Any transcript callback after the deletion generation begins is
  rejected before content persistence; only a content-free late-callback
  counter may advance.
- Every worker/attempt/connection registered for the session must durably
  acknowledge the same deletion generation and quiescent state. A crash uses
  the deployment/runtime epoch plus a verified egress fence and closed provider
  stream, not heartbeat expiry alone. Until all effect owners are quiescent,
  the state remains `delete_quiescing` and no absence claim begins.
- After the quiescence barrier, `session.deleting` runs one resumable,
  generation-fenced inventory. It rejects capture, reconnect, provider calls,
  report generation, export, and every content or stale-generation write.
- G3A must delete and independently verify absence across session records,
  subcollections, enrollment/lease records, retry keys/commitments, forwarding
  intents/journals, transcript segments, coverage/gap outcomes, blobs, caches,
  outboxes, logs/crash/support artifacts, indexes, exports, provider-enrichment
  records, provider processing/log-retention surfaces, and backups within the
  approved scope. A provider configuration or contract that retains scoped
  content without a verified deletion/expiry outcome blocks terminal success.
  Backup expiry/restoration suppression is an explicit store result, not an
  assumed absence.
- `session.deleted` and the content-free audit tombstone occur only after every
  store confirms absence, every late callback is fenced, and an independent
  second inventory after the quiescence barrier also finds no scoped content.
  The tombstone retains the deletion generation and non-content store results
  so all stale callbacks fail before recreation. It remains candidate-
  associated pseudonymous metadata under tenant ACLs, encryption, access
  audit, and a separately approved retention period; it is never an analytics
  or support-export identifier. A missing item is idempotent success; an
  unavailable, failed, or unverified store is not.
- `session.deletion_failed` remains non-success, fenced, resumable, and visible
  to the owner without revealing another tenant's resource existence.

G2's simulator must prove state ordering, admission revocation, effect-owner
quiescence, rejection of pre/post-tombstone late callbacks, crash/restart at
each intent/provider/drain/inventory boundary, idempotent resume, two absence
passes, and no success tombstone while an injected store or worker remains.
It does not claim real Firestore, GCS, cache, log, provider, or backup deletion.

## 4. Source-only implementation sequence

Implementation requires a new direct authorization after this plan and its
exact allowed paths are reviewed.

### G2-A0: prerequisite governing-artifact decision

- Before source implementation, obtain the separate owner-approved,
  documentation-only amendment required by Section 3.6 across ADR 0002, ADR
  0003, the native-launch roadmap, and the privacy contract.
- Preserve admission as non-release and forwarding as the only successful
  provider-forwarding release watermark. Define durable discard and local
  emergency/privacy-timeout zeroization only as terminal privacy releases.
- Record the conditional panel review and then obtain renewed architecture and
  security/privacy approval of the corrected exact documentation tree. If it is
  not approved, stop before G2-A rather than implementing a contradictory
  protocol. The G3C product-state/privacy UX reconciliation remains a separate
  docs-only artifact and UI gate.

### G2-A: v2 schema and vectors

- Add a v2 metadata schema and framing specification without changing v1.
- Add canonical frame vectors, invalid-frame vectors, identifier derivations,
  and digest manifests.
- Cover RFC 8785 key ordering/escaping, duplicate keys, non-NFC/lone-surrogate
  strings, non-canonical JSON, extra fields, length mismatch,
  truncated/oversized frames, every numeric boundary/overflow/negative/
  exponent form, invalid UTF-8, digest/HMAC mismatch, retry-content conflict
  across restart, control/audio rate-burst-window overflow, and canonical
  atomic/range/segment coverage identities.

### G2-B: Python reference model and simulator

- Implement v2 parsing, framing, buffer accounting, leases, journal batches,
  durable-before-effect forwarding intents, attempt rotation, reconnect,
  durable discard acknowledgement, disclosure admission, deletion lifecycle,
  health events, and transcript-or-gap terminal projection in memory only.
- Inject crash points before/after provider write, journal creation, lease
  replacement, transcript/gap/discard commit, deletion states, reconnect,
  pause, stop, and finalization.
- Inject two same-fence workers and prove deterministic intent ownership allows
  at most one provider invocation for each exact range.
- Inject owner crash before/after `invoking`, journal failure after the provider
  call, stale-owner resumption, recovery takeover attempts, and delayed
  provider callbacks; prove no second invocation and no post-terminal write.
- Inject an earlier durable gap followed by a later forwarded interval; prove
  acknowledgement and reconnect payloads carry the ordered disjoint interval
  set, never a scalar that crosses the gap, and release only the later proven
  interval.
- Generate canonical terminal and segment identities with two finals in one
  chunk, sparse ranges, duplicate/overlapping atomic lists, non-NFC IDs, and
  length-prefix boundary values; compare Python, Swift, and C# bytes.
- Inject egress-fence publication, runtime-epoch mismatch, provider-close
  acknowledgement loss, owner termination acknowledgement loss, and recovery
  into persistent `effect_quiescence_required`; prove no completion, deletion,
  ambiguous-gap, or replacement-effect claim occurs without the required fence.
- Exercise lease replacement across companion-known/unadmitted,
  admitted/unprepared, prepared/ambiguous, and forwarded/untranscribed ranges,
  including an unknowable tail.
- Exercise multiple finals inside one chunk, one final spanning chunks,
  reordered/duplicate/conflicting finals, partial provider overlap, silence,
  trailing coverage, and transactional atomic-chunk uniqueness distinct from
  transcript-segment identity.

### G2-C: Swift binding and custody model

- Implement only pure framing/types/state reducers under the existing offline
  guard.
- Do not import or invoke ScreenCaptureKit, AVAudioEngine, CoreAudio, Keychain,
  URLSession, Network, filesystem mutation, or executable composition.
- Prove the exact two-source frame/byte/reservation ceilings.
- Prove audio metadata, event count, parsed-object/container, and aggregate
  resident-allocation ceilings under minimum-duration adversarial traffic.

### G2-D: Windows binding vectors

- Add a pure C# binding/vector runner with networking and device APIs absent.
- Prove byte-for-byte frame equivalence and the same validation/rejection
  matrix. It is not a WASAPI implementation.

### G2-E: independent review and freeze

- Run Python, Swift, and C# vectors twice from clean scratch with networking
  denied.
- Run 60-, 90-, and 120-minute simultaneous two-source generated-byte cases.
- Scan the repository and scratch artifacts for payload bytes, credentials,
  project/endpoint selection, logs, and undeclared files.
- Obtain independent staff and security/privacy approval of the exact tree.
- Freeze the exact commit, tree, manifests, commands, test counts, and claim
  ceiling before G3A or G3B branches begin.

G2 source/offline evidence cannot claim physical erasure from allocator,
parser/HMAC/TLS, swap, core/crash, diagnostic, backup, or provider buffers; it
cannot claim provider-side deletion or hosted authentication, distributed
ingress, parser-resource, or tenant-spend safety. G3A/G4 must provide those
exact-environment controls and readback evidence before hosted or pilot audio.

## 5. Proposed allowed paths for G2

G2-A0 is a separate prerequisite documentation-only decision. Its write set is
limited to:

- `docs/architecture/0002-companion-stream-protocol.md`;
- `docs/architecture/0003-native-capture-launch-boundary.md`;
- `docs/plans/2026-08-13-native-capture-launch-roadmap.md`;
- `docs/privacy/data-flow-retention-contract.md`; and
- the plan/review record that binds the amendment decision.

That amendment receives its own exact commit/tree and renewed architecture and
security/privacy review before any source authority may be requested. The later
G2 source implementation plan should permit only:

- this G2 implementation plan and its final source-only evidence record;
- `companion/protocol/README.md`
- `companion/protocol/schema/` v2 additions;
- `companion/protocol/vectors/` v2 additions;
- `companion/protocol/python/` model, simulator, runner, guard, and tests;
- `companion/protocol/swift/` pure binding/model and tests;
- a new `companion/protocol/csharp/` pure binding/vector runner and tests;
- `companion/protocol/scripts/` offline guard and artifact-scan changes; and
- `companion/protocol/sandbox/` only if a reviewed platform-specific sandbox
  profile is required for the new offline runner.

No backend, frontend, native-capture, route, deployment, CI, cloud, credential,
or protected handoff path belongs in the G2 write set.

## 6. Required exit evidence

G2 exits only when one exact source tree proves:

- the prerequisite G2-A0 governing amendment was owner-approved and its exact
  tree received renewed architecture and security/privacy approval;
- canonical v2 frames, RFC 8785 profile, checked numeric domains, HMAC inputs,
  and schema validation agree byte-for-byte across Python, Swift, and C#;
- unauthenticated, expired, revoked, replayed, wrong-audience, wrong-tenant,
  wrong-actor, wrong-enrollment, wrong-session, wrong-stream,
  wrong-capture-generation, stale-fence, conflicting, oversized, out-of-order,
  unsupported-version, and missing, stale, revoked, wrong-notice, or
  wrong-legal-basis disclosure inputs fail closed without revealing resource
  existence;
- two simultaneous 8/48-kHz, mono/stereo, and mixed-rate sources remain within
  the exact two-second duration, frame, raw-byte, metadata, event-count,
  reservation, object, and resident-allocation ceilings for 60, 90, and 120
  minutes, including adversarial minimum-duration and resend traffic;
- audio event/byte rates, bursts, active sessions, provider attempts, and
  resident allocations remain inside every source/session/tenant/process row;
  retries consume quota and all excess fails before queue allocation/mutation;
- admission never releases companion audio;
- forwarding advances only after an immutable content-free journal record;
- no provider effect occurs without one durable prepared/invoking owner, and
  lease replacement waits for terminal state plus positive effect-owner and
  provider-stream quiescence;
- two same-fence workers, a stale resumed owner, and recovery takeover cannot
  invoke the provider more than once for one range;
- a restart validates identical audio through the session-scoped commitment
  and rejects changed bytes for the same event without a cross-session digest;
- a durable exact discard acknowledgement and local emergency/privacy-timeout
  release follow the approved G2-A0 semantics without falsely advancing the
  forwarded watermark or extending raw custody beyond its wall-clock deadline;
- attempt rotation never changes coverage identity or creates overlapping
  terminal outcomes;
- reconnect returns exact resend ranges and rejects stale fences;
- every known atomic captured range reaches exactly one durable transcript-
  coverage or gap claim, while unknowable ends remain explicit;
- multiple finals inside a chunk, one final spanning chunks, reordered/
  duplicate/conflicting finals, partial overlaps, silence, and trailing
  coverage preserve distinct segment identity plus atomic audio non-overlap;
- control and audio bytes, rate, burst, outstanding mutations, streams,
  connections, active sessions, and provider attempts remain inside their
  exact limits and fail closed on excess;
- deletion immediately revokes admission, fences late writes, proves every
  provider/worker/connection quiescent, resumes idempotently after injected
  failure, performs two absence passes, and never reports success while any
  modeled store or stale callback remains;
- pause, stop, finalization, completion, and upgrade rules preserve companion
  physical authority, gateway transport/coverage authority, and conservative
  derived web state;
- no payload, transcript, credential, project selection, endpoint, network
  activity, or unapproved artifact escapes the offline boundary; and
- independent staff and security/privacy reviewers approve the exact tree
  with no unresolved P0-P3 finding.

## 7. Stop conditions

Stop and return to planning if implementation needs:

- a backend, frontend, native-capture, route, cloud, or deployment path;
- a network-enabled test or package fetch not already reviewed and vendored;
- a different storage product or provider semantic;
- more than the frozen memory/message bounds;
- higher audio-rate, session, attempt, or process ceilings;
- intentional duplicate audio across STT attempts;
- admission-based audio release;
- terminal discard or local privacy-timeout release without the approved
  G2-A0 governing-artifact amendment;
- editable or overlapping terminal coverage;
- request-supplied tenancy authority;
- a silent version downgrade; or
- any real, ambient, participant, candidate, or customer data.

## 8. Claim ceiling and next action

Approval of this plan grants no source implementation authority. It only makes
the defined G2 corridor eligible for a later, separately requested and
explicitly approved implementation action against exact allowed paths, after
the G2-A0 governing-artifact decision passes its own owner and review gate.
Passing a later G2 implementation would establish protocol closure under
offline generated-byte tests. It would not prove a gateway deployment,
Firestore transaction rate, provider delivery, macOS/Windows capture,
permissions, packaging, physical devices, hosted integration, pilot behavior,
or launch readiness.

After G2 passes, open independent G3A gateway and G3B macOS plans against the
same frozen v2 artifact. The PR #8 and N11D-C source worktrees remain unchanged
until those focused plans explicitly name any behavior or clean committed file
to reuse.
