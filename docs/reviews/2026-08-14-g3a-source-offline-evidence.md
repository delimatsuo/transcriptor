# G3A Source-Only Offline Evidence

Status: source/offline evidence candidate. This record does not claim hosted
gateway behavior, provider delivery/deletion, device capture, physical
erasure, deployment, pilot, launch, or production readiness.

## Exact binding

- G2-A merged parent: `65051a8863ec9b3430318b63b091369d668cd1b0`;
- G2-A tree: `b5e0358b286e5f71e731f17653acffaf78aaeebd`;
- G3A source remediation commit: `e4668ee6e162bbe4540f4c2bc28ebcad1cf605eb`;
- G3A source remediation tree: `115b8941df5a119723a65e9147696a3de96d47ed`;
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
- bounded admission, source/session quotas, pending handshakes, receive/resident
  ceilings, typed retry identity, and contiguous forwarding journals;
- an opaque provider-effect seam with prepared/invoking/forwarded/ambiguous/
  discard/quiescence states, but no provider client or invocation;
- ordered disjoint coverage, durable transcript-or-gap terminalization, multiple
  overlapping finals within one atomic range, and independent gateway state;
- deletion generation/barrier fencing, positive worker/effect quiescence,
  explicit effect quiescence before deletion, late-callback rejection,
  idempotent recovery, two absence passes, and retryable non-success states; and
- content-free diagnostics, kill switch, rollback state, offline guard, and
  artifact scan.

## Verification

- deterministic G3A tests: **25 passed**;
- `run_g3a_offline_guard.sh`: passed, including the same 25 tests and forbidden
  import/path checks;
- `run_g3a_artifact_scan.sh`: passed with **zero artifacts, forbidden imports,
  and out-of-scope paths**;
- `git diff --check 65051a8..HEAD`: passed;
- no network, provider, cloud, credential, device, capture, or real-data
  action was performed; and
- no generated payload, credential, endpoint, project, package-resolution,
  `__pycache__`, or build artifact remains in the corridor.

The remediation specifically closes source identity/per-source quota binding,
lease validation before duplicate retry acceptance, pre-prepare discard CAS and
in-flight effect fencing, multiple provider-final accumulation with durable
missing-range gaps, and deletion's explicit positive effect-quiescence gate.

The local pytest installation emitted only its existing asyncio fixture-scope
deprecation warning; it did not affect test results or source behavior.

## Claim ceiling and remaining gate

This evidence supports only deterministic source/offline protocol behavior in
the exact G3A corridor. It does not prove FastAPI routing, hosted auth or
tenancy, distributed ingress, cloud quota/spend controls, provider behavior,
provider retention/deletion, native capture, physical erasure, deployment,
pilot, or launch readiness.

The remaining gate is fresh exact-tree staff and security/privacy review of the
remediated source and this record before merge. The final published commit/tree
binding is reported in the PR metadata after the documentation checkpoint.
Any later source or relevant configuration correction expires this record and
requires a new exact binding.
