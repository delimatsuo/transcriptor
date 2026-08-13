# ADR 0003: Native-Capture Launch Boundary

**Status:** Accepted for planning on 2026-08-13. This decision grants no
implementation, cloud, provider, device, pilot, release, or deployment
authority.

**Date:** 2026-08-13

**Decision owner:** Deli Matsuo

**Build roadmap:**
`docs/plans/2026-08-13-native-capture-launch-roadmap.md`

**Existing architecture:**

- `docs/architecture/0001-native-companion-cloud-stt.md`
- `docs/architecture/0002-companion-stream-protocol.md`
- `docs/privacy/data-flow-retention-contract.md`
- `docs/product/companion-web-state-contract.md`

## Decision

T.A.R.S. will not launch with BlackHole, VB-CABLE, PyAudioWPatch, or another
virtual-audio-device workflow as a supported capture dependency. Supported
capture must use native operating-system audio APIs through a companion that
speaks the protocol-0002 contract to an authenticated T.A.R.S. gateway.

The first product-validation milestone is a **limited native macOS pilot**
with a named cohort and supported-device matrix. It is not an Ella-wide or
customer launch. Because the intended Ella cohort includes Windows
recruiters, **broad Ella and customer launch require native Windows capture
parity** unless a later owner decision explicitly narrows the supported
cohort. The Windows implementation must use WASAPI directly and conform to
the same platform-neutral protocol; C#/NAudio is the current implementation
candidate, not a binding choice before its reviewed spike.

The existing Python and virtual-device capture path may remain temporarily as
an isolated development harness while native parity is built. It must not:

- appear in release onboarding, setup, readiness, support, or marketing;
- ship as an automatic or user-selectable fallback in a supported build;
- use real candidate data, production credentials, or a production endpoint;
- satisfy any native, device, pilot, or launch evidence gate; or
- become the rollback path for a native-capture release.

Once native replacement evidence covers the retained harness's necessary
development uses, the virtual-device path is removed rather than promoted.

## Why this decision is required

ADR 0001 already rejects mandatory BlackHole as the long-term product
experience. Virtual devices add an installation and routing dependency whose
most dangerous failure is silent: the application can appear to run while a
channel receives dead air or the wrong source. That is not an acceptable
commercial capture boundary.

The 2026-08-03 launch-scope document temporarily selected a local Python and
BlackHole/VB-CABLE launch to fit a four-to-six-week internal window. That
shortcut conflicts with the owner's current launch requirement: the product
will launch only on the intended architecture. This ADR supersedes that
document's launch sequencing and virtual-device launch gates. It does not
erase useful implementation evidence from the Week 1 through Week 4 branches.

## Required product boundary

### Native companion owns

- microphone and system-audio permissions;
- independent microphone and system-audio capture;
- device and route health, timestamps, framing, and per-source sequence;
- bounded in-memory raw-audio custody;
- authoritative physical capture, pause, stop, and degraded-state events;
- secure storage of short-lived enrollment credentials; and
- content-free diagnostics.

### Authenticated gateway owns

- server-derived user, organization, session, and stream authority;
- short-lived enrollment, renewal, revocation, and non-enumerating failures;
- one active fenced capture lease per session;
- schema, ordering, size, rate, duration, concurrency, and quota enforcement;
- bounded transient gateway custody and Google STT stream lifecycle;
- content-free forwarding journals and protocol watermarks;
- idempotent transcript-or-gap persistence;
- retention, deletion, audit, provider configuration, and kill controls; and
- content-free operational observability.

The companion never contains permanent Google Cloud credentials. macOS stores
allowed enrollment secrets in Keychain. A Windows companion uses Windows
Credential Manager or DPAPI-backed storage.

### Web workspace owns

- preparation, disclosure, and consent acknowledgement;
- capture commands without claiming that a requested action already happened;
- live transcript, notes, evidence review, and report approval;
- visible per-source health, degraded intervals, and durable gaps;
- retention visibility, deletion, and private export; and
- accessible Brazilian Portuguese setup, recovery, and finalization guidance.

Only companion-originated events may assert that physical capture started,
paused, resumed, stopped, or lost a source.

## Recruiter state contract required for pilot

The companion and web workspace must share one observable state model:

1. `setup_required`
2. `checking_permissions_and_devices`
3. `ready_both_sources`
4. `starting`
5. `recording`
6. `degraded`
7. `reconnecting`
8. `paused`
9. `stopping`
10. `finalizing`
11. `completed`
12. `completed_with_gaps`

Start fails closed until authentication, current disclosure acknowledgement,
permissions, and live health checks for both required sources pass. Recording
shows persistent per-channel health. Source loss never silently degrades into
a successful state. Pause and stop remain pending until the companion reports
its authoritative boundary. Finalization ends only when every known captured
range has one non-overlapping durable transcript or gap outcome; an unknowable
end boundary is reported honestly.

## Consent and candidate-data boundary

Audio admission requires a server-side pre-capture disclosure acknowledgement
containing the notice version, actor, session, time, and applicable legal
basis. A transcript cannot be the sole proof of consent because transcription
would already require capture. Refusal or revocation fails closed into a
no-recording workflow.

No candidate or other human audio may enter the native path until the
separately authorized hosted, device, integrated, privacy, and provider gates
pass. Generated or synthetic fixtures remain distinct from participant
consent.

## Delivery sequence

Gateway construction and strictly offline/simulator-only macOS work may run in
parallel after their respective plans and authority are approved. The
observable protocol semantics are shared and frozen before the tracks diverge:

- server-derived tenancy and fenced session authority;
- admission, provider-forwarding, and durable-transcript watermarks;
- raw-audio release only after contiguous provider forwarding;
- exactly one transcript-or-gap outcome per known coverage range;
- authoritative capture, pause, stop, and finalization state; and
- content-free logs and diagnostics.

Hosted integration is blocked until gateway authentication, authorization,
transient custody, provider configuration, cross-tenant negative tests,
retention/deletion, and kill controls pass. Physical-device evidence cannot
stand in for hosted evidence, and offline source evidence cannot stand in for
either.

After a qualified native macOS integrated path, a named and separately
authorized macOS pilot may begin. Native Windows parity then gates broad Ella
or customer launch.

## Existing-work disposition

### PR #8

PR #8 is frozen as a draft at
`754285480255de65f72b51a1ee529a6a7f95ba36`. It must not be readied, rebased,
force-pushed, retargeted, or merged before a read-only exact-head salvage
audit. The audit classifies each commit and dependency as:

1. architecture-independent and safely extractable;
2. reusable only after adaptation to the native/gateway boundary;
3. superseded physical-gate or virtual-device launch work; or
4. historical evidence only.

Coherent reusable units move to fresh, focused branches based on their
intended canonical baseline and receive fresh verification and exact-artifact
review. PR #8 is then closed as superseded. If safe extraction is impractical,
the required behavior is reimplemented narrowly instead of merging the mixed
PR.

### Native-companion N11D-C worktree

The dirty, partially staged N11D-C worktree remains preserved. It is not
resumed mechanically and is not launch evidence. A read-only audit must bind
its index, unstaged patch, baseline, guard assumptions, protocol compatibility,
and current macOS API choice before any new write. Reuse requires a fresh
approved plan, exact allowed paths, generated-fixture boundaries, full guards,
and renewed staff plus security/privacy review.

## Evidence gates

Evidence is labeled and may satisfy only its own layer:

| Layer | Minimum claim |
| --- | --- |
| Source/offline | Protocol, reducers, simulator, and bounded-memory contracts pass without network, credentials, devices, or human data |
| Hosted synthetic | Authenticated gateway accepts only allowlisted synthetic input in the exact isolated project and passes tenancy, custody, quota, provider, deletion, and kill-switch checks |
| Physical device | Native capture works on the named OS/device/route matrix with generated fixtures and no hosted dependency |
| Integrated synthetic | Exact companion, gateway, STT, and web artifacts complete long-running generated-fixture sessions with transcript-or-gap coverage |
| Consented pilot | Named recruiters complete authorized real workflows inside the explicitly limited cohort with rollback and incident controls |
| Broad launch | Native macOS and Windows, distribution/update, support, accessibility, privacy, security, and operational gates pass in the intended environment |

No lower layer implies a higher one. Any source, build input, protocol,
permission, provider, runtime, environment, or configuration change
invalidates dependent evidence.

## Consequences

### Positive

- Launch aligns with the already accepted commercial architecture.
- Recruiters do not install or route virtual audio devices.
- Capture truth comes from the process that owns the device.
- macOS and Windows share custody and recovery semantics.
- Permanent provider credentials are removed from recruiter machines.
- The product can fail visibly instead of silently losing interview audio.

### Costs

- The former four-to-six-week interim launch schedule is no longer credible.
- The authenticated gateway is a launch-critical workstream, not an optional
  hosted follow-up.
- Native Windows capture is required before broad launch for the known cohort.
- Existing Week 4 and N11D-C work must be audited before reuse.
- Packaging, signing, updates, accessibility, device matrices, and incident
  recovery become explicit release work.

## Superseded alternatives

- Mandatory BlackHole or VB-CABLE launch setup.
- PyAudioWPatch as a supported commercial Windows bridge.
- A local Python service with distributed Google service credentials as the
  launch security boundary.
- Treating an offline guard, CI result, source merge, or short device run as
  launch readiness.
- Calling a macOS-only pilot an Ella-wide or customer launch while Windows
  recruiters remain in the intended cohort.

## Revisit triggers

Revisit only through a new owner-approved ADR if:

- the supported launch cohort is formally narrowed to macOS-only;
- a supported OS cannot provide reliable native system-audio capture;
- privacy or customer requirements prohibit transient cloud STT;
- a measured local STT engine meets the same Portuguese quality, resource,
  and operational gates; or
- the protocol's custody or gap semantics must change.

Schedule pressure alone is not a revisit trigger.

## Approval boundary

This ADR records product and architecture direction. It authorizes the
documentation-only roadmap and read-only salvage audits. It does not authorize
source implementation, changes to either dirty worktree, cloud or provider
mutation, credential use, physical capture, ambient or human audio, candidate
data, pilot activity, push, PR mutation, merge, deployment, or release. Each
roadmap gate requires its own current authority and exact evidence.
