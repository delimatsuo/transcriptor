# Phase 1A Baseline and Preflight Evidence

**Recorded:** 2026-07-15T18:51:34Z

**Status:** Baseline/repository/containment preflight passed. Phase 1A implementation is not yet authorized or started.

**Normative baseline commit:** `f9877132e2c34980f12c8098e09bbd8134fd9bd6`

**Branch/worktree:** `codex/native-companion-phase1` in `/Volumes/Extreme Pro/myprojects/Transcriptor-worktrees/native-companion-phase1`

## 1. Immutable ancestry and repository boundary

- The baseline's parent is docs-only containment/gate commit `1d2ad13ad77bc317350a2464b851899c769b0d91`.
- `git merge-base --is-ancestor 1d2ad13... f987713...` passed.
- Preserved feature-WIP commit `f5fc9f61cfddfc67de6bb2cf7af7c23f402c9840` is a readable commit.
- The baseline tree contains none of `backend/speaker_correlation.py`, `extension/manifest.json`, or `extension/background.js`.
- The clean worktree had zero tracked, untracked, or ignored status lines after the baseline commit.
- The tracked secret-name scan returned only `.env.example`; a content scan found no private-key or API-secret pattern.
- `git diff --check` passed before commit. A post-commit status and object readback passed; unreachable temporary-index objects are not referenced by the baseline and do not affect recoverability.

## 2. Documentation and fixture checks

- The staff post-amendment readback returned `PASS` with no remaining P0/P1 discrepancy.
- The stale-status scan returned zero matches outside the explicitly historical first panel review, which links to the current re-review.
- The current panel record has the required decision, consensus, required changes, unresolved objections, verification, and next-action sections.
- README canonical links are repository-relative and resolve within this baseline.
- The generated fixture recipes in `docs/test-fixtures/phase-1a-synthetic-byte-manifest.md` reproduced all three recorded SHA-256 values:
  - `zero-3200-v1`: `5a312281df4bd8dfbb4d4a94ad0bf44d01bb8cfced1206b90e21b4ca0568cdb1`
  - `counter-3200-v1`: `78ad7b2c3cf464e4e219f6044605741a65a8197287a6951d142870af42c3397d`
  - `lcg-3200-v1`: `0a93dffb664217df4f004a088bdbf71c1a44b2416af59def297cd3668ede05fd`
- The contained deploy/config scan found none of `--allow-unauthenticated`, OIDC write permission, GitHub GCP auth, Cloud Run deploy, Firebase deploy, or a push trigger.

## 3. Live GitHub containment readback

Repository `delimatsuo/transcriptor` returned:

- Deploy workflow ID `246957762`: `disabled_manually`.
- Queued/running/waiting/requested/pending workflow runs: `0`.
- `main` and `staging`: pull-request boundary enabled, administrator enforcement enabled, linear history and conversation resolution required, force-push and deletion disabled.
- `staging` environment: one required reviewer; custom branch policy allows only `staging`.
- `production` environment: one required reviewer; custom branch policy allows only `main`.

No branch or commit was pushed.

## 4. Live Google Cloud containment readback

Reads were pinned to account `deli@ellaexecutivesearch.com`, the exact target project, and the matching quota/billing project to avoid local quota-project drift.

Legacy project `transcriptor-490222`:

- Cloud Run service `tars-backend-staging`: no URL, no traffic, and no latest ready revision.
- Public `allUsers`/`allAuthenticatedUsers` invoker count: `0`.
- Workload Identity provider `github-pool/github-provider`: disabled.
- Deploy service account `github-deploy@transcriptor-490222.iam.gserviceaccount.com`: disabled.

Development project `transcriptor-dev-20260715`:

- Runtime service account `tars-dev-runtime@transcriptor-dev-20260715.iam.gserviceaccount.com`: disabled.
- Cloud Run API enabled count: `0`; Cloud Run service count: `0`.
- Secret `tars-dev-runtime-config`: `0` versions.
- Bucket `gs://transcriptor-dev-20260715-tars`: `0` objects.
- Firestore root collection count: `0`; no next-page token.

This was a Phase 1A containment refresh, not the fuller Phase 1B activation preflight. Phase 1B still requires fresh STT/Vertex settings, quota, IAM, auth-topology, endpoint, and kill-switch evidence before separate authorization.

## 5. Remaining execution preflight

No Phase 1A source or test runner exists yet, so no runtime test is claimed. If the user explicitly authorizes Phase 1A, the first implementation slice must create the offline harness so that networking is disabled and credential, ADC, secret, environment-project, endpoint, non-fixture, and persistent-payload access aborts before any conformance test runs.

## 6. Decision boundary

The repository and live containment baseline are ready for an explicit user decision on **Phase 1A offline protocol conformance only**. This evidence does not authorize Phase 1A implementation, Phase 1B-1D, push, merge, deploy, cloud mutation, native capture, ambient/human audio, real data, or legacy-data mutation.
