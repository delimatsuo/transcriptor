# T.A.R.S. / Transcriptor

T.A.R.S. is an executive-search interview companion. The target product uses a native macOS companion to capture microphone and system audio, streams transient audio to Google Cloud Speech-to-Text, lets recruiters take timestamped notes, and produces evidence-grounded assessments.

## Current execution status

**Architecture direction:** accepted for planning.

**Phase 1 implementation:** Phase 1A offline protocol conformance passed at implementation tip `9f3f3a0`: 54 Python and 4 Swift tests pass twice, including 60/90-minute bounded-memory runs, and the final artifact/scope scan is clean. Phases 1B-1D remain blocked.

**Week 4 branch:** Draft PR #8 adds authenticated internal tenancy, server-derived ownership, short-lived WebSocket/stop capabilities, model-cost guardrails, and runtime performance controls. The exact source/test evidence and remaining hosted/device gates are recorded in [Week 4 evidence](docs/current-state/week-4-auth-evidence.md). This branch is not release-ready or hosted-authorized.

**Documentation reconciliation:** complete for project-owned documents and conflicting active configuration comments; generated and third-party dependency documentation is classified but not rewritten.

**Deployment:** not authorized. On 2026-07-15 the remote deploy workflow, GitHub Workload Identity provider, and deploy service account were disabled; the Cloud Run public invoker binding was removed. The checked-in workflow is now manual build-only with no deployment job.

**Data:** do not use real candidate or customer data. The historical containment inventory listed 16 sessions and four private PDFs; the later owner-authorized purge record reports 19 sessions and four blobs deleted. Current cloud state still requires a fresh authorized readback. The Week 4 branch defaults raw-audio backup off and stamps owner/org on new records, but hosted retention remains unresolved.

**Development environment:** `transcriptor-dev-20260715` is billed, labeled synthetic-only, and configured with an empty private Firestore database, private GCS bucket, empty secret container, disabled runtime identity, disabled STT data logging, and disabled Vertex cache. It has no hosted endpoint and is deliberately inactive.

Immediate public/deployment exposure is contained. The repository and GitHub approval boundaries are established, and Phase 0B is complete. The re-review found no P0 blocker and cleared only offline protocol conformance after a fresh baseline/preflight and explicit user authorization. See the [containment evidence](docs/current-state/phase-0b-containment-evidence.md).

## Current prototype versus target product

| Area | Current prototype | Approved target direction |
| --- | --- | --- |
| Capture | Python `sounddevice`, default mic, and BlackHole | Thin signed macOS companion using native capture APIs; virtual device only as fallback |
| Audio retention | Local FLAC backup is opt-in and disabled by default | No persistent raw audio by default; bounded memory only |
| STT | Google Cloud STT | Google Cloud STT remains the first-release provider |
| Backend | Local capture and cloud orchestration are coupled | Authenticated cloud control/intelligence plane; capture remains on device |
| UI | Next.js local web workspace | Reuse Next.js initially, paired with the native companion |
| Speaker names | Source labels plus experimental Meet extension | Provider-independent self/remote baseline; adapters remain optional |
| Auth and ownership | Week 4 branch: allowlisted Firebase admission and server-derived owner/org checks; hosted isolation is not yet authorized | Hosted, independently reviewed tenant boundary with migration and provider controls |
| Retention/deletion | Config value exists but is not enforced | Enforced, auditable, user-visible retention and deletion |
| Hiring assessment | AI-generated ratings and recommendation | Evidence-linked human decision support with explicit approval |

## Canonical documentation hierarchy

When documents appear to disagree, use this order:

1. **Execution status and document classification:** [documentation and configuration status](docs/current-state/documentation-and-config-status.md)
2. **Live containment evidence:** [Phase 0B containment evidence](docs/current-state/phase-0b-containment-evidence.md)
3. **Phase 1A baseline evidence:** [baseline and preflight readback](docs/current-state/phase-1a-baseline-preflight-evidence.md)
4. **Phase 1A guard evidence:** [guard-first implementation readback](docs/current-state/phase-1a-guard-evidence.md)
5. **Phase 1A conformance evidence:** [incremental protocol-model and simulator readback](docs/current-state/phase-1a-conformance-evidence.md)
6. **Governing product and architecture plan:** [native companion and note-first interviews](docs/plans/2026-07-15-native-companion-and-note-first-interviews.md)
7. **Accepted architecture direction:** [ADR 0001](docs/architecture/0001-native-companion-cloud-stt.md)
8. **Normative protocol target:** [companion streaming protocol](docs/architecture/0002-companion-stream-protocol.md)
9. **Normative privacy target:** [data-flow, retention, and deletion contract](docs/privacy/data-flow-retention-contract.md)
10. **Normative product behavior:** [companion and web state contract](docs/product/companion-web-state-contract.md)
11. **Phase-specific execution plans:** [Phase 1 native capture spike](docs/plans/2026-07-15-phase-1-native-capture-spike.md)
12. **Review decisions:** [initial 2026-07-15 panel review](docs/reviews/2026-07-15-native-companion-panel-review.md), [Phase 1 gate re-review](docs/reviews/2026-07-15-phase-1-gate-panel-review.md), and [Phase 1A guard review](docs/reviews/2026-07-15-phase-1a-guard-review.md)
13. **Historical records:** `DEPLOY-SETUP.md` and similar dated setup logs describe what was attempted at that time; they are not current authorization or target architecture.
14. **Active configuration and source:** workflows, manifests, rules, Dockerfiles, and code describe current behavior. They do not override an explicit safety gate or authorize deployment.

`AGENTS.md` is generated agent-memory context. It is non-normative and must not be used as product, privacy, security, deployment, or release documentation.

## Next gated activity

Phase 0B containment and Phase 1A offline conformance are complete. Week 4 source/test qualification is complete on draft PR #8, but its hosted index, deployment, provider, legacy-data, macOS, and Windows gates remain open. The user explicitly authorized Phase 1A offline work, and the implementation passed its reviewed guard, deterministic Python/Swift, long-duration, and artifact/scope gates.

The next decision is whether to prepare and review the Phase 1B hosted-fixture threat model and activation checklist. No Phase 1B implementation or cloud mutation is authorized: it still requires exact-project/runtime attestation, lower quotas, least privilege, reviewed authentication/ownership, protected approval, fresh containment evidence, a tested kill switch, and separate user authorization. Phase 1C offline native fixture capture and Phase 1D integrated synthetic testing also retain their separate gates.

See the [Week 4 evidence](docs/current-state/week-4-auth-evidence.md) for the exact release-readiness boundary and the [repository preservation inventory](docs/current-state/repository-preservation-inventory.md) for the dirty-work boundary.
