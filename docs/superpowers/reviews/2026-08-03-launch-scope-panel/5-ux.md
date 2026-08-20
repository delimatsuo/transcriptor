# UX Lead — T.A.R.S. Launch Scoping Report

## 1. Launch surface scope

**Recommendation: launch = excellent live screen (exists) + minimal live note capture + post-session evidence-linked report. Coverage is the fast-follow, not a launch surface.**

Ground truth from the repo:

- The live screen after PR #3 is genuinely good and matches the founding feedback: single hero question with staleness derived from server sequence numbers, queue chip, collapsed transcript sheet, tokens, pt-BR (`frontend/src/components/views/InterviewLiveView.tsx`, `frontend/src/lib/interviewQueue.ts`, `frontend/src/components/HeroQuestion.tsx`, `frontend/src/components/TranscriptSheet.tsx`). Capture status + transcript surfaces exist. Don't touch this except to add note chips.
- The labs are **contracts, reducers, and scripted demos — not shippable UI**. `RecruiterWorkspaceLab.tsx` replays 9 predeclared fixture events ("There is no free-form session, content, source, policy, or event input") and its own footer reserves "authentication, durable storage, network behavior… localization, multi-window behavior, deployment, and product readiness" as unresolved (`build/worktrees/integrated-recruiter-workspace-lab/frontend/src/components/RecruiterWorkspaceLab.tsx`). Their real value is that the *domain models are already designed and tested* — the UI must still be built, in pt-BR (all lab chrome is English).
- Coverage specifically: the 3-axis contract and projector are excellent (`build/worktrees/interview-coverage-lab/frontend/src/interview-coverage/contract.ts`, `projector.ts`), but the live projection runs off a **synthetic oracle** — the entire LLM judging backend doesn't exist, and the spec review recorded six critical findings against live coverage, including that judging depends on speaker attribution live-tested broken on 2026-08-01 and an unguarded untrusted JD input (`docs/superpowers/specs/2026-08-02-live-interview-ui-design.md`, "Deferred: coverage model"). By the glance test, a coverage rail whose states are wrong is worse than no rail — it erodes trust in the one screen that currently works. Coverage cannot responsibly make a 4–6 week launch.

Notes and the report, by contrast, are recruiter-authored — no LLM judgment on the critical path for notes, and the report model makes AI inference a clearly labeled, editable layer. They also deliver the two oldest unmet founding needs: post-session review and a report.

## 2. Notes UX for live interviews

**Core insight: capture the pointer live, author the words later.** The recruiter's spoken words are already in the transcript; notes exist to capture judgments they *won't* say aloud. So the live interaction must be sub-second and wordless; prose belongs in post-session review.

The note-sync lab's model supports this almost exactly (`Transcriptor-worktrees/note-sync-lab/frontend/src/note-sync/contract.ts`, 446 lines + reducer + simulated port + test vectors):

- `NoteKind = note | bookmark | concern | strength | follow_up` — precisely the plan §4.3 shortcuts.
- `bookmark.create` mandates **empty text** — mark-moment is already a one-tap operation by contract.
- Every note carries `transcriptOffsetMs` + `evidence: [{transcriptSegmentId}]` — tap stamps the current segment; `note.update` lets the recruiter add prose after the call.
- `pending | synced | failed | conflict` states give honest offline/latency affordances.

**Proposed launch interaction:** a quiet chip row in the live footer — `● Marcar` / `⚠ Preocupação` / `★ Ponto forte` / `↩ Retomar` — one tap, chip pulses, done. Optional inline one-line field appears *after* the tap for those who want a word, auto-dismissing on blur. Hotkeys (M/C/F/R) as accelerators **only when the tab has focus** — the recruiter is usually focused on Meet/Zoom, so hotkeys cannot be the primary path in a browser (global hotkeys need the native companion; later). Voice notes are wrong here: the candidate hears the recruiter.

Two wrinkles to plan for: (1) non-bookmark notes require non-empty text (`contract.ts` L292–294), so chips must send default text ("Preocupação"); (2) a bookmark can't later gain text by `note.update` (bookmark text must stay empty), so "annotate a mark" means creating a linked note in review. Neither requires contract changes. What *is* missing is the entire real backend — the lab's port is simulated. Budget accordingly.

## 3. Post-session report flow

The assessment-provenance contract already encodes plan §4.4 (`build/worktrees/integrated-recruiter-workspace-lab/frontend/src/assessment-provenance/contract.ts`): per-competency sections with `candidateStatements` (quote + evidence ref), `recruiterJudgments`, `simulatedInference`, `recruiterEdit`, `contradictions`, conclusion `supported | concern | insufficient_evidence`, and report status `draft | approved | superseded | stale` with a version-pinned approval. **This means edit→approve→export needs no document editor:**

1. Session ends → report generates as **draft** (this requires fixing the known `stop_session`-generates-before-corrections sequencing).
2. Review screen = section cards, four visually distinct layers (candidate quote / recruiter note / AI inference / missing evidence). Per section: **Aceitar** or edit AI text in a plain textarea (`recruiterEdit`). Evidence links jump to the transcript excerpt.
3. One **Aprovar relatório** action pins the version; any source change flips it `stale`, honoring "human-approved."
4. Export: today it's two `.txt` downloads (`frontend/src/components/SummaryPanel.tsx`) — not client-presentable. Launch with a **print-styled HTML report → browser Save-as-PDF** (zero new dependencies, one print stylesheet); keep separate transcript/notes exports per plan §4.4. Executive search clients receive formatted documents, typically on the firm's template — DOCX-on-Ella-template is the likely real ask and a fast-follow; confirm with owner (see §5).

## 4. Two-platform UX (Windows)

The web workspace runs fine on Windows; the differences are window management and screen-share safety:

- **Glanceability:** recruiters live in Meet/Zoom full-screen. Recommend a "modo compacto" — a Document Picture-in-Picture window carrying the hero question + note chips. Document PiP creates a true always-on-top window with arbitrary HTML and is supported in Chrome/Edge 116+ on desktop (Edge is the Windows default), per [MDN](https://developer.mozilla.org/en-US/docs/Web/API/Document_Picture-in-Picture_API) and [Chrome for Developers](https://developer.chrome.com/docs/web-platform/document-picture-in-picture). Safari lacks it — acceptable, since launch targets Chrome/Edge on Windows + macOS. This is the single highest-leverage two-platform UX feature and is ~days of work against the existing hero component.
- **Screen-share exposure:** if the recruiter shares **entire screen**, an always-on-top window is captured and the candidate sees the copilot — worst-case trust incident. Mitigations: (a) onboarding + pre-session checklist instructs "share a tab or window, never the whole screen"; (b) a panic-hide control/hotkey on the PiP card; (c) neutral window title ("Notas") rather than product branding. A hard technical guarantee isn't available to a web app; state this honestly.
- Smaller items: Windows taskbar/notification behaviors, and hotkey modifier conventions (Ctrl vs ⌘) if hotkeys ship.

## 5. Scope table, risk, disagreements, owner questions

| Item | Verdict |
|---|---|
| Live hero screen + transcript sheet (exists) | **Must** (done — freeze it) |
| Live note chips (bookmark/concern/strength/follow-up) + real notes backend | **Must** |
| Post-session review: notes timeline + evidence-linked draft report, edit/approve | **Must** |
| Print-CSS PDF export; separate transcript/notes export | **Must** |
| Document-PiP compact mode + screen-share safety checklist | **Should** (first fast-follow if the 6 weeks bite) |
| Hotkeys when focused | **Should** |
| DOCX on Ella template | **Should** (fast-follow) |
| Live coverage rail, coverage-driven triggers, session library/search, voice notes, integrated 4-panel workspace layout | **Cut from launch** |

**Biggest UX risk:** speaker attribution. The report's "candidate statements" (`speakerRole: "candidate" | "interviewer"` in the assessment contract) inherit labels from a pipeline whose Meet-extension correlation was live-tested broken (4 integration defects, 2026-08-01). A report that quotes the recruiter as the candidate re-creates founding complaint #1 inside the flagship deliverable. Launch must rely on the provider-independent mic-vs-system-audio baseline and let the reviewer reassign speakers on any excerpt during review.

**Disagreements with current direction:** (1) The governing plan's four *coordinated live surfaces* (§4.3) should not be read as four visible panels — the integrated lab's grid layout would undo PR #3's one-glance win; live stays hero + chips, coverage arrives later as a collapsed strip at most. (2) Six live shortcuts is too many; ship four (drop pause/exclude-passage from the live surface). (3) Don't ship the labs' UI; ship their contracts under the staging design system, translated.

**Owner questions:** What document format/template do Ella clients actually receive (PDF vs DOCX, pt-BR vs English)? Do recruiters ever share their screen mid-interview — can tab-sharing discipline be mandated internally? Is single-device note capture acceptable at launch (no cross-device sync)? Should the report be exportable before approval (draft watermark) or blocked entirely?

Sources: [MDN — Document Picture-in-Picture API](https://developer.mozilla.org/en-US/docs/Web/API/Document_Picture-in-Picture_API), [Chrome for Developers — Picture-in-Picture for any Element](https://developer.chrome.com/docs/web-platform/document-picture-in-picture)
