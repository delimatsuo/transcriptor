# T.A.R.S. / Transcriptor

T.A.R.S. is an executive-search interview companion. The target product uses
native macOS and Windows companions to capture microphone and system audio,
streams transient audio to Google Cloud Speech-to-Text, lets recruiters take
timestamped notes, and produces evidence-grounded assessments. Qualification
begins with a limited native macOS pilot; broad launch requires both platforms.

## Current execution status

**Architecture direction:** accepted for planning. ADR 0003 now makes native
operating-system capture and the authenticated gateway the launch boundary;
virtual audio devices are development-only legacy tooling, not a supported
release path.

**Phase 1 implementation:** Phase 1A offline protocol conformance passed at implementation tip `9f3f3a0`: 54 Python and 4 Swift tests pass twice, including 60/90-minute bounded-memory runs, and the final artifact/scope scan is clean. The authenticated gateway does not exist, the active native-capture lineage contains unqualified dirty N11D-C work, and native Windows capture has not started. These are separate source, hosted, device, and integration gates; none is launch evidence.

**Documentation reconciliation:** complete for project-owned documents and conflicting active configuration comments; generated and third-party dependency documentation is classified but not rewritten.

**Deployment:** not authorized. On 2026-07-15 the remote deploy workflow, GitHub Workload Identity provider, and deploy service account were disabled; the Cloud Run public invoker binding was removed. The checked-in workflow is now manual build-only with no deployment job.

**Data:** do not use real candidate or customer data. The legacy project contains 16 session records and four private PDFs that were preserved during containment. Their retention/deletion basis is unresolved. The current prototype still writes local FLAC audio and lacks application-layer ownership enforcement.

**Development environment:** `transcriptor-dev-20260715` is billed, labeled synthetic-only, and configured with an empty private Firestore database, private GCS bucket, empty secret container, disabled runtime identity, disabled STT data logging, and disabled Vertex cache. It has no hosted endpoint and is deliberately inactive.

Immediate public/deployment exposure is contained. The repository and GitHub approval boundaries are established, and Phase 0B is complete. The re-review found no P0 blocker and cleared only offline protocol conformance after a fresh baseline/preflight and explicit user authorization. See the [containment evidence](docs/current-state/phase-0b-containment-evidence.md).

## Current prototype versus target product

| Area | Current prototype | Approved target direction |
| --- | --- | --- |
| Capture | Python `sounddevice`, default mic, and BlackHole | Native macOS and Windows companions; virtual-device code is an isolated development harness only and is removed after native parity |
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
5. **Phase 1A conformance evidence:** [incremental protocol-model and simulator readback](docs/current-state/phase-1a-conformance-evidence.md)
6. **Current launch boundary:** [ADR 0003](docs/architecture/0003-native-capture-launch-boundary.md)
7. **Current gated sequencing:** [native-capture launch roadmap](docs/plans/2026-08-13-native-capture-launch-roadmap.md)
8. **Governing product and architecture plan:** [native companion and note-first interviews](docs/plans/2026-07-15-native-companion-and-note-first-interviews.md)
9. **Accepted architecture direction:** [ADR 0001](docs/architecture/0001-native-companion-cloud-stt.md)
10. **Normative protocol target:** [companion streaming protocol](docs/architecture/0002-companion-stream-protocol.md)
11. **Normative privacy target:** [data-flow, retention, and deletion contract](docs/privacy/data-flow-retention-contract.md)
12. **Normative product behavior:** [companion and web state contract](docs/product/companion-web-state-contract.md)
13. **Phase-specific execution plans:** [Phase 1 native capture spike](docs/plans/2026-07-15-phase-1-native-capture-spike.md)
14. **Review decisions:** [initial 2026-07-15 panel review](docs/reviews/2026-07-15-native-companion-panel-review.md), [Phase 1 gate re-review](docs/reviews/2026-07-15-phase-1-gate-panel-review.md), and [Phase 1A guard review](docs/reviews/2026-07-15-phase-1a-guard-review.md)
15. **Historical records:** the [superseded 2026-08-03 interim launch scope](docs/superpowers/specs/2026-08-03-launch-vision-and-scope-design.md), `DEPLOY-SETUP.md`, and similar dated setup logs describe what was attempted or planned at that time; they are not current authorization or target launch sequencing.
16. **Active configuration and source:** workflows, manifests, rules, Dockerfiles, and code describe current behavior. They do not override an explicit safety gate or authorize deployment.

`AGENTS.md` is generated agent-memory context. It is non-normative and must not be used as product, privacy, security, deployment, or release documentation.

## Next gated activity

Phase 0B containment and Phase 1A offline conformance are complete. The user explicitly authorized Phase 1A offline work, and the implementation passed its reviewed guard, deterministic Python/Swift, long-duration, and artifact/scope gates.

The next activity under ADR 0003 is G1 read-only salvage auditing: freeze PR
#8 at its exact draft head, map its reusable auth/privacy/durability work, and
separately bind the dirty N11D-C index and unstaged patch. Neither audit may
modify those artifacts. Protocol closure, gateway implementation, native
capture, cloud mutation, provider access, physical capture, candidate data,
pilot activity, deployment, and release retain their separate gates and need
fresh current authority.

See the [repository preservation inventory](docs/current-state/repository-preservation-inventory.md) for the dirty-work boundary.
