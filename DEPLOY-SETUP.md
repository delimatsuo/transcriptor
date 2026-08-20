# Historical Prototype Deployment Setup Log

> **Superseded as architecture guidance.** This file records deployment scaffolding generated on 2026-03-16. It is not a production-readiness statement, deployment authorization, or the target native-companion architecture.
>
> Phase 1 implementation and all deployments remain blocked. On 2026-07-15 the remote workflow, WIF provider, and deploy service account were disabled, and the Cloud Run public invoker binding was removed. See `docs/current-state/phase-0b-containment-evidence.md`.
>
> Canonical status: `README.md` and `docs/current-state/documentation-and-config-status.md`.

**Generated**: 2026-03-16
**Generator**: /deploy-setup v1
**Prototype platform scaffold**: Firebase Hosting + Cloud Run
**Config schema**: v1

## What Was Configured

- [x] Deploy config: `.claude/deploy-config.yaml`
- [x] GitHub Actions: `.github/workflows/deploy.yml` (starter)
- [x] GitHub Actions: `.github/workflows/rollback.yml` (starter)
- [x] Dockerfile: `Dockerfile` at the repository root
- [x] Staging branch created and pushed

## Detected Configuration

| Item | Value | Source |
|------|-------|--------|
| Stack | Next.js + TypeScript (frontend), Python 3.12 + FastAPI (backend) | package.json, requirements.txt |
| Prototype platform scaffold | Firebase Hosting + Cloud Run | User input + Firebase console |
| GCP Project | transcriptor-490222 | Firebase console |
| Git Provider | GitHub | git remote |
| Branch Strategy | staging -> main | Created by /deploy-setup |
| CI/CD | GitHub Actions | Generated |
| Auth | Workload Identity Federation | User choice |

## Manual Steps Required

- [x] Workload Identity Federation configured programmatically:
  - Pool: `github-pool`, Provider: `github-provider`
  - Service account: `github-deploy@transcriptor-490222.iam.gserviceaccount.com`
  - Roles: Cloud Run Admin, Firebase Hosting Admin, Service Account User, Storage Admin
  - Bound to repo: `delimatsuo/transcriptor`
- [ ] Enable branch protection on `main` and `staging`
- [ ] Set up Firebase Hosting configuration (`firebase.json` + `.firebaserc`)
- [ ] Configure frontend environment variables for Cloud Run backend URL
- [ ] Historical incomplete step: `/deploy-staging` was never verified. **Do not run it** while the current deployment block is active.

## Pre-containment limitations discovered after generation

- The workflow is push-triggered for both `staging` and `main`.
- The Cloud Run command uses `--allow-unauthenticated` for both branches, despite a comment that previously described it as staging-only.
- Staging and production entries use the same GCP project and do not prove datastore, bucket, identity, or quota isolation.
- The current FastAPI process owns local `sounddevice`/BlackHole capture, which a Cloud Run container cannot perform for a user's Mac.
- `firebase.json` and `.firebaserc` are absent, so the frontend deployment step is skipped.
- Frontend REST calls remain hardcoded to localhost.
- Week 4 now implements local application-layer Google/Firebase attribution,
  server-derived user/org ownership, and fail-closed cross-tenant checks. This
  remains an internal shared-trust boundary: Admin SDK/ADC bypasses Firestore
  rules, and hosted isolation is still a separate gate.
- Automated backend/frontend tests are not enabled in CI.
- Rollback covers Cloud Run traffic only; it does not cover frontend, IAM, rules, configuration, or data changes.

## Target interpretation

Firebase Hosting and Cloud Run may remain part of the future architecture, but only with this boundary:

- A native macOS companion owns device permissions and audio capture.
- An authenticated Cloud Run service may own control, STT orchestration, transcript events, and AI processing.
- Firebase Hosting may host the web interview workspace.

Immediate containment was executed on 2026-07-15. The isolated development project is now billed and configured as an inactive, synthetic-only boundary with no hosted endpoint and a disabled runtime identity. Before any deployment, complete the remaining gates in `docs/current-state/phase-0b-containment-evidence.md` and re-run the panel review.
