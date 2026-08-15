# G3A Source-Only Owner Attestation

This is the sole-owner exact-tree attestation for the G3A source/offline
corridor. It is limited to generated-byte, memory-only protocol behavior and
repository/artifact boundaries.

## Exact reviewed inputs

- merged G2-A parent: `65051a8863ec9b3430318b63b091369d668cd1b0`;
- source implementation/remediation commit: `e4668ee6e162bbe4540f4c2bc28ebcad1cf605eb`;
- source implementation/remediation tree: `115b8941df5a119723a65e9147696a3de96d47ed`;
- evidence and attestation records: the final documentation checkpoint on this branch;
- branch: `codex/native-g3a-source`;
- worktree: `/private/tmp/transcriptor-native-g3a-source`.

The owner reviewed the exact path set, the new pure gateway namespace, the
negative boundary against legacy backend/capture/provider/cloud paths, the
25-test deterministic suite, the offline guard, and the artifact scan.

## Attestation

The known source findings from the prior PR review are addressed in the
remediation commit; fresh exact-tree staff and security/privacy review remains
required before merge. The source implementation does not invoke a provider, open a network
socket, load credentials, use a device or capture API, write payload files,
modify a deployment, or consume real audio/candidate data.

The owner accepts the following claim ceiling:

- supported: deterministic generated-byte authority, admission, bounded
  transport, source-bound quotas, effect fencing, coverage, deletion ordering, recovery, and
  content-free diagnostics in the exact pure-Python corridor;
- not supported: FastAPI/hosted behavior, distributed ingress, cloud quota or
  spend, provider delivery/deletion, native capture, physical erasure,
  deployment, pilot, launch, or production readiness.

The attestation is invalidated by any source, configuration, dependency,
environment, path-set, or protocol-semantic change. A later hosted, provider,
device, capture, deployment, release, or real-data action requires its own
explicit gate and exact evidence.
