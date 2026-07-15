# Documentation and Configuration Status

**Reviewed:** 2026-07-15

**Purpose:** Prevent historical records, prototype configuration, generated memory, and target architecture from being mistaken for one another.

## Review coverage

The 2026-07-15 reconciliation reviewed every project-owned Markdown document at the repository root and under `docs/`, plus active deployment, environment, container, Firestore-rule, dependency-manifest, and extension-manifest surfaces that could contradict those documents. Vendored documentation under `.venv/` and `frontend/node_modules/` is third-party dependency material and is outside the product-documentation hierarchy.

`AGENTS.md` was reviewed but not edited because it is generated memory context rather than a maintained product document. Its entries are observations, not approvals or current status. The canonical status in this file and `README.md` governs.

## Classification rules

- **Canonical:** Defines current planning status or approved target behavior.
- **Normative target:** Requirements implementation must satisfy; not a claim about current behavior.
- **Phase plan:** Execution proposal gated by its own status and prerequisites.
- **Historical:** Records prior setup or decisions; does not authorize current action.
- **Prototype configuration:** Describes or controls current prototype behavior; not production-ready.
- **Generated/non-normative:** Tool-owned context that may change automatically.
- **Generated dependency metadata:** Build/dependency state, not product architecture.

## Surface inventory

| Surface | Classification | Current meaning | Required handling |
| --- | --- | --- | --- |
| `README.md` | Canonical | Entry point, status, and documentation hierarchy | Keep synchronized with review gates |
| `docs/plans/2026-07-15-native-companion-and-note-first-interviews.md` | Canonical governing plan | Approved direction; Phase 1A conditionally cleared, 1B-1D blocked | Architecture changes require plan/ADR updates |
| `docs/architecture/0001-native-companion-cloud-stt.md` | Normative target | Device capture plus cloud STT decision | Does not authorize implementation or deployment |
| `docs/architecture/0002-companion-stream-protocol.md` | Normative target | Audio custody, acknowledgement, release, retry, and exact-gap semantics | Required before hosted audio; does not authorize implementation |
| `docs/privacy/data-flow-retention-contract.md` | Normative target | Required privacy/data lifecycle | Explicitly not a claim about current prototype behavior |
| `docs/product/companion-web-state-contract.md` | Normative target | Capture authority and user-visible state semantics | Required before protocol/UI implementation |
| `docs/plans/2026-07-15-phase-1-native-capture-spike.md` | Phase plan | 1A offline panel-approved with conditions; 1B-1D blocked | 1A still requires clean preflight and explicit user authorization |
| `docs/test-fixtures/phase-1a-synthetic-byte-manifest.md` | Normative test-input manifest | Fixed content-free byte recipes and checksums for 1A | Generated in memory only; reject unlisted input |
| `docs/reviews/2026-07-15-native-companion-panel-review.md` | Canonical review record | Panel decision and blockers | Update only through a new review record |
| `docs/reviews/2026-07-15-phase-1-gate-panel-review.md` | Canonical review record | Re-review consensus and Phase 1A-only decision | Governs the current panel gate |
| `docs/current-state/phase-0b-containment-evidence.md` | Current-state evidence | Live GitHub/GCP inventory, containment mutations, and remaining gates | Refresh after any relevant external-state change |
| `docs/current-state/phase-1a-baseline-preflight-evidence.md` | Current-state evidence | Immutable Phase 1A baseline plus repository, fixture, and live-containment preflight | Refresh before implementation if relevant state drifts |
| `docs/current-state/repository-preservation-inventory.md` | Current-state record | Dirty/untracked/ignored file boundary | Refresh before any branch/worktree operation |
| `DEPLOY-SETUP.md` | Historical | March 2026 prototype deployment setup log | Retain with superseded/incomplete warning |
| `.claude/deploy-config.yaml` | Contained prototype configuration | Release targets are safety sentinels and authentication is disabled | Do not replace sentinels before isolated-environment review |
| `.github/workflows/deploy.yml` | Contained build configuration | Manual build-only; no push trigger, OIDC permission, or deployment job | Remote workflow is also `disabled_manually` |
| `.github/workflows/rollback.yml` | Partial prototype runbook/configuration | Cloud Run traffic rollback only | Does not cover frontend, IAM, rules, config, or data |
| `.env.example` | Prototype local-development reference | Current Python/BlackHole variables; retention value is inert | Do not treat as native-companion or production configuration |
| `Dockerfile` | Prototype container definition | Packages the current FastAPI backend, including local-capture dependencies | Not production-ready cloud control plane |
| `firestore.rules` | Contained target configuration | Checked-in client policy denies every read/write | Live project has no Rules release and anonymous read returns 403 |
| `extension/manifest.json` | Experimental implementation metadata | Meet-only local speaker-enrichment adapter | Optional, untracked/ignored, not capture infrastructure |
| `AGENTS.md` | Generated/non-normative | Agent memory index and context | Do not edit as product documentation or use as authority |
| `requirements.txt` | Dependency manifest | Current Python prototype dependencies | Not architecture documentation |
| `frontend/package.json` | Dependency/build manifest | Current Next.js scripts and dependencies | Not deployment authorization |
| `frontend/package-lock.json` | Generated dependency metadata | Locked npm dependency graph | Do not use as design documentation |
| `frontend/tsconfig.json` | Build configuration | Current TypeScript settings | Not architecture documentation |

## Reconciled apparent conflicts

### Firebase Hosting and Cloud Run versus native companion

There is no target conflict when the boundary is stated correctly:

- The native companion captures device audio.
- Cloud Run may later host the authenticated control and intelligence plane.
- Firebase Hosting may later host the web workspace.
- The existing container/workflow does not yet implement that boundary and is not production-ready.

### BlackHole versus native capture

- BlackHole is part of the current prototype.
- Native system-audio capture is the approved default target.
- BlackHole may remain a compatibility fallback only.

### Local FLAC versus no persistent raw audio

- Local FLAC is current prototype behavior.
- No persistent raw audio is a target requirement.
- No privacy claim may imply that the target is already implemented.

### `DATA_RETENTION_DAYS=90` versus configurable retention

- The variable exists but is not enforced.
- It is not evidence of deletion or a current retention policy.
- Approved retention values remain unresolved before external beta.

### Firestore “own data” comment versus rule behavior

- The previous comment was inaccurate.
- The checked-in policy now denies every client read/write.
- The live project has no Firebase Rules release; observed anonymous access is denied.
- Admin SDK access bypasses client rules, so application-layer ownership is still required before hosted or multi-user use.

### Meet extension versus provider independence

- The extension is experimental, Meet-only speaker enrichment.
- Device capture must work without it.
- Its ignored `manifest.json` must be explicitly preserved if the extension work is retained.

## Containment result and remaining block

The immediate P0 is contained with direct evidence:

- Remote Deploy workflow state is `disabled_manually` with no queued/active runs.
- GitHub WIF provider and deploy service account are disabled.
- Cloud Run has no traffic and no public invoker binding.
- Both legacy buckets enforce Public Access Prevention.
- Vertex AI in-memory caching is disabled in the legacy project.

Phase 0B is complete. Billing, the inactive service/data/provider boundary, source preservation, clean worktree, protected branches, and approval-gated GitHub environments are configured. The isolated project has private empty Firestore/GCS resources, an empty secret container, a disabled runtime identity, verified-disabled STT data logging, verified-disabled Vertex cache, and a BRL 250 monthly project budget. The panel re-review found no P0 objection and returned **approve with conditions: Phase 1A offline only**. Phase 1A still requires the docs-amendment baseline, fresh preflight, and explicit user authorization; Phases 1B-1D remain blocked. See `docs/current-state/phase-0b-containment-evidence.md` and `docs/reviews/2026-07-15-phase-1-gate-panel-review.md`.
