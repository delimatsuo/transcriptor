# Native-Capture Launch Roadmap

**Status:** Accepted sequencing plan with an owner-authorized G2-A0
documentation amendment candidate. Documentation and read-only audits only
remain in scope until the four-document amendment receives renewed exact-tree
architecture and security/privacy approval. Every implementation and live-effect
gate retains its own authority boundary.

**Date:** 2026-08-13

**Governing decision:**
`docs/architecture/0003-native-capture-launch-boundary.md`

## 1. Outcome

Deliver T.A.R.S. as an executive-search interview companion whose supported
capture path uses native macOS and Windows APIs, sends transient audio through
an authenticated T.A.R.S. gateway to Google Cloud STT, visibly accounts for
every captured range, and keeps the existing web preparation, notes, review,
human approval, and client-report workflow.

The first validation milestone is a named native macOS pilot. Broad Ella or
customer launch follows native Windows parity. Neither milestone depends on a
virtual audio driver.

## 2. Current gap

- Protocol 0002 has offline conformance evidence, but protocol-v2 framing,
  chunk/buffer, rotation, fencing-store, forwarding-journal, terminal-release,
  and deletion-quiescence choices require the G2-A0 amendment and later source
  evidence.
- The active macOS lineage has qualified offline components through N11D-B and
  a partially committed N11D-C lineage, plus a large dirty, partially staged
  V1-to-V2 cutover that is not qualified.
- Real native capture, permissions, packaging, signing, and update behavior
  are not qualified.
- The authenticated gateway assumed by ADRs 0001 and 0002 does not exist.
- Native Windows capture has analysis but no implementation or evidence.
- PR #8 contains substantial auth/privacy/durability work mixed with local and
  virtual-device launch assumptions.
- The web product does not yet have integrated, companion-authoritative state
  and gap behavior on the target architecture.

## 3. Non-goals

- No BlackHole, VB-CABLE, PyAudioWPatch, or virtual-device release path.
- No real candidate/customer data during source, hosted-synthetic,
  physical-fixture, or integrated-synthetic gates.
- No on-device STT requirement for the first native release.
- No provider-specific meeting extension as a capture dependency.
- No voiceprint, biometric speaker identification, meeting bot, or video/screen
  retention.
- No deployment, dogfood, pilot, or launch claim based on this plan alone.
- No automatic reuse, staging, cleanup, or overwrite of dirty worktrees.

## 4. Workstream ownership

| Workstream | Owns | Must not own |
| --- | --- | --- |
| Protocol | Versioned messages, identities, watermarks, coverage, gaps, compatibility | Platform capture or cloud identity implementation |
| Gateway | Enrollment, tenancy, leases/fencing, quotas, transient custody, STT, persistence, deletion, audit, kill switch | Physical capture or UI claims |
| macOS companion | Permissions, mic/system capture, health, bounded buffer, protocol client, Keychain, updates | Session tenancy or report authority |
| Windows companion | WASAPI mic/system capture, health, bounded buffer, protocol client, secure token storage, updates | Session tenancy or report authority |
| Web workspace | Preparation, disclosure, commands, notes, visible state/gaps, review, approval, export | Claiming physical device state without companion proof |
| Release/operations | Artifact provenance, signing, rollout, monitoring, rollback, support matrix | Weakening product or privacy gates to meet a date |

One writer owns a branch/index at a time. Exact-artifact reviewers remain
read-only, and the reviewed tree remains frozen.

## 5. Gate sequence

### G0: Decision adoption and preservation

**Purpose:** Establish one launch boundary without losing recoverable work.

**Required work:**

- Adopt ADR 0003 and mark the 2026-08-03 interim launch sequencing historical.
- Freeze PR #8 at
  `754285480255de65f72b51a1ee529a6a7f95ba36` as draft/non-promotable.
- Preserve the N11D-C worktree's staged and unstaged state exactly.
- Remove virtual-device physical qualification from active launch planning.
- Record separate evidence labels and authority boundaries.

**Exit:** Documentation is internally consistent, exact-artifact reviewed, and
committed on a documentation-only branch. No implementation or external state
changes.

### G1: Read-only salvage and dependency audits

**Purpose:** Reuse verified behavior without inheriting the discarded launch
architecture.

**PR #8 audit:**

- Bind exact base, head, commit graph, checks, file list, and dependency edges.
- Classify every commit and changed path as independent, adaptable,
  superseded, or historical.
- Specifically challenge distributed credentials, local capture coupling,
  virtual-device assumptions, physical-gate claims, ownership derivation,
  deletion, STT rotation/drain, reports, and frontend state.
- Recommend fresh focused extraction, narrow reimplementation, or discard.
- Do not mutate the PR or its branch during the audit.

**N11D-C audit:**

- Bind HEAD, index tree, unstaged binary patch, untracked protected paths, and
  guard baselines without modifying them.
- Determine whether the V2 cutover still matches protocol 0002 and ADR 0003.
- Resolve the target macOS system-audio API and supported OS floor before N11E
  planning.
- Identify the smallest coherent salvage unit and required allowed-path plan
  amendment.

**Exit:** Independent staff and security/privacy review agree on explicit
salvage maps. No extraction or implementation begins inside G1.

### G2: Protocol closure

**Purpose:** Prevent gateway and companion tracks from drifting.

**Resolve and version:**

- binary framing and schema language;
- maximum chunk duration and bytes;
- measured per-source memory bound;
- STT attempt rotation behavior;
- capture lease/fencing persistence;
- content-free forwarding-journal persistence;
- client/gateway version compatibility and fail-closed upgrade rules;
- platform-neutral capture health and lifecycle events; and
- exact pre-capture disclosure acknowledgement.

The source/offline quota defaults are explicit: per source, 50 audio events/s
with a 100-event burst, 192,000 payload bytes/s with a 384,000-byte burst, and
205,000 metadata-plus-prefix bytes/s with a 410,000-byte burst; a two-source
session doubles those budgets. Tenant and process ceilings, pending
pre-authentication handshakes, aggregate receive buffers, resident custody,
and provider-attempt reservations are enforced separately and fail closed when
their shared authority is unavailable. These are not hosted capacity or spend
evidence.

Protocol semantics that remain fixed are server-derived authority, the three
watermarks, `audio.forwarded` as the only successful provider-forwarding
release watermark; a discard CAS that wins before provider preparation creates a
durable gap, while named `local_privacy_discard`, durable discard, and
emergency/privacy-timeout zeroization never attribute an already-pending effect
to discard and preserve the original forwarded/ambiguous outcome. These are
terminal privacy releases with visible gaps, fencing, idempotency,
transcript-or-gap terminal coverage, deletion quiescence with late-callback
fencing, and content-free logging. These v2 semantics become operative only
after the G2-A0 amendment is approved at one exact documentation tree.

### G2-A0: governing-artifact amendment

**Entry:** Owner authorization for a documentation-only amendment to ADR 0002,
ADR 0003, this roadmap, and the privacy contract. No source, hosted, provider,
device, credential, capture, deployment, merge, or release action is included.

**Required result:** The four documents must agree that admission is not a
release, `audio.forwarded` is the only successful provider-forwarding watermark,
and the named local `local_privacy_discard`, `audio.discard.durable`, plus local
emergency/privacy-timeout zeroization follow the per-range discard CAS: a claim
winning before provider preparation creates an exact or honest unknown-end gap;
an already-pending effect remains with its original owner as forwarded or
ambiguous. None attributes that effect to discard or claims provider forwarding.
The documents must also agree on companion physical state versus gateway
transport/coverage state, `delete_quiescing`/`deleting` sequencing, positive
provider-effect quiescence, and late-callback fencing.

The corrected amendment must additionally bind ordered disjoint forwarding
intervals (a later forwarded range cannot skip an unresolved earlier gap), a
full ordered atomic-coverage-list terminal identity, and an explicit
`effect_quiescence_required` non-success state with a runtime-epoch/egress-fence
recovery rule. Protocol 0002 must classify frozen v2 invariants separately from
implementation-defined choices. The privacy contract is a governing artifact,
and its promise is limited to absence of T.A.R.S.-controlled durable raw-audio
artifacts; allocator, OS, crash, transport, and provider retention surfaces
remain later evidence gates.

**Exit:** One exact commit/tree plus the review record receives independent
architecture and security/privacy approval with no unresolved P0/P1. A passing
G2-A0 makes the later G2 source corridor eligible for a separate direct
authorization; it does not authorize source implementation. The G3C
product-state/privacy UX reconciliation remains a separately versioned,
reviewed docs-only artifact and UI gate.

### G2-A: v2 schema and source/offline implementation

**Entry:** Approved G2-A0 exact tree plus a new direct source-implementation
authorization naming the exact allowed paths.

**Exit:** Swift, Python, and future Windows binding vectors pass twice with
network denied, bounded long-duration fixtures, crash points, and clean
artifact scans. Protocol closure is source/offline evidence only.

### G3A: Minimal gateway, isolated and synthetic

**May run in parallel with G3B after G2 interfaces are frozen.**

**Build:**

- short-lived revocable enrollment and renewal;
- server-derived user and organization membership;
- server-side enforcement of a current pre-capture disclosure acknowledgement
  bound to notice version, actor, session, time, and legal basis;
- authorization on REST, WebSocket, audio, transcript, notes, reports,
  exports, storage, and deletion;
- one fenced capture lease and stale-client rejection;
- bounded transient queues with no T.A.R.S.-controlled durable raw-audio
  artifact beyond the approved forwarding or terminal privacy-release
  semantics; no physical-erasure claim is made until memory, crash, transport,
  swap, and provider-retention evidence passes;
- size, rate, duration, concurrency, distributed ingress, authentication, and
  spend controls;
- Google STT streaming and rotation;
- content-free forwarding journal and exact watermarks;
- idempotent durable transcripts and gaps;
- retention/deletion and content-free audit tombstones;
- non-enumerating failures, least-privilege runtime identity, and kill switch;
  and
- content-free logs, metrics, crash reports, and diagnostics.

**Verification:**

- reject unauthenticated, expired, revoked, replayed, wrong-audience,
  cross-tenant, stale-fence, conflicting-session, oversized, out-of-order, and
  unsupported-version requests;
- reject audio when disclosure acknowledgement is absent, stale, revoked, or
  mismatched by notice version, session, actor, organization, or legal basis;
- prove no admission acknowledgement releases client audio;
- prove forwarding advances only across contiguous journaled ranges;
- inject failure around provider write, journal, transcript, deletion, and
  reconnect boundaries;
- scan storage, logs, telemetry, crash artifacts, and backups for fixture
  payloads; and
- exercise the kill switch and rollback without redeployment;
- prove provider stream cancellation, deployment egress fencing, runtime-epoch
  ownership, and non-success `effect_quiescence_required` behavior; and
- scan allocator/parser/HMAC/TLS, swap/core/crash, diagnostics, caches, logs,
  backups, and provider-retention surfaces for raw-audio artifacts.

**Exit:** Source/offline gateway evidence. Hosted activation remains G4.

### G3B: macOS companion, simulator and controlled fixtures

**May run in parallel with G3A after G1 and G2 pass.**

**Build:**

- finish or replace the audited V2 route work without retaining V1
  compatibility merely to satisfy the old guard;
- use the explicitly selected native system-audio API and supported OS floor;
- capture microphone and system audio independently;
- implement permission denial/revocation, device changes, sleep/wake, and
  route recovery;
- implement bounded memory, provider-forwarding release plus durable-discard and
  emergency/privacy-timeout gap semantics, overflow gaps, pause, stop,
  finalization, deletion quiescence, and immediate local kill control;
- store only short-lived enrollment material in Keychain;
- expose content-free per-source health and companion-authoritative states;
  and
- package no virtual-device runtime path, whether automatic or manually
  selectable.

**Verification:**

- first run deterministic simulator and reducer/fault matrices;
- then use separately authorized, generated, controlled audio fixtures with
  networking and providers disabled;
- run 90-to-120-minute device-route cases, permission changes, device loss,
  buffer exhaustion, forced termination, and artifact scans;
- run VoiceOver, keyboard-only, 200-percent zoom, focus restoration, and pt-BR
  comprehension checks; and
- inspect the release candidate to prove it contains no virtual capture
  activation, onboarding, support, or rollback entry.

**Exit:** Physical-device fixture evidence on the named Mac matrix. No hosted
or real-audio claim.

### G3C: Recruiter state and web integration contract

**May be designed in parallel; implementation integrates only against the
versioned protocol. Entry additionally requires a docs-only reconciliation of
`docs/product/companion-web-state-contract.md` with the approved v2 precedence
table and `local_privacy_discard` UX. This G2-A0 amendment does not update that
product contract or authorize UI/source work.

The same G3C artifact must reconcile the existing candidate disclosure script's
v1 “audio is not recorded or stored” wording with the amended bounded privacy
claim, exact notice version/locale, legal-basis acknowledgement, refusal path,
and the distinction between T.A.R.S.-controlled durable artifacts and provider
or operating-system retention surfaces. Until that reconciliation is reviewed,
the old script is not launch or pilot evidence.

- Implement the ADR 0003 state model with independent physical capture,
  transport, source health, coverage, finalization, and deletion axes; do not
  assign gateway finalization or completion to companion events.
- Block start until gateway enrollment, current disclosure acknowledgement,
  permissions, and both required sources are healthy.
- Show persistent microphone and system-audio health with icon-plus-text, not
  color or sound alone.
- Treat web start/pause/resume/stop actions as requests until companion events
  confirm them.
- Make degraded ranges and gaps visible and non-editable.
- Explain send-versus-discard consequences, show terminal privacy gaps, and
  show truthful browser-close warnings and companion stop/quit guidance during
  unresolved finalization or deletion quiescence; do not treat a browser-close
  block as a deletion or capture guarantee.
- Preserve the report distinction: AI draft remains internal; only an
  explicitly approved client projection may be exported.

**Exit:** A separately versioned docs-only state/privacy UX reconciliation has
an exact commit/tree, Product UI/UX and accessibility review, and synthetic
fault/copy evidence covering English and pt-BR, keyboard/screen reader,
multi-tab/reopen, local discard versus `effect_pending`, deletion retry, and
browser/companion disappearance. This does not prove device or hosted behavior.

### G4: Hosted synthetic gateway qualification

**Entry:** G3A source evidence, current isolated-project attestation, exact
runtime identity and configuration, approved quotas, secrets populated through
an authorized channel, protected activation approval, and tested rollback.

- Deploy only to the named synthetic environment under a separately approved
  mutation set.
- Accept only server-issued allowlisted synthetic fixtures.
- Verify private authenticated ingress, least privilege, provider settings,
  quotas, costs, logs, deletion, and kill controls through independent
  readback.
- Repeat the disclosure-acknowledgement rejection matrix against the deployed
  gateway.
- Prove zero public, cross-tenant, implicit-project, or unexpected-content
  acceptance.

**Exit:** Hosted-synthetic evidence. It does not authorize native device audio.

### G5: Integrated native macOS synthetic qualification

**Entry:** G3B physical-fixture, G3C recruiter-state, and G4 hosted-synthetic
gates all pass on compatible protocol, gateway, companion, STT, and web
artifact versions.

- Run the exact macOS companion through the exact gateway, Google STT, and web
  workspace using approved generated fixtures.
- Cover 60- and 90-minute flows, reconnect, rotation, permission loss, route
  change, source loss, gateway restart, provider failure, overflow, pause,
  stop, discard, finalization, and deletion.
- Require one non-overlapping terminal transcript or visible gap for every
  captured range and zero persistent raw-audio artifacts.
- Prove the exact companion sends no audio when disclosure acknowledgement is
  missing, stale, revoked, or mismatched, and the gateway rejects any injected
  attempt.
- Drill enrollment revocation, active-session kill, client-version rejection,
  and rollback without re-enabling virtual capture.

**Exit:** Integrated-synthetic evidence and independent exact-artifact review.

### G6: Limited native macOS pilot

**Entry:** A new direct authorization naming recruiters, devices, duration,
data scope, environment, incident owner, rollback operator, and stop criteria;
all privacy, provider, retention/deletion, accessibility, distribution, and
support checks pass. Before entry, approve numeric thresholds for independent
setup completion time, visible gap rate and duration, reconnect time,
finalization time, recovery success, and support burden. G3C, G4, and G5 must
still pass on the exact pilot-compatible gateway, companion, STT, and web
versions.

- Begin with at least two named Mac recruiters on supported hardware.
- Use transparent pre-capture disclosure and a no-recording fallback.
- Require every named recruiter to complete the full consented workflow
  independently: install/update, enroll, permissions, live two-source health
  check, disclosure, capture, notes, pause/resume, recovery, stop,
  finalization, visible-gap review, human-approved report, deletion, and
  uninstall/rollback.
- Require every named recruiter to identify recording, degraded, paused,
  finalizing, and gap states correctly in a moderated check.
- Require every predeclared setup, gap, reconnect, recovery, finalization, and
  support-burden threshold to pass.
- Stop on silent channel loss, unauthorized access, retained raw audio,
  unbounded custody, failed deletion, misleading state, or rollback failure.

**Exit:** Every required recruiter workflow and numeric threshold passes, with
zero silent loss, false healthy state, unauthorized access, retained raw
audio, or failed deletion. Evidence remains limited to the named cohort. Do
not call it an Ella-wide or customer launch.

### G7A: Native Windows qualification

**Entry:** Reviewed Windows spike plan, named Windows 11 machines and meeting
apps, supported build floor, distribution/update plan, and the same protocol
version used by macOS. Offline source and physical-device work may begin after
G2. The integrated portion and exit additionally require G3C and G4 to pass on
compatible exact gateway, companion, STT, and web artifact versions.

- Implement native WASAPI microphone and system-audio capture. C#/NAudio is
  the default candidate; justify any alternative through review.
- Handle silent render, endpoint changes, headset switching, exclusive-mode
  detection, whole-system contamination, keepalive cadence, and secure token
  storage.
- Reuse the same custody, watermarks, gaps, capture states, pt-BR terminology,
  and web workflow.
- Run a 90-to-120-minute matrix on named recruiter machines, plus Narrator,
  keyboard, 200-percent zoom, packaging, update, uninstall, and rollback.
- Inspect the Windows release candidate to prove it contains no virtual
  capture activation, onboarding, support, or rollback entry.

**Exit:** Windows physical and integrated synthetic evidence equivalent to
macOS. No human-data or broad-launch claim.

### G7B: Limited native Windows pilot

**Entry:** A new direct authorization names the Windows recruiters, machines,
meeting apps, duration, data scope, incident owner, rollback operator, and stop
criteria. G7A passes, and numeric thresholds are approved for independent
setup completion time, visible gap rate and duration, reconnect time,
finalization time, recovery success, and support burden. G3C and G4 must still
pass on the exact pilot-compatible gateway, Windows companion, STT, and web
versions. Current privacy, provider, retention/deletion, accessibility,
distribution, support, rollback, incident, and legal/disclosure checks must
pass before consented audio.

- Begin with at least two named Windows recruiters on representative supported
  Windows 11 configurations.
- Run the complete consented workflow: install/update, enroll, permissions,
  live two-source health check, disclosure, capture, notes, pause/resume,
  recovery, stop, finalization, visible-gap review, human-approved report,
  deletion, and uninstall/rollback.
- Require every named recruiter to complete that workflow independently.
- Require every named recruiter to identify recording, degraded, paused,
  finalizing, and gap states correctly in a moderated check.
- Require every predeclared numeric threshold to pass with zero silent loss,
  false healthy state, unauthorized access, retained raw audio, or failed
  deletion.

**Exit:** Consented Windows-pilot evidence limited to the named cohort. It
cannot be generalized beyond the qualified OS, hardware, meeting-app, gateway,
and artifact matrix.

### G8: Broad Ella and customer launch

**Entry:** G6 and G7B pass; native macOS and Windows parity, supported artifact
signing and updates, incident response, support ownership, privacy/legal
review, provider contracts and settings, production deployment/rollback
approval, and launch acceptance are bound to exact artifacts. Release
thresholds for setup, gap rate/duration, reconnect, recovery, finalization,
deletion, approved-report completion, and support burden are approved before
entry.

Success requires:

- no supported virtual-device dependency or fallback;
- zero silent source failures in the release matrix;
- server-derived ownership and no cross-tenant access;
- no raw-audio persistence by default;
- complete transcript-or-gap coverage and truthful state;
- every predeclared product and reliability threshold passes on both supported
  platforms; truthful visible gaps do not excuse an excessive gap rate;
- enforced retention/deletion with audit evidence;
- accessible Brazilian Portuguese workflows on both platforms;
- every externally delivered report explicitly human-approved; and
- a tested operational kill switch and rollback that preserve the native-only
  boundary; and
- release-artifact inspection for both platforms proves there is no virtual
  capture activation, onboarding, support, or rollback entry.

## 6. Evidence matrix

| Gate | Source/offline | Hosted | Physical device | Integrated | Human data | Production claim |
| --- | --- | --- | --- | --- | --- | --- |
| G0-G2 | Allowed | No | No | No | No | No |
| G3A | Allowed | No | No | No | No | No |
| G3B | Allowed | No | Controlled fixtures only | No | No | No |
| G4 | Required | Synthetic isolated environment | No | Gateway/provider only | No | No |
| G5 | Required | Required | Generated fixtures | Required | No | No |
| G6 | Inherited | Inherited | Named Macs | Required | Separately consented and authorized | Named pilot only |
| G7A | Required | G4 required before integrated exit | Named Windows machines | G3C and G4 required | No | Windows source qualification only |
| G7B | Inherited | Qualified exact gateway required | Named Windows machines | Required | Separately consented and authorized | Named Windows pilot only |
| G8 | Required | Required | Both supported platforms | Required | Authorized launch workflows | Only after explicit release approval |

## 7. Parallelism and critical path

After G1 and G2, the minimal gateway (G3A), offline macOS work (G3B), and web
state contract (G3C) may proceed in parallel on isolated branches with disjoint
ownership. The critical path then converges:

`G4 hosted gateway -> G5 integrated Mac -> G6 limited Mac pilot`

`G2 protocol closure -> G7A offline/physical Windows work`

`G3C recruiter state + G4 hosted gateway + G7A device evidence -> G7A integrated exit -> G7B limited Windows pilot`

Both paths converge at `G8 broad launch`.

Native Windows work may begin after protocol closure and can overlap later Mac
qualification, but **G7A and G7B remain required before G8 broad launch**.

Parallel work never allows hosted integration to outrun gateway security or
device work to use human audio before its explicit gate.

## 8. Verification and review rules

- Every gate records repository/worktree, base, HEAD, dirty state, exact paths,
  data class, environment, account/resource, allowed effects, rollback, and
  stop conditions.
- Any source or relevant configuration change invalidates dependent evidence.
- Full affected guards run before consequential commit or promotion.
- Staff engineering and security/privacy independently review gateway,
  protocol, companion, integration, and release candidates.
- Product UI/UX and accessibility review every change to recruiter state,
  consent, recovery, gaps, finalization, and report approval.
- Review applies only to the exact tree/artifact supplied. A changed artifact
  requires renewed evidence and review.
- CI green is source evidence only. It is never device, hosted, pilot, or
  production proof.

## 9. Stop conditions

Stop the active gate if:

- BlackHole, VB-CABLE, PyAudioWPatch, or another virtual-device path becomes a
  supported or automatic fallback;
- permanent provider credentials reach a companion or recruiter machine;
- audio is admitted before server-derived authority and current disclosure;
- raw audio reaches disk, logs, telemetry, crash reports, backups, or a text
  outbox;
- a source is lost while the product continues to claim healthy capture;
- transcript/gap coverage overlaps, duplicates, or silently omits a range;
- the environment, project, identity, provider configuration, quota, fixture,
  branch, or artifact differs from the reviewed target;
- a dirty/protected worktree is modified outside its explicit plan;
- required rollback or kill controls fail; or
- evidence from one layer is presented as proof of a higher layer.

## 10. Immediate next actions

1. Complete and independently review this documentation-only reset.
2. Run the read-only PR #8 salvage audit at its frozen exact head.
3. Run the read-only N11D-C index/unstaged/API-choice audit.
4. Present the two salvage maps and a protocol-closure plan for approval.
5. Only then begin separately authorized G2/G3 implementation on isolated,
   clean branches.

No current step authorizes real audio, candidate data, cloud activation,
provider calls, deployment, PR #8 mutation, or edits to the N11D-C worktree.
