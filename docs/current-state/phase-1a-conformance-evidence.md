# Phase 1A Offline Conformance Evidence

**Recorded:** 2026-07-15T20:28:31Z

**Status:** In progress. The canonical Python protocol model plus deterministic provider/reconnect/fencing simulator are implemented and verified; Swift, long-duration, and final artifact conformance remain.

**Authorization:** Phase 1A offline protocol conformance only. This does not authorize Phase 1B-1D, push, merge, deployment, cloud/provider access, native capture, ambient or human audio, real data, or legacy-data mutation.

**Implementation owner:** Codex, under the user's explicit Phase 1A authorization

**Approval owner:** User

**Model commit:** `76d28dc2b4a1edb1586f1a2f9ff115bc46145d55`

**Simulator commit:** `61104250efb5b5c4b1770904cf932c3542ed17a6`

**Reviewed guard tip:** `9ea95803e92ae740e6078903b2665cf604e1db09`

**Branch/worktree:** `codex/native-companion-phase1` in `/Volumes/Extreme Pro/myprojects/Transcriptor-worktrees/native-companion-phase1`

## 1. Completed conformance slice

- Added strict Python bindings for stream keys, chunks, known and unknown-end coverage, terminal outcomes, deterministic event IDs, and attempt-independent terminal IDs.
- Added retry and terminal ledgers that reject event-ID payload changes, overlapping sequence/sample coverage, reversed ordering, and outcomes inside an unresolved unknown-end interval.
- Enforced integer types, identifier syntax, PCM framing, sample/duration alignment, message bounds, source separation, and timezone-bearing capture timestamps.
- Corrected the fixture description to identify 3,200 bytes of 16 kHz mono PCM as 100 milliseconds.
- Reconciled the canonical terminal schema with the normative contract: terminal metadata now explicitly requires `source` and `resultOrdinal`; gaps require a reason and transcripts prohibit one.
- Kept payload bytes in memory and outside metadata. No transcript, note, participant, candidate, customer, credential, or provider field was introduced.

The simulator slice adds:

- active fake-principal ownership checks with the same non-enumerating rejection for unauthenticated, expired, revoked, cross-organization, and cross-user input;
- per-source capture and STT-attempt fencing, independent sequence/sample/time watermarks, and bounded client/gateway queues;
- distinct admission, journaled provider-forwarding, and durable-transcript stages, with raw bytes released only by journaled forwarding;
- exact reconnect resend ranges and authoritative watermark comparison;
- deterministic recovery before provider write, after provider write but before journal, after journal but before transcript, and after terminal commit;
- exact `unknown_forwarding_state`, `stt_stream_failed`, and `buffer_overflow` gaps plus honest `unknown_end` process-termination coverage;
- metadata-only forwarding journals and diagnostics without fixture digests or content fields.

## 2. Verification results

Command:

```sh
companion/protocol/scripts/run_offline_guard.sh
```

After the model commit, both executions passed all 29 tests. After the simulator commit, the wrapper again executed the full suite twice under the network-denying Seatbelt profile and compared the summaries. Both executions passed all 49 tests:

```json
{"errors":0,"failures":0,"phase":"1A-guard","successful":true,"testsRun":49}
```

The new model tests directly verify:

- independent fixed vectors for coverage, audio-event, transcript-terminal, and unknown-end-gap identities;
- STT attempt rotation cannot change a terminal event ID;
- retries are idempotent and payload changes under a reused event ID fail closed;
- transcript and gap terminal ranges cannot overlap in sequence or sample space;
- sequence order and sample order cannot disagree;
- an unknown-end gap blocks every later exact outcome while allowing a strict prefix;
- bound, type, timestamp, identifier, PCM-frame, duration, and schema-field failures are rejected;
- encoded metadata contains no raw payload or transcript content.

The simulator tests additionally verify:

- admission cannot release client audio, while journaled contiguous forwarding does;
- fake identity/ownership and stale capture or STT fences fail closed;
- microphone and system-audio counters and watermarks remain independent;
- retry payload changes, out-of-order capture, non-contiguous admission, and queue excess are rejected;
- reconnect returns the exact unforwarded range and rejects client watermarks ahead of or inconsistent with authoritative sequence/sample/time state;
- crash recovery produces one stable non-overlapping transcript or gap outcome at each named crash point;
- attempt rotation cannot duplicate or rename a committed terminal event;
- an unknowable forced-termination boundary is recorded as `unknown_end` instead of fabricated precision;
- logs and forwarding records contain counts/ranges only and no fixture digest or content field.

## 3. Artifact and scope checks

- `git diff --check`: passed before commit.
- Raw-audio extensions under `companion/protocol/`: `0`.
- `__pycache__` and `.pyc` artifacts under `companion/protocol/`: `0`.
- Production/cloud/network-client imports in the implementation: `0`.
- Provider calls, network access, filesystem payload writes, native capture, and production imports: none.
- Stop conditions encountered: none.

## 4. Remaining Phase 1A work

1. Swift binding validation against shared deterministic vectors.
2. 60- and 90-minute bounded-memory/determinism tests plus final artifact and scope scans.

Phase 1A is not complete until the remaining work passes and this record is tied to the final reviewed commit. Later phases remain blocked behind their separate gates.
