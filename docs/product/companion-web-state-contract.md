# Companion and Web State Contract

**Status:** Normative target; implementation blocked pending Phase 0 containment and panel re-review

**Date:** 2026-07-15

**Applies to:** macOS companion, web workspace, streaming gateway, live transcript, recruiter notes, assessment review, deletion, and export

## 1. Authority model

The macOS companion is the sole authority for physical audio-capture state because it owns the microphone and system-audio APIs. The server is authoritative for durable session, transcript, note, retention, deletion, and assessment state. The web workspace mirrors those states and sends commands; it never infers that physical capture changed merely because a button was clicked or a request was accepted.

Every state update includes a session ID, capture generation, monotonically increasing state version, source health, origin, and time. Clients ignore stale versions.

## 2. Capture state machine

The user-visible capture states are:

| State | Meaning | Allowed primary actions |
| --- | --- | --- |
| `consent_required` | Required disclosure has not been acknowledged | Review and acknowledge; cancel |
| `permissions_required` | Mic or system-audio permission is missing | Open permission guidance; cancel |
| `ready` | Consent, permissions, devices, auth, and session lease are valid | Start |
| `starting` | Companion is acquiring devices and opening streams; capture is not yet confirmed | Cancel |
| `active` | Companion confirms physical capture is active | Pause; stop |
| `pausing` | Pause was requested but the companion has not confirmed the boundary | Stop |
| `paused_finishing` | New capture has stopped; previously captured audio is still being forwarded/finalized | Resume; stop |
| `paused` | No new audio is captured and all pre-pause audio is represented by transcript coverage or gaps | Resume; stop |
| `reconnecting` | Capture continues into bounded memory while transport reconnects | Pause; stop |
| `buffering` | Capture continues but the queue is above its warning threshold | Pause; stop |
| `companion_unreachable` | The web cannot confirm the companion's physical state; capture may still be active | Use the companion to verify, pause, or stop |
| `degraded_gap` | Some audio coverage was lost or is untranscribed; capture may otherwise continue | Acknowledge; pause; stop |
| `stopping` | Stop was requested; waiting for companion capture boundary | None beyond emergency quit guidance |
| `finalizing` | Physical capture has stopped; pending text/gap events are becoming durable | Choose send or discard for unforwarded audio where applicable |
| `completed` | Every captured range has durable transcript coverage or a durable gap | Review; export; delete |
| `failed` | Capture or finalization cannot continue | Retry where safe; preserve text; delete |
| `deletion_pending` | Session deletion is running and content access is blocked | View deletion status |
| `deleted` | Required stores confirmed deletion; only content-free audit tombstone remains | None |

`active`, `reconnecting`, `buffering`, and `companion_unreachable` all mean physical capture may be occurring and use the same unmistakable recording indicator. `degraded_gap` never appears as a healthy state.

## 3. Start and consent

- Start is disabled until the applicable consent acknowledgement, permissions, authenticated ownership, device health, and session lease are valid.
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
- If unforwarded audio remains in memory, the user is told exactly how much is pending and may send it or discard it. Discarding creates a permanent exact-range gap.
- Closing the web tab does not stop physical capture. Before closing an active tab, the browser warns that the native companion will continue and points to the companion's stop control.
- Quitting the companion while active requires an explicit stop/discard decision where the OS permits. Forced termination is represented as a gap on recovery.
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

Synthetic fixtures are the default. Consented human audio is permitted only after the isolated environment, STT settings, authentication/ownership, and protocol gates pass.
