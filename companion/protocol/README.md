# Phase 1A Offline Protocol Boundary

This directory is isolated Phase 1A work. It is not imported by the current backend or frontend and is not part of any deployment path.

The guard-first slice establishes the execution boundary for all protocol state-machine and conformance implementation:

- `scripts/run_offline_guard.sh` clears the inherited environment and runs the standard-library test process under the macOS Seatbelt profile in `sandbox/phase1a-offline.sb`.
- The Seatbelt profile denies every network operation.
- Python guards reject credential-, project-, endpoint-, token-, and secret-shaped environment variables; production/cloud imports; subprocess execution; and filesystem mutation.
- `fixtures/phase1a-v1.manifest.json` is the only byte-fixture input. Fixtures are generated and verified in memory.
- `schema/protocol-v1.schema.json` is the tracked canonical v1 schema boundary. It contains metadata only; audio payload bytes are transported separately and never represented in JSON.

The Python binding and terminal-coverage model is implemented in `python/tars_phase1a/model.py`; the deterministic in-memory provider, reconnect, fencing, and crash oracle is in `python/tars_phase1a/simulator.py`. Swift bindings live in `swift/` and share `vectors/protocol-v1-vectors.json` with Python. All vectors, bounds, retry, ordering, release, recovery, schema-binding, and terminal-uniqueness tests run only through the guarded wrapper. Long-duration and final artifact gates remain in progress.

## Canonical coverage identity

For a known multi-chunk range, encode these values as UTF-8 fields separated by one NUL byte, in this exact order:

1. literal `tars-coverage-v1`;
2. `sessionId`;
3. `streamId`;
4. base-10 `captureGeneration` without leading zeroes;
5. `source`;
6. base-10 `firstSequence`;
7. base-10 `lastSequenceInclusive`;
8. base-10 `firstSample`;
9. base-10 `lastSampleExclusive`.

`coverageId` is `cov_` followed by the lowercase hexadecimal SHA-256 digest of that byte string. Identifiers may not contain NUL bytes.

Sequence ranges are inclusive. Sample ranges are half-open. A multi-chunk coverage range must contain contiguous sequences and non-overlapping, contiguous sample ranges for one session, stream, capture generation, and source.

## Terminal outcomes and retry

- Transport is at least once. Retrying an event reuses the same `eventId`, payload digest, and coverage identity.
- A known coverage range has exactly one terminal outcome: durable transcript coverage or durable gap coverage.
- Terminal outcomes for the same session, stream, capture generation, and source may not overlap.
- An unknown-end gap is terminal from its known start until a separately versioned reconciliation proves the end. No exact outcome may be inserted into that unresolved interval.
- STT attempt generation is provenance and never changes `coverageId`.

## Initial message bounds

- Encoded JSON control/event metadata: at most 65,536 bytes.
- Audio payload per chunk: at most 64,000 bytes.
- Audio duration per chunk: 20 through 1,000 milliseconds.
- IDs: 1 through 128 ASCII characters matching the schema pattern.
- Transcript or note content is outside the guard-first slice and is not accepted by its runner.

Changing coverage identity, terminal uniqueness, retry identity, or raw-audio release semantics requires an architecture review. Phase 1B-1D, provider calls, native capture, real data, push, and deployment remain out of scope.
