# G3A Source-Only Owner Attestation

This is the sole-owner exact-tree attestation for the G3A source/offline
corridor. It is limited to generated-byte, memory-only protocol behavior and
repository/artifact boundaries.

## Exact reviewed inputs

- merged G2-A parent: `65051a8863ec9b3430318b63b091369d668cd1b0`;
- source implementation commit: `ca397439931f19e7fbaf16f18b7e5e9e56856636`;
- source implementation tree: `5c5126f93fc98a3bd0a57624af18e4712308b77e`;
- evidence commit: `1064fe4c0008e3f33684d555d4b0845cba4165ae`;
- evidence tree: `68865e42ed7cc524fc00d582d600b0d233d64669`;
- branch: `codex/native-g3a-source`;
- worktree: `/private/tmp/transcriptor-native-g3a-source`.

The owner reviewed the exact path set, the new pure gateway namespace, the
negative boundary against legacy backend/capture/provider/cloud paths, the
21-test deterministic suite, the offline guard, and the artifact scan.

## Attestation

No unresolved P0-P3 finding remains within the approved G3A source/offline
scope. The source implementation does not invoke a provider, open a network
socket, load credentials, use a device or capture API, write payload files,
modify a deployment, or consume real audio/candidate data.

The owner accepts the following claim ceiling:

- supported: deterministic generated-byte authority, admission, bounded
  transport, effect fencing, coverage, deletion ordering, recovery, and
  content-free diagnostics in the exact pure-Python corridor;
- not supported: FastAPI/hosted behavior, distributed ingress, cloud quota or
  spend, provider delivery/deletion, native capture, physical erasure,
  deployment, pilot, launch, or production readiness.

The attestation is invalidated by any source, configuration, dependency,
environment, path-set, or protocol-semantic change. A later hosted, provider,
device, capture, deployment, release, or real-data action requires its own
explicit gate and exact evidence.
