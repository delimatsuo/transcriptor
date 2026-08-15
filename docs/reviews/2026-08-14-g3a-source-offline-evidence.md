# G3A Source-Only Offline Evidence

Status: source/offline evidence candidate. This record does not claim hosted
gateway behavior, provider delivery/deletion, device capture, physical
erasure, deployment, pilot, launch, or production readiness.

## Exact binding

- G2-A merged parent: `65051a8863ec9b3430318b63b091369d668cd1b0`;
- G2-A tree: `b5e0358b286e5f71e731f17653acffaf78aaeebd`;
- G3A source commit: `ca397439931f19e7fbaf16f18b7e5e9e56856636`;
- G3A source tree: `5c5126f93fc98a3bd0a57624af18e4712308b77e`;
- implementation branch: `codex/native-g3a-source`;
- implementation worktree: `/private/tmp/transcriptor-native-g3a-source`.

The source commit adds only the exact `backend/g3a_gateway/*` implementation,
guard, sandbox, and `backend/tests/g3a_gateway/*` paths named by the approved
G3A authorization package. No existing backend, protocol, frontend,
extension, configuration, dependency, deployment, provider, device, or
protected-worktree path changed.

## Scope and behavior

The corridor is pure Python, memory-only, and generated-byte only. It models:

- server-derived enrollment, disclosure, actor/organization/session/stream
  authority, lease epochs, revocation, and stale-fence rejection;
- bounded admission, per-stream quotas, pending handshakes, receive/resident
  ceilings, retry identity, and contiguous forwarding journals;
- an opaque provider-effect seam with prepared/invoking/forwarded/ambiguous/
  discard/quiescence states, but no provider client or invocation;
- ordered disjoint coverage, transcript-or-gap terminalization, multiple
  finals within one atomic range, and independent gateway state;
- deletion generation/barrier fencing, positive worker/effect quiescence,
  late-callback rejection, idempotent recovery, two absence passes, and
  retryable non-success states; and
- content-free diagnostics, kill switch, rollback state, offline guard, and
  artifact scan.

## Verification

- deterministic G3A tests: **21 passed**;
- `run_g3a_offline_guard.sh`: passed, including the same 21 tests and forbidden
  import/path checks;
- `run_g3a_artifact_scan.sh`: passed with **zero artifacts, forbidden imports,
  and out-of-scope paths**;
- `git diff --check 65051a8..HEAD`: passed;
- no network, provider, cloud, credential, device, capture, or real-data
  action was performed; and
- no generated payload, credential, endpoint, project, package-resolution,
  `__pycache__`, or build artifact remains in the corridor.

The local pytest installation emitted only its existing asyncio fixture-scope
deprecation warning; it did not affect test results or source behavior.

## Claim ceiling and remaining gate

This evidence supports only deterministic source/offline protocol behavior in
the exact G3A corridor. It does not prove FastAPI routing, hosted auth or
tenancy, distributed ingress, cloud quota/spend controls, provider behavior,
provider retention/deletion, native capture, physical erasure, deployment,
pilot, or launch readiness.

The remaining gate is the owner's exact-tree architecture/security/privacy,
scope, and claim-ceiling attestation for the enclosing evidence and source
commits. Any source or relevant configuration correction expires this record
and requires a new exact binding.
