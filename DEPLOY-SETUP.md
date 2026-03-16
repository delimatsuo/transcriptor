# Deployment Setup Log

**Generated**: 2026-03-16
**Generator**: /deploy-setup v1
**Platform**: Firebase Hosting + Cloud Run
**Config schema**: v1

## What Was Configured

- [x] Deploy config: `.claude/deploy-config.yaml`
- [x] GitHub Actions: `.github/workflows/deploy.yml` (starter)
- [x] GitHub Actions: `.github/workflows/rollback.yml` (starter)
- [x] Dockerfile: `backend/Dockerfile`
- [x] Staging branch created and pushed

## Detected Configuration

| Item | Value | Source |
|------|-------|--------|
| Stack | Next.js + TypeScript (frontend), Python 3.12 + FastAPI (backend) | package.json, requirements.txt |
| Platform | Firebase Hosting + Cloud Run | User input + Firebase console |
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
- [ ] Run `/deploy-staging` to test the pipeline end-to-end
