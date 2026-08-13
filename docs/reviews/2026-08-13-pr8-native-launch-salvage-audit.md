# PR #8 Native-Launch Salvage Audit

**Status:** Read-only audit complete; prior staff observations incorporated;
exact-artifact staff and security/privacy review required before publication.
Approval must live in an external review record bound to an exact commit and
tree; this document does not claim self-bound approval.

**Date:** 2026-08-13

**Governing decision:**
`docs/architecture/0003-native-capture-launch-boundary.md`

**Audit rule:** This document evaluates the committed pull-request range only.
It does not authorize a PR mutation, cherry-pick, implementation, hosted
operation, credential use, deployment, or merge.

## 1. Exact artifact binding

| Field | Bound value |
| --- | --- |
| Pull request | `#8`, `Week 4: authenticated internal tenancy` |
| URL | `https://github.com/delimatsuo/transcriptor/pull/8` |
| State at audit | `OPEN`, draft, merge state `CLEAN` |
| Base branch | `codex/week-3-evidence-report` |
| Base commit | `867cd6e57a5f93274fc81a42fd4ddb7a2e2cd218` |
| Head branch | `codex/week-4-auth` |
| Head commit | `754285480255de65f72b51a1ee529a6a7f95ba36` |
| Range size | 101 commits; 77 paths; 6,548 additions; 407 deletions |
| Full-index binary range patch SHA-256 | `c0872f1d840269de76b015f5f53cb54f300fa4217ba4675d742e001bc8ac06d4` |

The four checks reported for the exact head were green at the 2026-08-13
readback: two `Backend tests` runs and two `Frontend tests and build` runs.
Those checks prove only the workflows that ran against this source head. They
do not prove the target gateway boundary, hosted tenancy, native capture,
provider forwarding, deletion execution, or launch readiness.

The attached `week-4-auth` worktree was dirty during this audit. It contained
seven unstaged deletions:

- `backend/scripts/physical_audio_gate.py`
- `backend/scripts/preflight_audio.py`
- `backend/tests/test_physical_audio_gate.py`
- `backend/tests/test_preflight_audio.py`
- `docs/current-state/week-4-auth-evidence.md`
- `docs/launch/2026-08-0X-windows-gate-decision.md`
- `docs/launch/windows-spike-runbook.md`

It also contained protected untracked paths under `docs/handoffs/` and the
frontend instruction files. None of those working-tree changes or protected
untracked files were read as PR content, modified, staged, or included in the
classification below. Source inspection used a clean detached worktree at the
exact committed head.

## 2. Recommendation

Do **not** merge or mechanically cherry-pick PR #8. Preserve its exact head as
a frozen reference, port its useful behaviors into focused branches after
protocol closure, and close the mixed PR as superseded once those replacements
are independently qualified.

The PR contains valuable auth, ownership, provider-bound, durability, and
frontend hardening. Its first auth commit also binds those behaviors directly
to a process that starts local `sounddevice`/BlackHole capture. Subsequent
commits repeatedly modify the same large `backend/main.py`, process-local
capability stores, local STT manager, and local web session flow. A cherry-pick
would therefore import the discarded capture boundary or create a misleading
partial security boundary.

The safe reuse unit is the **behavior plus its negative tests**, not the mixed
commit graph. The new gateway should reimplement those behaviors against the
versioned companion protocol, durable fenced leases, transient audio custody,
and companion-authoritative capture state.

### 2.1 Blocking findings

- **P0 — false capture truth and wrong launch target.** The session-create
  route starts local BlackHole plus microphone capture. The frontend treats the
  successful HTTP response as capture start and immediately sets
  `isActive=true`. This violates ADR 0003's requirement that only
  companion-originated events assert physical capture and can present an
  authenticated local prototype as if it were the target gateway.
- **P1 — missing gateway security boundary.** Static email allowlisting, one
  configured organization, and process-memory WebSocket/stop capabilities do
  not provide durable companion enrollment, revocation, disclosure admission,
  lease fencing, or multi-instance replay protection.
- **P1 — deletion is not failure-atomic.** Recursive deletion mutates children
  while it discovers and authorizes later records. A later mismatch or crash
  can leave a partial deletion. Its GCS dependency catches blob-delete errors
  and returns `false`, allowing the session deletion path to continue, write a
  tombstone, and report success while a blob remains.
- **P1 — no durable transcript-or-gap replay contract.** WebSocket replay is a
  process-local 1,000-message ring. When history is unavailable it logs that a
  snapshot is needed and returns without sending one. STT work has no durable
  forwarding journal, protocol watermarks, or idempotent gap projection.

## 3. Commit classification

Every range commit is assigned to exactly one class below; some identifiers
are repeated in explanatory notes. The class describes semantic salvageability,
not permission to cherry-pick.

### Class 1: architecture-independent behavior

These units are the strongest candidates for narrow patch-porting after their
target files are established. Even here, fresh verification is required.

| Commit | Reusable behavior |
| --- | --- |
| `91a65f3` | Declares the HTTP test dependency. |
| `fd48741` | Refreshes pinned GitHub Action runtimes; re-evaluate pins on the destination branch. |
| `e3d72ff` | Bounded Gemini request duration. |
| `2fdc06f` | Bounded rolling-summary input. |
| `7314ab7` | Bounded retry-backoff exponent. |
| `3bb4e39` | Explicit Vertex region selection. |
| `1841b80` | Fail-closed rejection of the prohibited global Vertex endpoint. |
| `f21b24c` | Central output-token ceilings. |
| `cf7a157` | Streaming-output-bound regression test. |
| `254a22e` | Concurrent bounded WebSocket fanout and focused tests, if the destination retains the same Python connection manager. |

### Class 2: reusable only after native/gateway adaptation

| Commit group | Commits | Adaptation requirement |
| --- | --- | --- |
| Identity and tenancy | `9344793`, `0d2fd80`, `cbdef14` | Retain token validation, non-enumerating ownership checks, and boundary tests; replace static email allowlisting and process-local tickets with server-derived membership, short-lived enrollment, revocation, and durable session fencing. |
| Legacy inventory | `0736007` | Reuse the content-free inventory shape, but replace its capped legacy ADC/schema runner before making any corpus-completeness claim. |
| Mixed runtime bounds | `b6724c6` | Split model/UI bounds from local capture and session orchestration before reuse. |
| Distributed provider concurrency | `29f63f4` | Preserve the bounded-work invariant, but replace the process-local semaphore with tenant/global distributed quotas and spend enforcement. |
| Suggestion and summary scheduling | `79638f7`, `f04cecd`, `00efb29`, `0b7abfb`, `d331a32`, `f98059a`, `a97ff20`, `05d024e`, `93b4163`, `50bef27`, `c59715e`, `4b280dd` | Port the bounded/coalesced behavior after durable transcript coverage and per-tenant job ownership exist. |
| Startup and transcript ordering | `8e7cc0a`, `d71ce6f` | Keep fail-closed readiness and deterministic dual-source ordering, but bind readiness to gateway runtime identity and order by protocol coverage rather than local callback time alone. |
| Terminal durability and deletion | `3f622f1`, `5443c02`, `35366bd`, `d158044`, `66591bf`, `15f561d` | Rebuild around durable transcript-or-gap uniqueness, forwarding journals, distributed workers, and idempotent deletion; in-memory fences/capabilities cannot be the production authority. |
| WebSocket lifecycle | `39fb0b6` | Retain terminal replay cleanup as a behavior, but key it to authenticated protocol streams and durable watermarks. The coherent bounded-fanout commit `254a22e` is classified separately above. |
| STT lifecycle | `19fd5be`, `de22296`, `7542854` | Retain audio-timeline mapping, fail-closed rejection, bounded drain, and zero-time handling; replace local byte queues with fenced attempts, durable forwarding acknowledgements, exact resend ranges, and transcript-or-gap coverage. |

### Class 3: superseded virtual-device or physical-gate work

| Commit | Disposition |
| --- | --- |
| `785cdff` | Discard from launch. It configures a local microphone device/channel inside the old process-owned capture path. |
| `228195c` | Preserve as historical deterministic-harness evidence only; do not use its BlackHole physical gate as native-launch evidence. |
| `229c736` | Preserve the consent-test idea, but discard this physical-gate implementation from the supported path. |

### Class 4: historical evidence or PR-specific CI lineage

These commits document or qualify the old mixed branch. They are useful for
provenance but are not source-extraction candidates:

`ab8fc90`, `4102ad1`, `37ca09d`, `7c1aacc`, `a93d5e4`, `9ce0b4e`,
`ea9b303`, `1473231`, `2624416`, `088d959`, `5cdcf00`, `d04f076`,
`918071c`, `1ce7630`, `f145ecf`, `8f63767`, `30bf99e`, `1544fa2`,
`ac3f942`, `310f25a`, `02ca17e`, `01790ba`, `6705947`, `d372005`,
`585c759`, `305914c`, `a279287`, `46b8223`, `d6cacfa`, `42d201e`,
`3605859`, `73f7322`, `afadaed`, `3292bf3`, `fd00ccf`, `3ffa74f`,
`5715079`, `6d3ab40`, `3881bcb`, `b08222c`, `9affcd0`, `4867c5a`,
`1ff1798`, `456d5e8`, `d119716`, `4fdaee6`, `009fb36`, `7694c7b`,
`df18fdb`, `1072d9d`, `f4e19d2`, `72bd7bf`, `3023f5d`, `9579e29`,
`e46e76b`, `d7ba4d5`, `257760c`, `dbe1c61`.

The CI commits `ab8fc90`, `009fb36`, and `f4e19d2` encode stacked-branch
qualification rules specific to Week 4. They must not be copied to a canonical
gateway branch. The action-runtime update in `fd48741` is separated above
because that maintenance behavior is architecture-independent.

## 4. Changed-path classification

Every one of the 77 changed paths is assigned below. A path marked Class 1 can
still contain implementation details that need a destination-tree review. A
mixed path is assigned the more conservative class.

### Class 1 paths

- `backend/llm/context_window.py`
- `backend/llm/gemini.py`
- `backend/llm/interview_prompts.py`
- `backend/tests/test_context_window.py`
- `backend/tests/test_gemini.py`
- `backend/tests/test_interview_prompts.py`
- `requirements.txt`
- `frontend/src/lib/authAdmission.test.ts`
- `frontend/src/lib/authAdmission.ts`

### Class 2 paths

- `.env.example`
- `.gitignore`
- `backend/auth.py`
- `backend/config.py`
- `backend/main.py`
- `backend/schemas/models.py`
- `backend/scripts/check_auth_setup.py`
- `backend/scripts/inventory_legacy_scope.py`
- `backend/scripts/soak_rotation.py`
- `backend/sessions/manager.py`
- `backend/sessions/reports.py`
- `backend/sessions/review.py`
- `backend/storage/deletion.py`
- `backend/storage/firestore.py`
- `backend/stt/stream_manager.py`
- `backend/tests/test_auth.py`
- `backend/tests/test_auth_matrix.py`
- `backend/tests/test_google_stt.py`
- `backend/tests/test_interview_reports.py`
- `backend/tests/test_legacy_inventory.py`
- `backend/tests/test_session_review.py`
- `backend/tests/test_soak_rotation.py`
- `backend/tests/test_startup_credentials.py`
- `backend/tests/test_stream_manager_drain.py`
- `backend/tests/test_stream_manager_rotation.py`
- `backend/tests/test_suggestion_scheduling.py`
- `backend/tests/test_suggestion_trigger.py`
- `backend/tests/test_summary_scheduling.py`
- `backend/tests/test_ws_handler.py`
- `backend/ws/handler.py`
- `firestore.indexes.json`
- `frontend/.env.example`
- `frontend/playwright.config.ts`
- `frontend/src/app/page.tsx`
- `frontend/src/components/AuthControls.tsx`
- `frontend/src/components/InterviewReportReview.tsx`
- `frontend/src/components/NoteChips.tsx`
- `frontend/src/components/RecentInterviews.tsx`
- `frontend/src/components/SessionControls.tsx`
- `frontend/src/components/SuggestionsPanel.tsx`
- `frontend/src/components/SummaryPanel.tsx`
- `frontend/src/components/TranscriptPanel.tsx`
- `frontend/src/hooks/useWebSocket.ts`
- `frontend/src/lib/auth.ts`
- `frontend/src/lib/firebase.ts`
- `frontend/src/lib/interviewReport.test.ts`
- `frontend/src/lib/interviewReport.ts`
- `frontend/src/lib/sessionReview.test.ts`
- `frontend/src/lib/sessionReview.ts`
- `frontend/src/lib/sessionStop.ts`
- `frontend/src/types/ws.ts`

### Class 3 paths

- `backend/audio/capture.py`
- `backend/scripts/physical_audio_gate.py`
- `backend/scripts/preflight_audio.py`
- `backend/tests/test_audio_capture.py`
- `backend/tests/test_physical_audio_gate.py`
- `backend/tests/test_preflight_audio.py`

### Class 4 paths

- `.github/workflows/ci.yml`
- `.github/workflows/deploy.yml`
- `.github/workflows/rollback.yml`
- `DEPLOY-SETUP.md`
- `README.md`
- `docs/current-state/documentation-and-config-status.md`
- `docs/current-state/week-4-auth-evidence.md`
- `docs/launch/preflight-checklist.md`
- `docs/launch/week-4-hosted-gate-checklist.md`
- `docs/privacy/2026-08-03-gcp-data-posture-checklist.md`
- `docs/superpowers/plans/2026-08-06-week-4-auth.md`

## 5. Dependency findings

### 5.1 Authentication is useful but not yet the gateway boundary

`backend/auth.py` verifies Firebase ID tokens with revocation checks, requires
verified and allowlisted email, validates audience/issuer, and derives the
organization from server configuration. Those are useful fail-closed
properties. The branch still uses one static organization and exact email
allowlist, initializes Firebase Admin after an ADC probe, and stores WebSocket,
stop, and extension capabilities in process memory. That is appropriate only
as evidence for an internal prototype. It does not provide durable enrollment,
membership lookup, active-lease fencing, multi-instance revocation, or a
companion credential lifecycle.

### 5.2 Session admission starts the discarded capture path

The session-create route creates the durable session and immediately starts
`_run_audio_pipeline`, whose documented implementation owns BlackHole system
audio plus microphone capture in the backend process. This is the central
reason the initial auth commit cannot be adopted as a gateway commit. In the
target architecture, a session-create request establishes authority and a
fenced capture lease; only a separately enrolled native companion can report
physical capture state or send audio.

### 5.3 STT work is algorithmically useful but protocol-incomplete

The stream manager adds bounded rotation buffering, audio-timeline offsets,
bounded drain, sticky incomplete state, callback-failure stop, and zero-time
final handling. It has no `audio.admitted`, durable `audio.forwarded` journal,
authoritative resend negotiation, attempt-independent coverage ID, or exact
transcript-or-gap terminal projection. Its bounded deque can overwrite the
oldest pending audio while recording only a process-local failure flag. The
configured overlap value is not used to run two provider streams; rotation
stops the current request and buffers until the response loop opens the next
one. Reuse these test cases when implementing the G2 attempt state machine,
not the current manager as the gateway contract.

### 5.4 Durability and deletion require distributed reimplementation

The branch usefully fences local callbacks, retries failed transcript child
writes, delays report generation until child durability, retains recovery
credentials until terminal persistence, and recursively deletes scoped child
records before writing a tombstone. The authority and fences are process
local, while the target gateway must tolerate restart, concurrent workers,
stale clients, and idempotent replay. The delete traversal is additionally
non-atomic: it can delete earlier children before discovering a later ownership
mismatch, and the GCS adapter swallows blob-deletion failure while allowing the
caller to proceed. Port the invariants into a durable, resumable state machine;
require incomplete/retryable status while any content remains; and preserve
the negative tests. Do not treat the local lock/set maps or success tombstone
as a production design.

### 5.5 The web changes remain request-authoritative

The auth controls and token propagation are reusable after endpoint and
enrollment adaptation. The small `authAdmission` generation guard and its
test are independently extractable. Session controls, WebSocket state, and
transcript panels still integrate with the old local session lifecycle: the
start handler calls `onSessionStart` after the HTTP response and the page sets
`isActive=true` before any companion event exists. The target UI must keep
`start`, `pause`, and `stop` pending until companion-originated events confirm
the physical boundary, and it must show independent source health and durable
gaps. None of those state-contract requirements is proved by PR #8.

## 6. Extraction plan after G2

No extraction begins during this audit. After the protocol closure artifact is
approved, use fresh focused branches in this order:

1. **Gateway identity and lease branch.** Reimplement Firebase/OIDC validation,
   server-derived membership, companion enrollment, revocation, and durable
   fencing. Port the auth matrix as behavior-driven negative tests.
2. **Gateway custody and STT branch.** Implement the three watermarks, bounded
   queues, attempt rotation, immutable content-free forwarding journal, exact
   resend ranges, and transcript-or-gap terminal uniqueness. Port the useful
   PR #8 drain and audio-timeline test cases.
3. **Tenant storage and deletion branch.** Reimplement scoped persistence,
   idempotent recursive deletion, audit tombstones, and restart/concurrency
   tests against the selected durable store.
4. **Model/report bounds branch.** Patch-port the Class 1 provider limits and
   the adaptable summary/report bounds after durable evidence inputs exist.
5. **Web identity and state branch.** Reuse token-handling ideas while adopting
   the companion-authoritative state and gap contract.

Each branch must start from its intended canonical baseline, limit its allowed
paths, run fresh checks, and receive exact-artifact review. PR #8 stays draft
and unchanged until replacement evidence makes closure as superseded safe.

## 7. Claim ceiling

This audit establishes a reviewable salvage map for one committed PR range.
It does not validate the dirty worktree deletions, any hosted environment,
Firebase configuration, ADC identity, Firestore rules/index deployment,
Google STT behavior, data deletion, native audio, physical routing, customer
data, or release readiness.
