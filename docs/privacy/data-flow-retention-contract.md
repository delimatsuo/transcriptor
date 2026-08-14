# T.A.R.S. Data Flow, Retention, and Deletion Contract

**Status:** Normative target draft with an owner-authorized G2-A0 amendment
candidate dated 2026-08-13. It becomes effective only after this exact
four-document tree receives renewed architecture and security/privacy approval.
The current prototype is non-compliant. Phase 1A offline conformance passed at
`9f3f3a0` under the pre-amendment v1 contract; that result does not qualify the
G2-A0/v2 semantics. Phases 1B-1D remain blocked.

**Date:** 2026-08-13 amendment candidate; original v1 contract date was 2026-07-15.

**Applies to:** Native companion, web workspace, FastAPI services, Firestore, GCS, Google Cloud STT, Vertex AI, provider adapters, logs, and exports

## 1. Purpose

This document defines what T.A.R.S. collects, where each data class travels, what may be persisted, and how deletion is proven. Product copy, implementation, and operational runbooks must agree with this contract.

The primary privacy promise is:

> T.A.R.S. captures audio only while the user has an active transcription session. Raw audio is processed transiently and is not retained by T.A.R.S. as a durable application artifact by default. This is not a claim that allocator copies, TLS/parser buffers, OS swap, crash/core tooling, or provider/network buffers are physically erased; those surfaces require direct hosted/pilot evidence before any such claim. A range is released from raw custody only as provider-forwarded under `audio.forwarded`, as the named local privacy release `local_privacy_discard` (immediate local zeroization followed by an exact/unknown gap when no provider effect is pending, or an original-owner forwarded/ambiguous outcome when one is already pending), as a durably discarded terminal gap under `audio.discard.durable`, or under an emergency/privacy-timeout zeroization rule. `local_privacy_discard`, `audio.discard.durable`, and emergency/privacy-timeout zeroization never claim provider-forwarding success. The transcript, recruiter notes, source documents, and approved assessment remain according to the user's or organization's retention policy.

This promise is not publishable until the verification section passes in the intended production environment.

Any prior candidate disclosure script that says audio is simply “not recorded
or stored” is stale for v2 and is not launch or pilot evidence. G3C must bind
the replacement copy to this bounded claim, the notice version/locale,
legal-basis acknowledgement, refusal path, and the distinction between
T.A.R.S.-controlled durable artifacts and provider or operating-system
retention surfaces before UI or consented audio work.

The current prototype does not satisfy this promise: it writes local FLAC audio, lacks application-layer ownership enforcement, and has no verified retention/deletion implementation. Real candidate or customer data must not be used.

## 2. Data classes

| Data class | Examples | Sensitivity | Default persistence target |
| --- | --- | --- | --- |
| Raw audio | Microphone frames, system-audio frames | Restricted | None; bounded memory only |
| Transcript | Interim and final utterances, timestamps, speakers | Restricted | Final segments only |
| Recruiter input | Notes, bookmarks, concerns, ratings | Restricted | Durable and versioned |
| Candidate documents | CV, JD, extracted text | Restricted | Durable under session retention policy |
| AI context | Transcript excerpts, notes, CV/JD text, prompts | Restricted | No separate application copy unless required for an auditable assessment version |
| Assessment | Competency evidence, ratings, report versions | Restricted | Durable and versioned |
| Identity | User, organization, membership, device enrollment | Confidential | Durable while account or policy requires |
| Operational metadata | Event IDs, timestamps, latency, error codes | Internal | Time-bounded logs and metrics |
| Authentication secrets | Access/refresh tokens, device credentials | Restricted | Keychain or managed secret systems only |
| Provider enrichment | Meeting title, participant names, provider IDs | Confidential | Only if linked to an owned session |

Restricted content must not appear in ordinary application logs, analytics events, crash reports, or error messages.

## 3. End-to-end data flow

### Flow 1: Authentication and enrollment

1. The user signs in through the approved identity provider.
2. The server derives user and organization membership.
3. The companion receives short-lived credentials and stores allowed secrets in Keychain.
4. The server records enrollment and revocation audit events without recording authentication secrets.

### Flow 2: Interview preparation

1. The user provides candidate name, CV, JD, template, and retention choices.
2. Uploads travel over TLS to an authenticated organization-scoped endpoint.
3. Original documents are stored in an organization-scoped object path.
4. Extracted text is stored with the session and document retention policy.
5. Pre-interview analysis sends only the required text to Vertex AI.

### Flow 3: Session start

1. The companion requests an owned session and short-lived audio-stream credentials.
2. The server returns session ID, protocol version, per-source stream IDs, limits, and expiry.
3. The server verifies the applicable consent acknowledgement and disclosure version before audio is accepted.
4. The user sees an active-transcription indicator only after the companion confirms physical capture.

### Flow 4: Device capture

1. The companion captures microphone and system audio as independent sources.
2. Frames receive capture timestamps and monotonic per-source sequence numbers.
3. Frames enter a bounded in-memory queue.
4. No raw-audio file is created by the default path. This bounds T.A.R.S.-
   controlled durable artifacts; it does not by itself prove physical erasure
   from transient allocator, transport, OS, crash, or provider surfaces.

Protocol-v2 custody is at most two seconds per source, independently for the
companion and gateway: `retainedFrames <= min(96,000, 2 * sampleRate)` and
`retainedPayloadBytes <= min(384,000, retainedFrames * channels * 2)`. Thus
8-kHz mono permits 16,000 frames/32,000 bytes and 48-kHz stereo permits 96,000
frames/384,000 bytes; mixed-rate sources retain independent limits. Each source
also caps 100 queued events, 409,600 metadata bytes, 100 reservation objects,
and 100 queue objects; resident allocation includes parsed/container overhead
and is capped at 1,048,576 bytes per source and 2,097,152 bytes per session.
Thirty seconds is only the absolute raw-custody expiry, not the retained-memory
bound. At 10 seconds without forwarding or a terminal release, acquisition stops
and bounded reconciliation begins. The deadline uses a continuous boot clock
where available; an unannounced process suspension can delay execution, so
evidence must not claim physical RAM erasure at the exact wall-clock instant
while the process is frozen.

### Flow 5: Audio streaming and acknowledgement

1. The companion sends ordered audio chunks over an authenticated TLS connection.
2. The gateway validates session ownership, stream identity, limits, and ordering.
3. The gateway forwards transient audio to Google Cloud STT under the approved
   bounded ingress, bitrate, custody, and provider-attempt quotas.
4. A gateway-admission acknowledgement identifies the authoritative ordered
   disjoint `stageIntervals` copied into a bounded gateway queue, plus any
   derived contiguous prefix. It does not permit client audio release.
5. A provider-forwarding acknowledgement identifies an authoritative ordered
   disjoint interval set written to the active STT stream, with any derived
   contiguous prefix explicitly marked as a convenience summary; content-free
   forwarding metadata is durably journaled.
6. The companion releases raw audio from memory through the ordered disjoint
   `audio.forwarded` intervals for that source, or after an explicit local
   discard/zeroization action. A discard CAS that wins before provider
   preparation produces `audio.discard.durable`; an `effect_pending` response
   leaves the original owner to produce a forwarded or ambiguous-effect outcome
   and is never shown as “not sent.” A local emergency kill, deletion command,
   or privacy deadline may zeroize before acknowledgement; recovery records the
   exact or honest unknown-end gap when no effect is pending and never attributes
   a pending effect to discard.
7. Separate `transcript.segment.durable` and `transcript.coverage.durable`
   acknowledgements identify immutable final segments and the complete atomic
   coverage ranges claimed by them.
8. If a queue fills, a forwarded range cannot produce a durable transcript, or
   a terminal privacy release occurs, the system persists and displays the
   proved source/sequence/sample/time gap. If process loss prevents proof of
   the end boundary, it displays unknown coverage rather than fabricated
   precision. It does not silently lose content or spill raw audio to disk.

The complete custody, retry, fencing, and gap semantics are normative in `docs/architecture/0002-companion-stream-protocol.md`.

Protocol-v2 source/offline ingress defaults are 50 audio events/s with a
100-event burst per source, 192,000 payload bytes/s with a 384,000-byte burst,
and 205,000 metadata-plus-prefix bytes/s with a 410,000-byte burst; a
two-source session has the doubled budgets. Pending handshakes, aggregate
receive buffers, resident custody, tenant/process totals, and provider-attempt
reservations are separate ceilings. Retries consume budget, reconnect grants no
new burst, and unavailable shared quota state fails closed. These values are
not hosted capacity or spend evidence.

### Flow 6: Speech recognition

1. Google Cloud STT returns interim and final transcript results.
2. Interim results are broadcast but not persisted as the durable transcript.
3. Final results receive stable, idempotent segment IDs with provider ordinal,
   provenance, text bounds, and intersecting atomic coverage IDs.
4. Final segments are persisted and broadcast to owned clients; a separate
   durable coverage projection claims complete atomic chunks exactly once and
   creates a gap for any forwarded chunk without a valid final.
5. STT data-logging configuration is verified in the exact production project.

### Flow 7: Recruiter notes

1. Notes and bookmarks receive stable IDs and transcript-relative timestamps.
2. The companion or browser may keep encrypted text events in a retry outbox.
3. The server processes retries idempotently.
4. Notes remain distinguishable from AI-generated content.

### Flow 8: Suggestions and assessment

1. The server selects the minimum required transcript, notes, CV/JD, and competency context.
2. Context is sent to Vertex AI under the approved project configuration.
3. Suggestions are ephemeral UI events unless the user pins or incorporates them.
4. Assessment versions persist output, evidence references, prompt/template version, model identifier, and recruiter approval state.

### Flow 9: Stop and completion

1. A stop request does not claim success. The companion confirms the exact boundary at which new audio capture stopped.
2. Remaining admitted or captured frames are forwarded and finalized, or the
   user explicitly discards them as a local privacy action. When the gateway is
   reachable, the discard CAS runs before zeroization; when it is not, discard
   clears unforwarded bytes immediately, records the exact or unknown boundary,
   and sends `audio.discard.requested` best-effort. `audio.discard.durable`
   later confirms gateway gap persistence when the discard CAS wins; an
   `effect_pending` result follows the original-owner forwarded/ambiguous rule.
   An emergency/privacy timeout may likewise zeroize before acknowledgement but
   never attributes a pending effect to discard.
3. In-memory raw audio is cleared.
4. Every captured range is represented by a durable `transcript.coverage.durable`
   event or a durable gap.
5. Final transcript state and assessment job state are durable.
6. Completion does not imply recruiter approval of the assessment.

### Flow 10: Export and sharing

1. Exports require authorization at generation and download time.
2. Export audit events include actor, session, format, and time, but not exported content.
3. Generated temporary export files expire automatically.
4. Sharing is private by default and never inferred from meeting participation.

### Flow 11: Deletion

1. A user or authorized administrator requests session deletion.
2. The session atomically enters non-restorable gateway `delete_quiescing`,
   increments a durable deletion generation, and fences admission/effects. The
   gateway then sends companion stop and the companion performs immediate local
   `local_privacy_discard` zeroization of
   retained unforwarded audio; the resulting exact or unknown boundary is
   preserved as content-free metadata.
3. New reads, exports, AI jobs, capture, reconnect, provider intents, and all
   new content writes are blocked or fenced.
4. Prepared-but-not-invoking provider intents become terminal discard gaps;
   invoking intents remain with their original owner/fence and finish only by
   journaling the already-issued effect or, after positive owner/stream
   quiescence, recording an ambiguous-effect gap. They are never replayed or
   taken over.
5. Every worker, connection, and provider attempt acknowledges the deletion
   generation. Late callbacks fail before content persistence.
6. After the quiescence barrier, the session enters `deleting`; documents,
   extracted text, transcripts, notes, assessments, provider enrichment, and
   temporary exports are deleted.
7. The first generation-fenced inventory covers session records,
   enrollment/lease records, retry commitments, forwarding intents/journals,
   transcript segments, coverage/gaps, blobs, caches, outboxes, logs/crash/
   support artifacts, indexes, exports, provider-enrichment records, provider
   retention surfaces, and backups within approved scope. An unavailable store
   or unverified provider-retention surface produces retryable
   `deletion_failed`; it never counts as absence.
8. Search indexes and derived views are removed, and an independent second
   inventory verifies absence before `deleted`.
9. A content-free audit tombstone records completion and policy basis. The UI
   shows `deleted` only after verified absence; an unavailable store or any
   other retryable failure remains `deletion_failed` and never receives the
   deleted treatment.

### Flow 12: Account or organization deletion

Account and organization deletion enumerate all owned resources and run the same deletion machinery. Completion requires an inventory proving that no owned session artifact remains outside explicitly documented legal or security records.

## 4. Retention matrix

Values marked `TBD` require product, customer, and legal approval before external beta.

| Artifact | Device | T.A.R.S. cloud | Subprocessor | Initial retention rule |
| --- | --- | --- | --- | --- |
| Raw audio | T.A.R.S.-controlled memory only, two-second rate-derived bound/source; 30-second absolute expiry | No T.A.R.S.-controlled durable raw-audio artifact; transient request/stream memory and content-free gap metadata only after terminal release | Governed by verified STT configuration and provider-retention evidence | Client releases after ordered `audio.forwarded` intervals, named `local_privacy_discard`, `audio.discard.durable`, or emergency/privacy-timeout zeroization; only the first is forwarding success; no physical-erasure claim without direct evidence |
| Interim transcript | UI memory | WebSocket/event memory | STT stream lifetime | Do not persist as canonical transcript |
| Final transcript | Optional encrypted text outbox until acknowledged | Durable | Included in selected Vertex prompts | `TBD`; configurable user/org policy |
| Recruiter notes | Optional encrypted text outbox until acknowledged | Durable and versioned | Included in selected Vertex prompts | `TBD`; normally follows session policy |
| CV/JD originals | Temporary upload buffer only | Durable object storage | None unless explicitly processed | `TBD`; configurable and independently deletable |
| Extracted CV/JD text | None after acknowledgement | Durable session/document record | Included in selected Vertex prompts | `TBD`; normally follows source document |
| Suggestions | UI memory unless pinned | No durable copy by default | Vertex request lifecycle/settings | Discard unless incorporated into a note/report |
| Assessment versions | Optional browser cache only | Durable and versioned | Vertex request lifecycle/settings | `TBD`; follows approved report policy |
| Operational logs | Local diagnostic ring without content | Time-bounded | Cloud logging systems | Proposed 30 days; approve before beta |
| Audit events | None | Durable metadata without content | Managed logging/storage | `TBD`; organization and legal policy |
| Temporary exports | User-selected destination | Short-lived generated artifact | None | Automatic expiry after approved short window |

The existing `DATA_RETENTION_DAYS=90` setting is not an enforced policy and must not be treated as evidence of deletion.

## 5. Logging contract

Allowed log fields:

- Randomized session and event identifiers.
- Organization identifier when operationally required and access-controlled.
- Event type, protocol version, sequence range, byte count, duration, latency, status, and error code.
- Model/provider identifier and token/character counts.
- Deletion job state without content.

Forbidden log fields:

- Raw audio or encoded audio chunks.
- Transcript text.
- Recruiter notes.
- CV/JD text or filenames containing candidate names.
- Assessment prose.
- Participant names or email addresses.
- Authentication tokens, extension tokens, authorization headers, or signed URLs.

## 6. Deletion semantics

- Deletion is idempotent and retryable.
- `delete_quiescing` fences new effects and waits for positive provider-effect
  quiescence; lease expiry or heartbeat loss alone is not proof.
- User-visible completion means every required datastore and derived index has confirmed deletion.
- A partial failure remains visible to operations and does not falsely report success.
- Late provider/transcript callbacks are rejected before content persistence by
  deletion generation and fence; only content-free counters may advance.
- Backups, if introduced, require a documented expiry and restoration-time deletion process.
- Audit tombstones contain identifiers, timing, actor, policy, and result only.
- Deleting a transcript must define whether evidence-linked assessments are also deleted or intentionally invalidated; the initial recommendation is to delete the full session bundle.

## 7. Required controls before any hosted native audio

- Disable or gate the active push-triggered deployment workflow and remove unauthenticated service access.
- Inventory and contain current live GitHub, Cloud Run, IAM, Firestore, GCS, Firebase, logging, and stored-data exposure.
- Use an isolated development project, identity, datastore, bucket, secrets, quotas, and provider configuration.
- Verify STT data logging and applicable Vertex AI retention/cache behavior in that exact project.
- Require authenticated, revocable companion enrollment and server-derived user/organization ownership.
- Enforce stream leases, fencing, size/rate/duration/concurrency limits, and the protocol contract.
- Enforce separate pre-authentication, control, audio-event, payload-byte,
  metadata-byte, resident-custody, provider-attempt, and tenant/process quotas;
  fail closed when shared quota state is unavailable.
- Add bounded distributed ingress/connection ceilings, parser CPU/depth limits,
  TLS/token validation, enrollment revocation and rotation, WebSocket origin and
  replay protections, and tenant/user spend ceilings before hosted ingress.
- Require a provider egress fence, runtime-epoch effect ownership, stream
  close/cancel acknowledgement, and an explicit non-success
  `effect_quiescence_required` state before deletion or replacement can claim
  quiescence.
- Pass unauthenticated, revoked, stale-lease, and cross-tenant rejection tests.
- Use synthetic fixtures until these controls and no-persistent-audio tests have direct evidence.

The phased controls are stricter than the final hosted boundary:

- Phase 1A uses only fixed/generated synthetic-byte manifests with a deterministic provider simulator, networking disabled, no credentials or implicit environment lookup, and no candidate/customer fields.
- Phase 1B accepts only server-issued allowlisted fixture manifests and expected chunk digests/ranges in the exact isolated project. A client `synthetic` flag or mutable project label is not authority.
- Phase 1C routes generated fixtures through native APIs into an in-memory/null sink with network/provider access disabled, persistent test-mode labeling, an immediate local kill control, and a stop/discard rule for possible ambient or unrelated-system-audio contamination.
- Phase 1D integrates 1B and 1C only after both pass and after separate authorization. Ambient, consented, or other human speech remains out of scope for the spike.

## 8. Required controls before external beta

- Authenticated REST and WebSocket endpoints.
- User and organization ownership on every stored object.
- Short-lived, revocable companion and extension credentials.
- Rate and size limits for audio, documents, notes, and exports.
- Encryption in transit and managed encryption at rest.
- Verified Google Cloud STT data-logging status.
- Inventoried Vertex AI abuse-monitoring, caching, retention, and region settings.
- Automated retention and deletion jobs.
- Cross-tenant authorization tests.
- Content-safe logs and crash reports.
- Consent acknowledgement, visible capture state, and immediate pause/stop.

## 9. Verification evidence

The privacy promise requires direct evidence, not code inspection alone.

1. Filesystem snapshots before, during, and after success, stop, forced termination, logout, and deletion, including application storage and OS temporary/cache locations.
2. Process-memory and queue-limit instrumentation proving bounds without logging content, plus adversarial scans for allocator/parser/HMAC/TLS copies, swap, core/crash, diagnostic, and temporary artifacts.
3. Network inspection matching documented destinations and payload classes.
4. Queries of Firestore, GCS, indexes, exports, and logs after deletion.
5. Google Cloud configuration evidence for STT data logging and Vertex AI retention/cache behavior.
6. Cross-tenant attempts against every API and WebSocket operation.
7. Failure injection for interrupted deletion jobs.
8. An inventory-driven account deletion test.
9. A signed privacy verification report tied to the exact app build and cloud revision.
10. Unique synthetic canary scans across unified logs, crash reports, core dumps, diagnostic uploads, CI artifacts, Firestore, GCS, and Cloud Logging after success, overflow, disconnect, stop, logout, and forced termination.
11. Network evidence showing no traffic in Phase 1A/1C and only authenticated gateway plus approved gateway-to-STT traffic in Phase 1B/1D, with no credentials in URLs or content in logs.
12. Fault-injection evidence for `local_privacy_discard`,
    `audio.discard.requested` / `audio.discard.durable`, local
    privacy-timeout zeroization, deletion
    quiescence, provider-effect fencing, and late-callback rejection.
13. Exact-environment evidence for TLS/auth/enrollment/revocation/rotation,
    distributed quotas, parser resource limits, tenant spend ceilings, provider
    stream cancellation/egress fencing, and retention/deletion settings across
    logs, caches, backups, and provider surfaces before any hosted or pilot
    claim.

## 10. Open policy decisions

- Default retention period for transcripts, notes, documents, and assessments.
- Whether an organization may require shorter retention than an individual user.
- Whether any future separately approved encrypted raw-audio recovery spool is
  permitted; it is not part of G2-A0 and may not extend the raw-custody deadline.
- Audit-event retention.
- Export expiry window.
- Backup strategy and deletion behavior.
- Regional processing requirements.

These policy values do not prevent documentation or an explicitly authorized Phase 1A offline conformance implementation. They do not override the Phase 1B-1D blocks, hosted/native gate preconditions, or the prohibition on ambient or human audio in this spike. All must be resolved before external beta.
