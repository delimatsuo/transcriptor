# T.A.R.S. / Transcriptor

T.A.R.S. is an executive-search interview companion. The target product uses a native macOS companion to capture microphone and system audio, streams transient audio to Google Cloud Speech-to-Text, lets recruiters take timestamped notes, and produces evidence-grounded assessments.

## Current execution status

**Architecture direction:** accepted for planning.

**Phase 1 implementation:** Phase 1A offline-only work is explicitly authorized. The hardened guard tip `9ea9580` passed review; protocol/state-machine conformance may proceed inside the clean worktree. Phases 1B-1D remain blocked.

**Documentation reconciliation:** complete for project-owned documents and conflicting active configuration comments; generated and third-party dependency documentation is classified but not rewritten.

**Deployment:** not authorized. On 2026-07-15 the remote deploy workflow, GitHub Workload Identity provider, and deploy service account were disabled; the Cloud Run public invoker binding was removed. The checked-in workflow is now manual build-only with no deployment job.

**Data:** do not use real candidate or customer data. The legacy project contains 16 session records and four private PDFs that were preserved during containment. Their retention/deletion basis is unresolved. The current prototype still writes local FLAC audio and lacks application-layer ownership enforcement.

**Development environment:** `transcriptor-dev-20260715` is billed, labeled synthetic-only, and configured with an empty private Firestore database, private GCS bucket, empty secret container, disabled runtime identity, disabled STT data logging, and disabled Vertex cache. It has no hosted endpoint and is deliberately inactive.

Immediate public/deployment exposure is contained. The repository and GitHub approval boundaries are established, and Phase 0B is complete. The re-review found no P0 blocker and cleared only offline protocol conformance after a fresh baseline/preflight and explicit user authorization. See the [containment evidence](docs/current-state/phase-0b-containment-evidence.md).

## Current prototype versus target product

| Area | Current prototype | Approved target direction |
| --- | --- | --- |
| Capture | Python `sounddevice`, default mic, and BlackHole | Thin signed macOS companion using native capture APIs; virtual device only as fallback |
| Audio retention | Local FLAC backup is always written | No persistent raw audio by default; bounded memory only |
| STT | Google Cloud STT | Google Cloud STT remains the first-release provider |
| Backend | Local capture and cloud orchestration are coupled | Authenticated cloud control/intelligence plane; capture remains on device |
| UI | Next.js local web workspace | Reuse Next.js initially, paired with the native companion |
| Speaker names | Source labels plus experimental Meet extension | Provider-independent self/remote baseline; adapters remain optional |
| Auth and ownership | Not enforced by the application | Server-derived user/organization ownership on every operation |
| Retention/deletion | Config value exists but is not enforced | Enforced, auditable, user-visible retention and deletion |
| Hiring assessment | AI-generated ratings and recommendation | Evidence-linked human decision support with explicit approval |

## Canonical documentation hierarchy

When documents appear to disagree, use this order:

1. **Execution status and document classification:** [documentation and configuration status](docs/current-state/documentation-and-config-status.md)
2. **Live containment evidence:** [Phase 0B containment evidence](docs/current-state/phase-0b-containment-evidence.md)
3. **Phase 1A baseline evidence:** [baseline and preflight readback](docs/current-state/phase-1a-baseline-preflight-evidence.md)
4. **Phase 1A guard evidence:** [guard-first implementation readback](docs/current-state/phase-1a-guard-evidence.md)
5. **Governing product and architecture plan:** [native companion and note-first interviews](docs/plans/2026-07-15-native-companion-and-note-first-interviews.md)
6. **Accepted architecture direction:** [ADR 0001](docs/architecture/0001-native-companion-cloud-stt.md)
7. **Normative protocol target:** [companion streaming protocol](docs/architecture/0002-companion-stream-protocol.md)
8. **Normative privacy target:** [data-flow, retention, and deletion contract](docs/privacy/data-flow-retention-contract.md)
9. **Normative product behavior:** [companion and web state contract](docs/product/companion-web-state-contract.md)
10. **Phase-specific execution plans:** [Phase 1 native capture spike](docs/plans/2026-07-15-phase-1-native-capture-spike.md)
11. **Review decisions:** [initial 2026-07-15 panel review](docs/reviews/2026-07-15-native-companion-panel-review.md), [Phase 1 gate re-review](docs/reviews/2026-07-15-phase-1-gate-panel-review.md), and [Phase 1A guard review](docs/reviews/2026-07-15-phase-1a-guard-review.md)
12. **Historical records:** `DEPLOY-SETUP.md` and similar dated setup logs describe what was attempted at that time; they are not current authorization or target architecture.
13. **Active configuration and source:** workflows, manifests, rules, Dockerfiles, and code describe current behavior. They do not override an explicit safety gate or authorize deployment.

`AGENTS.md` is generated agent-memory context. It is non-normative and must not be used as product, privacy, security, deployment, or release documentation.

## Next gated activity

Phase 0B containment is complete. The user explicitly authorized Phase 1A offline work, and hardened guard tip `9ea95803e92ae740e6078903b2665cf604e1db09` passed review after its deterministic negative suite and artifact checks.

The next activity is Python coverage and terminal-outcome conformance against deterministic vectors, followed by the offline provider/reconnect/fencing simulator and Swift validation. No Phase 1B-1D permission is implied.

Phase 1B hosted fixtures still requires separate authorization, lower provider quotas, exact-project/runtime attestation, least privilege, reviewed authentication, protected approval, fresh containment readback, and a tested kill switch. Phase 1C offline native fixture capture and Phase 1D integrated synthetic testing also retain their separate gates.

See the [repository preservation inventory](docs/current-state/repository-preservation-inventory.md) for the dirty-work boundary.
