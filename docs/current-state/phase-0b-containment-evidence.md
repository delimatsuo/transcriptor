# Phase 0B Containment Evidence

**Executed:** 2026-07-15

**Evidence timestamp:** 2026-07-15T18:29:50Z

**Status:** Immediate deployment and anonymous-access paths contained; the isolated synthetic-only development boundary, repository boundary, and GitHub approval boundary are established; Phase 0B remains incomplete pending final panel review

**Authorized scope:** Disable automatic/unauthenticated deployment, inventory live GitHub and Google Cloud state, preserve existing data, and establish an isolated development boundary. This record does not authorize Phase 1, a branch push, deletion, or use of real interview data.

## 1. Independent staff review

The staff reviewer returned **approve with conditions**. The highest-risk issue was wrong-project mutation or administrative lockout because the local gcloud default pointed to an unrelated project.

The condition was resolved before GCP mutation:

- Every GCP command was pinned to account `deli@ellaexecutivesearch.com`.
- The account read project ID `transcriptor-490222` and project number `33726443105`.
- The account holds `roles/owner` on that project and remains the recovery administrator.
- The unrelated local gcloud default project was not changed.

A follow-up staff review initially blocked unlinking the legacy project because that could interrupt access to preserved candidate-related records. The user instead moved the legacy project to another active billing account, preserving its paid-service access while freeing a slot for the isolated project. The data-custody objection therefore did not apply to the executed billing move.

## 2. GitHub inventory and containment

Observed before containment:

- Repository: `delimatsuo/transcriptor`, public, default branch `main`.
- `main` and `staging` had no branch protection.
- No GitHub environments existed.
- Workflow ID `246957762`, name `Deploy`, was active.
- Its two recorded push runs failed during Cloud Run deployment.

Containment executed:

- Workflow ID `246957762` was disabled out of band through GitHub.
- Verification returned state `disabled_manually`.
- Verification returned zero queued and zero in-progress Actions runs.
- The checked-in workflow was changed locally to manual build-only: no push trigger, OIDC permission, GCP authentication, Cloud Run deployment, Firebase deployment, or `--allow-unauthenticated` command remains.

The local workflow change is not pushed. The remote disable plus disabled Google identity prevents deployment from the existing remote workflow without relying on a branch push from this dirty worktree.

Repository protection executed after containment:

- `main` and `staging` require pull requests, enforce protection for administrators, require linear history and conversation resolution, and disallow force-pushes and deletion.
- The repository has one administrator, so the required external approval count is zero; the pull-request boundary remains mandatory without creating a single-maintainer deadlock.
- GitHub environments `staging` and `production` require approval by `delimatsuo` and accept only the matching `staging` and `main` branches, respectively.
- The disabled workflow does not reference these environments. Any future deployment implementation must use the matching environment and pass a separate release review.

## 3. Legacy GCP project inventory

Project:

- ID: `transcriptor-490222`
- Number: `33726443105`
- Organization: `ellaexecutivesearch.com`
- Current billing account after the user-directed move: `01CA38-F1E90C-E9FD07` (`Billing_acc_2`)
- Billing account during the initial containment inventory: `014D44-7C46A4-17F79F` (`My Billing Account`)

### Cloud Run

One service exists:

- Service: `tars-backend-staging`
- Region: `us-central1`
- Ingress annotation: `all`
- Latest created revision: `tars-backend-staging-00002-76b`
- Latest ready revision: none
- Current service URL: none
- Current traffic: none
- Runtime identity: default Compute Engine service account

Two revisions exist. The first became ready but is retired; the second failed its container health check. Before containment, the service IAM policy granted `roles/run.invoker` to `allUsers` even though no ready route remained.

Containment executed:

- Removed `allUsers` from `roles/run.invoker`.
- Verified the service IAM policy has no bindings.
- Retained the service and revisions for evidence; no deployment or deletion occurred.

### GitHub deployment identity

Observed before containment:

- Workload Identity provider `github-pool/github-provider` trusted repository `delimatsuo/transcriptor`.
- Service account `github-deploy@transcriptor-490222.iam.gserviceaccount.com` held broad deployment roles including Cloud Run, Cloud Build, Artifact Registry, Firebase Hosting, Storage, and Service Account User.
- The service account had no user-managed keys.

Containment executed:

- Disabled the Workload Identity provider.
- Disabled the GitHub deploy service account.
- Retained its role bindings as recovery evidence. Re-enabling that account without first replacing the broad roles remains prohibited.

### Firestore

Observed:

- Native Firestore database `(default)` in `nam5`.
- Root collection `sessions` contains 16 documents.
- Document creation range: 2026-03-14T23:26:03Z through 2026-03-16T20:18:44Z.
- No Firebase Rules release exists in the live project.
- An anonymous Firestore REST read returned HTTP 403.

Containment handling:

- No document or subcollection content was read for this inventory.
- No Firestore data was changed or deleted.
- The checked-in `firestore.rules` now fails closed for every client read/write until ownership rules and cross-tenant tests are implemented.

### Cloud Storage

Observed:

| Bucket | Objects | Contents by type | Public IAM/ACL findings |
| --- | ---: | --- | --- |
| `run-sources-transcriptor-490222-us-central1` | 3 | ZIP source archives, 209,709 bytes | No public binding or object ACL |
| `transcriptor-490222-tars` | 4 | PDF files, 293,939 bytes | No public binding or object ACL |

Containment executed:

- Enforced Public Access Prevention on both buckets.
- Preserved every object. The four PDFs may contain candidate material and require a separately approved retention/deletion decision.

### Firebase Hosting

Observed:

- Default site: `transcriptor-490222`.
- Associated Firebase web app is active.
- Hosting release count: zero.

No hosting deployment or deletion occurred.

### Vertex AI

Observed before containment:

- The project cache configuration used the provider default, which means in-memory caching was enabled.

Containment executed:

- Set the project cache configuration to `disableCache: true`.
- Read-back verification returned `disableCache: true`.

### Speech-to-Text

- Speech-to-Text is enabled in the legacy project.
- Google documentation states that data logging is opt-in and that the prototype's v2 API path is not the optional v1 data-logging setup path.
- At `2026-07-15T16:07:42Z`, the Cloud Console data-logging page for project `transcriptor-490222` displayed `Data logging is disabled for this project for Google Cloud Speech API`.
- The Cloud Console session identified the active account as `Deli Matsuo (deli@ellaexecutivesearch.com)`.
- No Speech-to-Text setting was changed during this verification.

## 4. Isolated development project

Created:

- Project ID: `transcriptor-dev-20260715`
- Project number: `570346565602`
- Name: `Transcriptor Development`
- Parent organization: `558436851610` (`ellaexecutivesearch.com`)
- Labels: `environment=development`, `data_class=synthetic-only`, `phase=phase0b`, `status=blocked`
- Recovery administrator: `deli@ellaexecutivesearch.com`

Reserved identity:

- `tars-dev-runtime@transcriptor-dev-20260715.iam.gserviceaccount.com`
- Project-scoped roles: `roles/datastore.user`, `roles/speech.client`, and `roles/aiplatform.user`.
- Bucket-scoped role: `roles/storage.objectUser` on `gs://transcriptor-dev-20260715-tars` only.
- Secret-scoped role: `roles/secretmanager.secretAccessor` on `tars-dev-runtime-config` only.
- The account is disabled pending Phase 1 review.

Billing status:

- The intended administrator and recovery identity is `deli@ellaexecutivesearch.com`.
- A second link attempt at `2026-07-15T16:08:11Z`, explicitly pinned to that identity and billing account, failed with `Cloud billing quota exceeded` for `billingAccounts/014D44-7C46A4-17F79F`.
- At `2026-07-15T16:17:02Z`, Google Developers Help displayed a successful submission confirmation for an additional project-quota request and stated that review typically takes about two business days.
- The confirmation page did not echo the contact email entered. A contemporaneous search of the `deli@ellaexecutivesearch.com` inbox found no matching confirmation message, so the reply inbox is not yet independently verified.
- The user moved legacy project `transcriptor-490222` to billing account `01CA38-F1E90C-E9FD07` (`Billing_acc_2`) without disabling billing or losing resource access.
- Development project `transcriptor-dev-20260715` was then linked to `014D44-7C46A4-17F79F` (`My Billing Account`).
- Read-back verification returned `billingEnabled: true` for both projects under their respective accounts.

Isolated resources and provider controls:

- Enabled only the additional APIs required for the inactive boundary: Cloud Resource Manager, Firestore, Speech-to-Text, Vertex AI, and Secret Manager. Cloud Run remains disabled and no hosted endpoint exists.
- Created Firestore Native database `(default)` in `nam5` with delete protection enabled and PITR disabled. It contains no application data; an anonymous document read returned HTTP 403.
- Created `gs://transcriptor-dev-20260715-tars` in `US-CENTRAL1` with uniform bucket-level access, Public Access Prevention, and seven-day soft delete. It contains no objects.
- Created empty secret container `tars-dev-runtime-config`; it has no secret versions.
- Verified the dev project's Speech-to-Text Console status says data logging is disabled.
- Set Vertex AI project cache configuration to `disableCache: true` and verified the read-back.
- Inventoried the default Speech-to-Text and Vertex AI provider quotas. Custom lower provider quotas remain required before the runtime identity is enabled; the disabled identity and absent endpoint are the current hard usage gates.
- Created monthly project-scoped budget `8da65d0f-15c3-459a-a42e-d1afc2218d94` on `My Billing Account`: BRL 250, all credits included, with current-spend alerts at 50%, 90%, and 100%.

The isolated project is now a configured but deliberately inactive development boundary. It is not a deployed environment and must contain synthetic data only.

## 5. Repository preservation and clean baseline

- Preserved the unfinished speaker-correlation and Meet-extension work on local branch `codex/preserve-speaker-correlation-wip-20260715`, commit `f5fc9f61cfddfc67de6bb2cf7af7c23f402c9840`.
- The preservation commit intentionally includes ignored `extension/manifest.json` and excludes generated `frontend/tsconfig.tsbuildinfo`, agent memory, local secrets, documentation, and containment configuration.
- Committed only the reviewed Phase 0 containment/configuration/documentation package to `staging` as `e8fb026f77be11fe43450f9727553520c42d9f94`.
- Created clean worktree `/Volumes/Extreme Pro/myprojects/Transcriptor-worktrees/native-companion-phase1` on local branch `codex/native-companion-phase1` from that containment commit.
- No branch or commit was pushed.

## 6. Remaining gates

Before Phase 0B can be marked complete:

1. Re-run the plan review and obtain an explicit Phase 1 proceed decision; any approval to enable the runtime identity must include lower provider quota overrides.

## 7. Current decision

- **Immediate public/deployment exposure:** contained.
- **Legacy data:** private in observed checks and preserved; retention/deletion unresolved.
- **Isolated development environment:** billing and private resources configured; inactive, synthetic-only, no hosted endpoint, and runtime identity disabled.
- **Phase 0B:** incomplete pending final panel review only.
- **Phase 1:** blocked.
