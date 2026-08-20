# Native Companion Plan Panel Review

**Current status:** Historical initial review. Its Phase 1 block was superseded by `docs/reviews/2026-07-15-phase-1-gate-panel-review.md`, which conditionally clears Phase 1A offline only. The findings below remain the record of the earlier decision at that time.

**Date:** 2026-07-15

**Reviewers:** Staff Engineering, Security/Privacy Engineering, Product UI/UX Engineering

**Review method:** Independent initial reviews followed by one structured debate round

## Final consensus

Panel decision: **block**

The architecture direction is viable and may remain accepted for planning. Phase 1 implementation, pushes to `staging` or `main`, deployment, and use of real candidate data remain blocked.

## Consensus recommendations

1. **P0:** Disable push-triggered unauthenticated deployment and verify live GitHub/GCP containment before Phase 1.
2. **P0:** Use an isolated approved development project, identity, datastore, bucket, quotas, and STT configuration.
3. **P1:** Put minimal authenticated enrollment, server-derived ownership, limits, and revocation before any native audio reaches a hosted endpoint.
4. **P1:** Define separate gateway-admission, provider-forwarding, and durable-transcript acknowledgements; define exactly when audio may be released.
5. **P1:** Define deterministic IDs, per-source sequence ranges, exact gap ranges, retries, and bounded-buffer loss semantics.
6. **P1:** Establish one authoritative companion/web capture state, consent gate, pause/stop truthfulness, and visible degraded/gap behavior.
7. **P1:** Reconcile all documentation/configuration classifications and preserve tracked, untracked, and ignored work before selecting a clean baseline.
8. **P1:** Use synthetic fixtures by default. Do not transmit consented human audio until isolated-project STT settings are verified.
9. **P2:** Add note synchronization, assessment provenance, deletion/export, accessibility, and localization requirements before their respective phases.

## Required plan changes before proceeding

- Add a canonical source-of-truth and status index.
- Mark historical and prototype deployment surfaces accurately.
- State explicitly that documentation does not neutralize active deployment risk.
- Add a preservation inventory including ignored `extension/manifest.json`.
- Align approval language across the governing plan, ADR, privacy contract, and Phase 1 plan.
- Reorder Phase 1 around containment, isolated environment, minimal auth/ownership, protocol semantics, then native streaming.
- Add acknowledgement, audio-release, deterministic-ID, gap-range, and reconnect semantics.
- Add a normative companion/web state and interaction contract.
- Require project-specific STT settings evidence and synthetic-only fixtures at Phase 1 entry.

## Unresolved objections

- **Staff:** Existing Cloud Run/IAM/data exposure has not been inventoried; the risk may already be live.
- **Security:** The active workflow/configuration remains unsafe until a separately authorized containment change is executed and verified.
- **UI/UX:** Without containment and the authoritative capture-state contract, the product could deploy the wrong build or tell users capture has stopped when it has not.

## Verification required after plan changes

- Confirm every documentation/configuration surface is classified and cross-linked.
- Verify the complete tracked/untracked/ignored preservation inventory.
- Retrieve live GitHub branch/environment/WIF configuration.
- Retrieve live Cloud Run IAM, ingress, revisions, traffic, service accounts, and environment variables.
- Inventory Firestore, GCS, Firebase, logs, and current stored-data exposure.
- Verify isolated development project and STT data-logging configuration.
- Prove unauthenticated and cross-tenant requests are rejected before hosted audio.
- Run protocol conformance tests for acknowledgement, release, retries, exact gaps, and idempotency.
- Run companion/web consent, pause, stop, reconnect, and degraded-state tests.

## Recommended next action

Complete the documentation-only reconciliation, then obtain separate authorization to execute and verify the P0 deployment-containment gate. Re-run this panel before any spike code, branch push, deployment, or real interview data.
