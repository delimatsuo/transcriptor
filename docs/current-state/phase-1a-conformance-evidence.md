# Phase 1A Offline Conformance Evidence

**Recorded:** 2026-07-15T21:00:31Z

**Status:** Passed. Phase 1A offline protocol conformance is complete at verified implementation tip `9f3f3a0`; Phases 1B-1D remain blocked behind their separate evidence and authorization gates.

**Authorization:** Phase 1A offline protocol conformance only. This does not authorize Phase 1B-1D, push, merge, deployment, cloud/provider access, native capture, ambient or human audio, real data, or legacy-data mutation.

**Implementation owner:** Codex, under the user's explicit Phase 1A authorization

**Approval owner:** User

**Model commit:** `76d28dc2b4a1edb1586f1a2f9ff115bc46145d55`

**Simulator commit:** `61104250efb5b5c4b1770904cf932c3542ed17a6`

**Cross-language commit:** `915f16cc3213739ec47f53e716f684862eeb5436`

**Long-duration commit:** `eaed0e62c4b445783a709fc98b84c64f610e91bf`

**Verified implementation tip:** `9f3f3a08db7c77401fab8e6c2272041f589aa183`

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

The cross-language slice adds:

- one committed content-free vector file consumed by both Python and Swift;
- Swift protocol identities and typed audio/known-coverage/unknown-coverage/terminal metadata bindings with the same bounds and fail-closed validation as Python;
- schema-versus-binding field checks and deterministic round-trip tests in both languages;
- exact monotonic start/end coverage fields required by the governing protocol; time fields do not alter attempt-independent coverage IDs;
- a SwiftPM package with no external package dependencies;
- Swift builds and tests under the same outer network-denying Seatbelt profile, with isolated `/tmp` home, module cache, temp, and build directories removed after every run.

## 2. Verification results

Command:

```sh
companion/protocol/scripts/run_offline_guard.sh
```

After the model commit, both executions passed all 29 tests. After the simulator commit, the wrapper again executed the full suite twice under the network-denying Seatbelt profile and compared the summaries. Both executions passed all 49 tests:

```json
{"errors":0,"failures":0,"phase":"1A-guard","successful":true,"testsRun":49}
```

After cross-language commit `915f16c`, the wrapper ran Python and Swift twice each and compared each language's deterministic summary:

```json
{"phase":"1A-guard","python":{"errors":0,"failures":0,"phase":"1A-guard","successful":true,"testsRun":53},"successful":true,"swift":{"successful":true,"testsRun":4}}
```

SwiftPM's own nested sandbox is disabled because macOS rejects nesting it inside the outer Seatbelt process. The outer reviewed `(deny network*)` sandbox remains active around the Swift compiler and test process. This does not relax the Phase 1A network boundary.

At verified implementation tip `9f3f3a0`, the final wrapper run passed 54 Python and 4 Swift tests twice:

```json
{"phase":"1A-guard","python":{"errors":0,"failures":0,"phase":"1A-guard","successful":true,"testsRun":54},"successful":true,"swift":{"successful":true,"testsRun":4}}
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

The cross-language tests additionally verify:

- Swift and Python produce identical coverage, audio-event, transcript-terminal, and unknown-gap IDs from one shared vector;
- typed audio and terminal metadata contain every required schema field and no undeclared field;
- malformed identifiers, reversed time coverage, and tampered event IDs fail closed in Swift;
- encoded Swift terminal metadata round-trips deterministically and remains inside the control-message bound;
- known coverage carries exact monotonic start/end times and unknown coverage carries an honest start time without a fabricated end.

The long-duration test executes, per guarded Python run:

| Source | Logical duration | Chunks | Forwarding/terminal batches | Peak client raw bytes | Peak gateway raw bytes | Final raw bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Microphone | 60 minutes | 36,000 | 1,800 | 64,000 | 64,000 | 0 |
| System audio | 90 minutes | 54,000 | 2,700 | 64,000 | 64,000 | 0 |

Both runs include periodic reconnect negotiation and STT-attempt rotation. Their final admitted, forwarded, and durable-transcript sequence/sample/time watermarks equal the complete logical source range.

## 3. Artifact and scope checks

- `git diff --check`: passed before commit.
- Raw-audio extensions under `companion/protocol/`: `0`.
- `__pycache__` and `.pyc` artifacts under `companion/protocol/`: `0`.
- Production/cloud/network-client imports in the implementation: `0`.
- Provider calls, network access, filesystem payload writes, native capture, and production imports: none.
- Swift repository build/cache directories after the run: `0`; all compiler output used deleted `/tmp` scratch directories.
- Swift external package dependencies and `Package.resolved`: `0`.
- Final reproducible artifact/scope scan:

```json
{"artifacts":0,"forbiddenImports":0,"outOfScopePaths":0,"phase":"1A-artifact-scan","successful":true}
```

- Changes outside `.gitignore`, `README.md`, `docs/`, and `companion/protocol/` since the reviewed guard base: `0`.
- Stop conditions encountered: none.

## 4. Gate result and next boundary

Phase 1A passed its named exit criteria:

- networking and credential/environment escape attempts fail closed;
- Python and Swift validate one tracked schema and shared vector set;
- deterministic identities, bounds, fencing, retries, reconnect, crash points, overflow, exact and unknown-end gaps, and terminal uniqueness pass;
- 60/90-minute logical runs remain inside the approved queue bounds and finish with zero raw bytes;
- final repository artifact and scope scans pass.

This is an offline protocol result only. It does not authorize a branch push, merge, deployment, cloud/provider access, hosted gateway, native capture, ambient or human audio, real data, legacy mutation, or any Phase 1B-1D activity. Phase 1B may begin only after its threat model, exact-project/runtime controls, lower quotas, least privilege, kill switch, fresh containment evidence, review, and separate user authorization are complete.
