# Phase 1 Gate Panel Re-Review

**Date:** 2026-07-15

**Reviewed baseline:** clean branch `codex/native-companion-phase1`, pre-amendment commit `1d2ad13ad77bc317350a2464b851899c769b0d91`

**Reviewers:** staff engineer; security/privacy engineer; UI/UX lead

**Method:** independent initial reviews followed by one shared debate round over the same concern and consensus matrix

## Panel decision

**Approve with conditions: Phase 1A offline only.**

No P0 objection remains. Phase 0B containment is sufficient and is complete. The current permission extends only to panel clearance of an offline source/schema/protocol conformance slice. It is not implementation authorization.

Phase 1A begins only after this docs-only reconciliation is committed as a new clean baseline, the local preflight passes, and the user explicitly authorizes **Phase 1A offline protocol conformance**. Phase 1B hosted fixtures, Phase 1C offline native capture, and Phase 1D integrated synthetic testing remain blocked.

## Consensus recommendations

1. Use four independent gates:
   - **1A:** offline protocol conformance with fixed synthetic bytes and networking disabled;
   - **1B:** hosted gateway/STT with server-allowlisted fixtures and separate security/activation approval;
   - **1C:** offline native capture into an in-memory/null sink under controlled fixture routing;
   - **1D:** separately authorized native-to-hosted synthetic integration after 1B and 1C pass.
2. Define transport as at least once with idempotent processing and exactly one non-overlapping terminal transcript-or-gap outcome for each attempt-independent known coverage range.
3. Use one canonical versioned schema with generated or validated Swift and Python bindings and deterministic crash/reconnect/rotation conformance tests.
4. Enforce synthetic-only work through fixed manifests, controlled routing, scoped execution, prohibited identity fields, and artifact scans; never trust a client `synthetic` flag.
5. Model physical capture, transport, each source's health, coverage, finalization, and deletion as independent truths. Whenever capture may continue, retain the prominent recording indicator.
6. Before hosted work, require reviewed gateway authentication, exact project/runtime attestation, lower provider quotas, least privilege, protected approval, fresh containment readback, negative authorization tests, and an independently executable kill switch.
7. Before native work, record the Python baseline, fixed fixture provenance/checksums, numeric thresholds, supported macOS/test Macs, controlled routing, accessibility/language checks, and contamination stop behavior.

## Required plan changes before proceeding

The governing plan, Phase 1 plan, ADR statuses, protocol contract, privacy contract, product-state contract, current-state evidence, canonical index, and README must consistently state the 1A-only decision and 1B-1D blocks.

The Phase 1 plan must include, for every gate, allowed and forbidden work, entry and exit evidence, required authorization, owner/pass-fail evidence expectations, and stop/rollback behavior. Phase 1A must explicitly prohibit credentials, ADC, network access, provider calls, physical capture, cloud mutation, real data, push, deployment, and legacy-data mutation.

These amendments are part of the documentation-only reconciliation changeset accompanying this review. A narrow verification must confirm that no contradictory status or weakened gate remains before the panel clearance is treated as operative.

## Unresolved objections

No unresolved P0 or Phase 1A P1 remains after the required documentation reconciliation and preflight.

The following P1 objections remain intentional blockers to later gates:

- **Phase 1B:** hosted authentication topology, exact-project/runtime enforcement, lower quotas, least privilege, server-side fixture allowlisting, abuse controls, and kill-switch proof.
- **Phase 1C:** measurable baseline/thresholds, supported hardware/OS, ambient-audio prevention and contamination response, composite capture UX, stop/discard/unknown-boundary behavior, and accessibility/localization evidence.
- **Phase 1D:** full re-verification of every 1B and 1C control before any integrated audio egress.

The 16 legacy session records and four private PDFs remain a P2 custody issue for this phase. They are isolated from Phase 1 and must not be read, migrated, or deleted without separate authorization.

## Verification required

Before Phase 1A authorization:

- record the new docs-only clean commit and prove it descends from `1d2ad13`;
- confirm preserved WIP commit `f5fc9f61cfddfc67de6bb2cf7af7c23f402c9840` is recoverable;
- confirm the clean worktree contains no preserved feature WIP, ignored credentials, local secrets, or real data;
- confirm active deployment containment remains unchanged;
- verify the fixed manifest in `docs/test-fixtures/phase-1a-synthetic-byte-manifest.md`; when the conformance process exists, require networking disabled and abort on credential or environment lookup;
- scan the reconciled documents for stale contradictory status and approval language.

Phase 1B, 1C, and 1D must satisfy the evidence listed in their gate table before separate authorization is requested.

## Recommended next action

Commit the documentation-only reconciliation on `codex/native-companion-phase1`, record and verify that new clean baseline, run the Phase 1A local preflight, and present this panel decision to the user for explicit authorization. Do not implement Phase 1A, push, deploy, enable identities/APIs/endpoints, capture audio, or mutate cloud/legacy data before that authorization.
