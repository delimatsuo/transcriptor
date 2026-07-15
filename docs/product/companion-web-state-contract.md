# Companion and Web State Contract

**Status:** Normative target; Phase 1A offline non-UI conformance passed at `9f3f3a0`. User-facing/native work and Phases 1B-1D remain blocked.

**Date:** 2026-07-15

**Applies to:** macOS companion, web workspace, streaming gateway, live transcript, recruiter notes, assessment review, deletion, and export

## 1. Authority model

The macOS companion is the sole authority for physical audio-capture state because it owns the microphone and system-audio APIs. The server is authoritative for durable session, transcript, note, retention, deletion, and assessment state. The web workspace mirrors those states and sends commands; it never infers that physical capture changed merely because a button was clicked or a request was accepted.

Every state update includes a session ID, capture generation, monotonically increasing state version, source health, origin, and time. Clients ignore stale versions.

## 2. Composite state model

Capture truth is not one flat state. Every authoritative update carries these independent axes:

| Axis | Minimum values | Authority |
| --- | --- | --- |
| Physical capture | `not_started`, `starting`, `active`, `pausing`, `paused`, `stopping`, `stopped`, `unknown` | Companion |
| Transport/connectivity | `offline`, `connecting`, `connected`, `reconnecting`, `unreachable` | Companion/server, mirrored by web |
| Microphone health | `unknown`, `healthy`, `permission_missing`, `permission_revoked`, `device_unavailable`, `overflow`, `failed` | Companion |
| System-audio health | Same values as microphone health | Companion |
| Transcript coverage | `none`, `current`, `buffering`, `degraded_known_gap`, `degraded_unknown_boundary`, `complete` | Server from protocol coverage ledger |
| Finalization | `not_started`, `pending_audio_choice`, `finalizing`, `completed`, `failed` | Companion for capture boundary; server for durable completion |
| Deletion | `not_requested`, `pending`, `partial_failure`, `deleted` | Server |

Consent, permissions, authentication, device readiness, and lease validity are start/resume gates, not substitutes for these axes. UI labels such as `ready`, `reconnecting`, `companion_unreachable`, `degraded_gap`, `finalizing`, and `completed` are derived presentations only; they never overwrite or hide the component truths.

Precedence rules:

1. If physical capture is `active`, `pausing`, `stopping`, or `unknown` after a previously active state, both companion and web retain the same prominent recording treatment. `unreachable`, source failure, buffering, or a gap never downgrades that indicator.
2. Each source failure is named independently. Total capture loss is distinct from one-source loss.
3. A known gap shows its proved range. A forced-termination boundary that cannot be proved shows `unknown coverage` and never claims an exact end.
4. Finalizing is visibly non-recording only after companion-confirmed `stopped` state. Server completion is separate from physical capture.
5. Deletion state never implies capture, transcript, or assessment state and cannot be represented by the same control.

Minimum derived presentations and actions:

| Presentation | Required component truth | Allowed primary actions |
| --- | --- | --- |
| `consent_required` | Start/resume consent gate invalid | Review fixture-use or participant disclosure as applicable; cancel |
| `permissions_required` | One or both required source permissions missing | Open permission guidance; cancel |
| `ready` | All start gates valid; physical capture `not_started` or `paused` | Start or resume |
| `starting` | Physical capture `starting` | Cancel |
| `active` | Physical capture `active` | Pause; stop |
| `reconnecting` / `buffering` | Physical capture may continue; transport or queue degraded | Pause; stop; inspect affected source/queue |
| `companion_unreachable` | Transport `unreachable`; physical capture `unknown` | Use companion/local kill control to verify or stop |
| `degraded_gap` | Coverage degraded while another axis may remain active | Inspect gap; pause; stop |
| `stopping` | Physical capture `stopping` | Local emergency-stop guidance; retry status check |
| `finalizing` | Physical capture `stopped`; finalization incomplete | Send/discard pending audio where available; retry |
| `completed` | Finalization `completed`; full transcript-or-gap accounting | Review; export; delete |
| `deletion_pending` / `deletion_failed` | Deletion `pending` or `partial_failure` | View scope/status; retry or contact support |
| `deleted` | Deletion `deleted` | None |

## 3. Start and consent

- Start is disabled until the applicable consent acknowledgement, permissions, authenticated ownership, device health, and session lease are valid.
- Phase 1A has no capture UI or deletion control. Its deletion behavior is conformance-harness simulation only.
- Phase 1C shows a persistent `Synthetic fixture test — no interview data` treatment in the companion and any test web surface. It provides no candidate, customer, participant, CV, JD, or recruiter identifier field.
- The Phase 1C acknowledgement attests that the controlled fixture-routing setup is active. It is explicitly labeled as fixture-use attestation, not proof of participant consent.
- Before fixture capture, the product warns that system capture can include unrelated application audio. The test starts only after the operator confirms the controlled route and immediate local kill control.
- The disclosure says that microphone and system audio are captured on the device and sent transiently to T.A.R.S. and Google Cloud STT. It does not say transcription is entirely on-device.
- The product reminds the recruiter that it does not replace their obligation to notify or obtain consent from interview participants.
- Clicking Start changes the UI to `starting`, not `active`.
- The global active indicator appears only after the companion confirms `capture.active`; the transcript timer uses that confirmed boundary.
- Failure to start returns to a truthful non-capturing state with an actionable error.

## 4. Pause and resume

- Clicking Pause emits a request and shows `pausing` until the companion confirms the last captured range.
- Once confirmed, the UI shows `paused_finishing` while pre-pause audio is still forwarding or finalizing. The label must read plainly, for example: “Paused — finishing audio captured before pause.”
- The state becomes `paused` only when no pre-pause raw audio remains and all captured ranges have transcript coverage or declared gaps.
- No audio acquired after the confirmed pause boundary may be forwarded as interview audio.
- Resume is a request. The state becomes `active` only after the companion confirms capture resumed.
- If consent version, permission, device, auth, or lease became invalid while paused, resume returns to the corresponding gate instead of capturing.

## 5. Stop and finalization

- Clicking Stop emits a request; it does not immediately claim capture stopped.
- The companion stops acquiring new audio, records the final sequence ranges, and confirms `capture.stopped`.
- After confirmation, the capture indicator changes to a clear non-recording finalization indicator.
- If unforwarded audio remains in memory, the user is shown the known pending duration/range, the action that will occur on timeout, and recovery guidance. `Send pending audio` explains that capture is already stopped but the pending bytes will leave the device. `Discard pending audio` requires destructive confirmation and says that the bytes are cleared immediately and the transcript will permanently show missing or unknown coverage.
- There is no silent default. If the choice times out or transport cannot recover, the companion keeps the bounded choice visible while memory is available; before OS termination it uses the locally configured fail-safe, clears memory, and records either the proved discarded range or an unknown-end coverage event on recovery.
- Discarding creates a permanent exact-range gap only when both boundaries are proven. Forced quit or process loss shows an honest unknown-coverage interval when the end boundary cannot be reconstructed.
- Closing the web tab does not stop physical capture. Before closing an active tab, the browser warns that the native companion will continue and points to the companion's stop control.
- Quitting the companion while active requires an explicit stop/discard decision where the OS permits. Forced termination is represented as proved gap coverage or unknown-boundary coverage on recovery.
- `completed` requires durable transcript-or-gap coverage for every captured range. It does not mean the assessment is approved.

## 6. Companion, browser, and multiple tabs

- The companion menu-bar/window indicator remains visible whenever capture may be active, even if no browser is open.
- All browser tabs subscribe to the same server state version. A second tab does not create another capture session or lease.
- Commands carry an expected state version and unique command ID. Conflicting or stale commands are rejected and reconciled to authoritative state.
- Only one companion capture lease may be active per session. A new companion cannot silently take over; transfer requires an explicit handoff and new capture generation.
- Browser disconnect changes browser connectivity, not capture state. The companion continues within the bounded-buffer contract and tells the user locally.
- Companion disconnect makes the web state `companion_unreachable` within a short approved timeout; the web UI never displays “paused” or “stopped” without confirmation.

## 7. Device and permission changes

- Losing either required source changes the UI immediately to degraded health and identifies the affected source.
- Automatic device switching is allowed only when it preserves the user's selected intent and is visibly announced. Otherwise capture pauses for that source.
- Permission revocation creates an exact coverage gap from the last confirmed sample and blocks resume until restored.
- Headset or route changes are audit metadata without participant names or transcript content.
- Mixed system audio is disclosed: sounds from unrelated applications may enter the system-audio stream unless OS-level selection can reliably restrict them.

## 8. Transcript coverage and exclusions

- Interim text is visually distinguishable from durable final text.
- Each final segment exposes source, time range, correction state, and whether it is included in assessment.
- A gap is rendered at its exact time range and cannot be hidden by transcript reflow.
- “Exclude from assessment” prevents selected content from downstream suggestion/report context. It does not delete the transcript or erase the audit fact that an exclusion occurred.
- Delete is a separate destructive operation governed by the deletion contract.
- Speaker correction changes attribution without rewriting the quoted words or original evidence record.

## 9. Recruiter-note synchronization

Every note and bookmark has a stable ID, author, creation time, last-edit time, transcript-relative timestamp, and version. The UI exposes these states:

- `pending`: stored in the encrypted local text outbox but not yet durable in the cloud;
- `synced`: durably acknowledged at the displayed version;
- `failed`: retry stopped and user action is required;
- `conflict`: the server and local copy diverged and neither is silently overwritten;
- `deleted_pending`: deletion mutation is not yet durable.

The user can retry, copy, or resolve failed/conflicted notes. A short undo window creates a new version; it does not erase audit history. Closing a tab must not silently lose pending text.

## 10. Assessment and hiring safeguards

- Candidate statements, recruiter notes/ratings, and AI inference use distinct labels and data fields.
- Every material rating or recommendation links to transcript evidence and/or an attributed recruiter judgment; otherwise it is `insufficient_evidence`.
- Excluded passages are not sent to new assessment jobs.
- The UI shows model/template version, generation time, evidence links, and approval status.
- Recruiter edits never overwrite the original candidate quote or AI suggestion.
- Final approval is an explicit human action with actor and time. T.A.R.S. does not automatically rank, advance, or reject a candidate in the initial scope.

## 11. Retention, export, and deletion

- The session shows its effective retention date and policy source.
- Transcript, notes, and approved report can be exported separately.
- Export generation and download each require current authorization and produce content-free audit events.
- Delete describes the scope before confirmation and immediately blocks new reads, exports, and AI jobs after acceptance.
- `deleted` appears only after required storage and derived indexes confirm deletion. Partial failure remains `deletion_pending` with a retryable status.
- A partial failure names the affected storage class without disclosing content, preserves the content-free audit tombstone, and offers retry plus support guidance. It never renders the terminal `deleted` treatment.
- No real deletion control appears until its authorization, partial-failure, retry, and tombstone behavior is implemented. Phase 1A may exercise only state-machine simulation.
- Assessment exclusion, transcript correction, retention expiry, and deletion are never represented as the same action.

## 12. Accessibility and language

- Capture state is conveyed by text, icon, and accessible announcement; color alone is insufficient.
- Start, pause, resume, stop, gap, note-sync failure, and delete controls have keyboard access and clear focus order.
- VoiceOver announces capture-state changes, source failures, gaps, and destructive confirmations without repeatedly interrupting transcript review.
- Timers and status updates avoid excessive live-region chatter.
- All user-facing strings are localizable. Initial product verification covers English and Brazilian Portuguese, including consent, privacy, error, retention, and deletion copy.
- Shortcuts are discoverable and do not conflict with common browser or macOS accessibility commands.

## 13. UX conformance gates

Before internal human audio testing, automated and manual tests must prove:

- no screen claims active, paused, stopped, completed, or deleted before the authoritative event;
- browser close, multi-tab use, companion disconnect, device switch, and permission loss remain truthful;
- pause and stop boundaries match protocol sequence ranges;
- gaps are exact, persistent, accessible, and included in final review;
- pending, failed, and conflicted notes cannot disappear silently;
- assessment provenance and human approval remain visible;
- English and Brazilian Portuguese critical flows pass accessibility review.

Before Phase 1C exits, task-based English and Brazilian Portuguese tests cover permission approval/denial/revocation, one-source and total loss, reconnect and overflow, stop while offline, pending send/discard, browser close, companion unreachable, forced quit/relaunch, and deletion partial failure. VoiceOver and keyboard-only tests verify focus restoration after permission dialogs, restrained live-region announcements, shortcut compatibility, and state communication without color. Multi-window tests cover browser closure, multiple tabs, foreground meeting applications, menu-bar visibility, lock/wake, and device changes. Participants must correctly answer whether capture is active, which source failed, what may be missing, whether closing the browser stops capture, what discard removes, and whether deletion is complete.

Synthetic fixtures are the default. Consented human audio is permitted only after the isolated environment, STT settings, authentication/ownership, and protocol gates pass.
