# T.A.R.S. / Transcriptor

T.A.R.S. is an executive-search interview companion. The target product uses a native macOS companion to capture microphone and system audio, streams transient audio to Google Cloud Speech-to-Text, lets recruiters take timestamped notes, and produces evidence-grounded assessments.

## Current execution status

**Architecture direction:** accepted for planning.

**Phase 1 implementation:** blocked by the 2026-07-15 review panel.

**Documentation reconciliation:** complete for project-owned documents and conflicting active configuration comments; generated and third-party dependency documentation is classified but not rewritten.

**Deployment:** not authorized. On 2026-07-15 the remote deploy workflow, GitHub Workload Identity provider, and deploy service account were disabled; the Cloud Run public invoker binding was removed. The checked-in workflow is now manual build-only with no deployment job.

**Data:** do not use real candidate or customer data. The legacy project contains 16 session records and four private PDFs that were preserved during containment. Their retention/deletion basis is unresolved. The current prototype still writes local FLAC audio and lacks application-layer ownership enforcement.

**Development environment:** `transcriptor-dev-20260715` is billed, labeled synthetic-only, and configured with an empty private Firestore database, private GCS bucket, empty secret container, disabled runtime identity, disabled STT data logging, and disabled Vertex cache. It has no hosted endpoint and is deliberately inactive.

Immediate public/deployment exposure is contained, but Phase 0B and Phase 1 remain blocked. See the [containment evidence](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/current-state/phase-0b-containment-evidence.md>).

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

1. **Execution status and document classification:** [documentation and configuration status](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/current-state/documentation-and-config-status.md>)
2. **Live containment evidence:** [Phase 0B containment evidence](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/current-state/phase-0b-containment-evidence.md>)
3. **Governing product and architecture plan:** [native companion and note-first interviews](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/plans/2026-07-15-native-companion-and-note-first-interviews.md>)
4. **Accepted architecture direction:** [ADR 0001](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/architecture/0001-native-companion-cloud-stt.md>)
5. **Normative protocol target:** [companion streaming protocol](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/architecture/0002-companion-stream-protocol.md>)
6. **Normative privacy target:** [data-flow, retention, and deletion contract](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/privacy/data-flow-retention-contract.md>)
7. **Normative product behavior:** [companion and web state contract](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/product/companion-web-state-contract.md>)
8. **Phase-specific execution plans:** [Phase 1 native capture spike](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/plans/2026-07-15-phase-1-native-capture-spike.md>)
9. **Review decisions:** [2026-07-15 panel review](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/reviews/2026-07-15-native-companion-panel-review.md>)
10. **Historical records:** `DEPLOY-SETUP.md` and similar dated setup logs describe what was attempted at that time; they are not current authorization or target architecture.
11. **Active configuration and source:** workflows, manifests, rules, Dockerfiles, and code describe current behavior. They do not override an explicit safety gate or authorize deployment.

`AGENTS.md` is generated agent-memory context. It is non-normative and must not be used as product, privacy, security, deployment, or release documentation.

## Next gated activity

Immediate containment and the inactive development boundary are established. Before Phase 1 can begin:

1. Preserve and inventory all tracked, untracked, and ignored work.
2. Keep the BRL 250 monthly dev budget active and configure lower provider quota overrides in the same approved change that enables the runtime identity.
3. Put minimal authentication, ownership, limits, and revocation before hosted audio.
4. Add GitHub branch/environment protection before any deployment workflow is restored.
5. Re-run the plan gate with direct containment evidence.

See the [repository preservation inventory](</Volumes/Extreme Pro/myprojects/Transcriptor/docs/current-state/repository-preservation-inventory.md>) for the dirty-work boundary.
