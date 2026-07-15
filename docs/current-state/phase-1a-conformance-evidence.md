# Phase 1A Offline Conformance Evidence

**Recorded:** 2026-07-15T20:28:31Z

**Status:** In progress. The canonical Python protocol model and terminal-coverage invariants are implemented and verified; deterministic provider, reconnect, fencing, Swift, long-duration, and final artifact conformance remain.

**Authorization:** Phase 1A offline protocol conformance only. This does not authorize Phase 1B-1D, push, merge, deployment, cloud/provider access, native capture, ambient or human audio, real data, or legacy-data mutation.

**Implementation owner:** Codex, under the user's explicit Phase 1A authorization

**Approval owner:** User

**Model commit:** `76d28dc2b4a1edb1586f1a2f9ff115bc46145d55`

**Reviewed guard tip:** `9ea95803e92ae740e6078903b2665cf604e1db09`

**Branch/worktree:** `codex/native-companion-phase1` in `/Volumes/Extreme Pro/myprojects/Transcriptor-worktrees/native-companion-phase1`

## 1. Completed conformance slice

- Added strict Python bindings for stream keys, chunks, known and unknown-end coverage, terminal outcomes, deterministic event IDs, and attempt-independent terminal IDs.
- Added retry and terminal ledgers that reject event-ID payload changes, overlapping sequence/sample coverage, reversed ordering, and outcomes inside an unresolved unknown-end interval.
- Enforced integer types, identifier syntax, PCM framing, sample/duration alignment, message bounds, source separation, and timezone-bearing capture timestamps.
- Corrected the fixture description to identify 3,200 bytes of 16 kHz mono PCM as 100 milliseconds.
- Reconciled the canonical terminal schema with the normative contract: terminal metadata now explicitly requires `source` and `resultOrdinal`; gaps require a reason and transcripts prohibit one.
- Kept payload bytes in memory and outside metadata. No transcript, note, participant, candidate, customer, credential, or provider field was introduced.

## 2. Verification results

Command:

```sh
companion/protocol/scripts/run_offline_guard.sh
```

The wrapper executed the full suite twice under the network-denying Seatbelt profile and compared the summaries. Both executions passed all 29 tests:

```json
{"errors":0,"failures":0,"phase":"1A-guard","successful":true,"testsRun":29}
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

## 3. Artifact and scope checks

- `git diff --check`: passed before commit.
- Raw-audio extensions under `companion/protocol/`: `0`.
- `__pycache__` and `.pyc` artifacts under `companion/protocol/`: `0`.
- Production/cloud/network-client imports in the implementation: `0`.
- Provider calls, network access, filesystem payload writes, native capture, and production imports: none.
- Stop conditions encountered: none.

## 4. Remaining Phase 1A work

1. Deterministic provider simulation with admission, forwarding-journal, release, and durable-transcript watermarks.
2. Reconnect negotiation, exact resend ranges, lease fencing, queue overflow, crash points, and STT-attempt rotation.
3. Swift binding validation against shared deterministic vectors.
4. 60- and 90-minute bounded-memory/determinism tests plus final artifact and scope scans.

Phase 1A is not complete until the remaining work passes and this record is tied to the final reviewed commit. Later phases remain blocked behind their separate gates.
