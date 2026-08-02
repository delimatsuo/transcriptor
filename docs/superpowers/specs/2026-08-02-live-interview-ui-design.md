# Live interview UI redesign — design

**Date:** 2026-08-02
**Status:** approved for planning
**Scope:** live interview screen, pre-session setup, post-session review

## Problem

After the first real interview conducted with T.A.R.S. (2026-03-16), the owner reported:
"UI not helpful during live interview — needs rethinking with Apple design principles:
clarity, calm, persistent information." The governing test is: *can the interviewer glance
at this mid-conversation and get value?*

The current live interview layout gives 60% of the screen to the running transcript — a
record of a conversation the interviewer was personally just part of, and therefore the
lowest-value thing on screen mid-interview — and 40% to a newest-first stack of visually
identical suggestion cards that compete with each other for attention.

## Goals

- One glance, one answer: the screen's primary job is to hand the interviewer their next question.
- Nothing the interviewer is reading may vanish or change underneath them.
- The interviewer should never leave an interview with an unnoticed coverage gap.
- The interface must never imply a competency was adequately explored when it was not.

## Non-goals

- Live meeting mode (non-interview) is untouched.
- No CSS framework, no new runtime dependencies.
- No changes to audio capture, STT, or speaker labelling.
- Not an i18n project: the UI ships in pt-BR only, with no translation layer.

## Locked decisions

| Decision | Choice |
|---|---|
| Primary glance value | The single best next question |
| Transcript during live | Collapsed, one tap away |
| Question lifecycle | Holds its place until dismissed; visibly marks itself stale |
| Coverage source | Auto-derived from the JD, editable before the session starts |
| Coverage depth | Three states: `untouched` / `mentioned` / `explored` |
| Stale coverage | Quiet indicator; last known state retained |
| UI language | pt-BR throughout |
| Styling | Design tokens + small primitives, consumed by the existing inline-style pattern |

## Foundation

`frontend/src/lib/tokens.ts` exports the visual system: colour **roles** (`text.primary`,
`text.secondary`, `text.tertiary`, `surface.base`, `surface.raised`, `surface.sunken`,
`border.subtle`, `accent`, `warn`, `success`), a spacing scale, a type scale, and radii.

`frontend/src/components/ui/` provides `Panel`, `Label`, `Chip`.

Components keep the existing inline-style approach but consume tokens instead of literal
values. This is a refactor of values, not of architecture: no dependencies, no build
changes. It exists because "calm" is chiefly a function of a consistent type scale and
spacing rhythm, which per-component ad-hoc values structurally prevent.

## Surface: live interview screen

Four regions.

**Status bar** — elapsed time with recording indicator, candidate name and role,
connection state. Quiet, always present.

**Hero** (dominant) — kicker `Próxima pergunta`, the question at large type, and the stale
indicator when applicable. The only element competing for the eye.

**Coverage rail** (~140px, right) — heading `Cobertura`, one line per competency, each in
one of three states. Shows a quiet `cobertura desatualizada` indicator and dims slightly
when no successful coverage update has arrived recently.

**Footer** — queue chip (`3 na fila`) and transcript toggle (`Transcrição ⌃`).

The transcript is a collapsible bottom sheet, collapsed by default, expanding over the
hero without reflowing it. `TranscriptPanel` is reused unchanged inside the sheet; its
placement was the problem, not the component.

### Hero and queue semantics

The hero is whichever suggestion the interviewer has not yet dismissed. Batches newer than
the hero constitute the queue; the footer chip shows the count of **undismissed batches**
newer than the hero, not the total number of individual questions they contain. Dismissing
advances to the next batch.

Staleness is derived entirely on the frontend as `heroTimestamp < latestTimestamp`. It
requires no new backend state and therefore cannot desynchronize.

## Surface: pre-session setup

After the JD is parsed, the extracted competencies appear as editable chips. The
interviewer may add, remove, rename, or reorder them before starting. The resulting list
is authoritative and is never re-derived mid-interview.

## Surface: post-session review

Gains the final coverage map, correctable before the assessment report is generated, so an
overcalled `mentioned` can be fixed before it reaches a document the owner puts their name
on. `INTERVIEW_REPORT_PROMPT` receives the corrected map, not the model's raw output.

## Data flow

**Competency extraction.** The JD already flows through `PRE_INTERVIEW_ANALYSIS_PROMPT`.
That call is extended to also return 5–7 competencies (`id`, `label`). The reviewed and
edited list is persisted on the session.

**Coverage judging.** `_generate_interview_suggestions` (backend/main.py) already fires
every 5th final transcript segment. That same call is extended to return a coverage state
per competency. No new LLM call, no new cadence, no added cost.

**Reconciliation.** The backend holds authoritative per-session coverage. Each analysis
pass lenient-merges the model's reply: recognized ids with valid states are applied,
unknown ids are dropped and logged with counts, omitted competencies retain their previous
value. Automatic coverage is **monotonic** — `untouched → mentioned → explored`, never
backward — which makes wholesale replacement idempotent and reconnect-safe, and prevents a
single bad model turn from erasing evidence.

Monotonicity constrains *automatic* reconciliation only. Interviewer corrections in the
post-session review may set any competency to any state, including downgrading an
overcalled `explored`; that is the purpose of the review step. Corrected values are stored
distinctly from model-derived ones so a later automatic pass can never silently re-raise a
state the interviewer deliberately lowered.

**Transport.** A dedicated `coverage_update` WebSocket message carries the full reconciled
map; the client replaces its coverage state wholesale. Coverage is deliberately *not*
merged into the existing `suggestion` message: suggestions are an accumulating history,
coverage is current state, and conflating the two would force the reducer to dig current
state out of the newest history entry.

**Reconnect.** The backend sends a fresh `coverage_update` on every connect, **after**
replay. Sending it before replay would allow a buffered older update to replay afterward
and clobber it with stale state. The client additionally ignores any `coverage_update`
whose sequence number is at or below the highest already applied.

**Persistence.** The competency list and the final coverage map are stored on the session
in Firestore so the post-session review can load and correct them.

## Error handling and degradation

- No JD, failed extraction, or zero competencies → the rail is hidden and the hero expands
  to full width. The screen never renders an empty or speculative rail.
- Malformed model output → lenient merge as above; the previous map survives.
- Coverage considered stale after **two consecutive analysis passes** fail to produce a
  usable update (a pass being every 5th final segment, so roughly 10 final segments). The
  rail then dims and shows `cobertura desatualizada`, retaining its last known state rather
  than discarding it. A single failed pass is not surfaced — transient model errors should
  not flicker the interface mid-interview.
- Meeting mode renders neither hero nor rail.

## Testing

Three layers, chosen deliberately: on 2026-08-01 a feature was found to be entirely
non-functional despite 31 passing unit tests, because all four of its defects sat on
integration boundaries no unit test touched.

1. **Pure logic** in `src/lib/`, tested with the existing zero-dependency Node test-runner
   pattern established by `src/lib/transcript.test.ts`: staleness derivation, queue
   counting, coverage merge and monotonicity, competency list editing.
2. **Backend** pytest coverage for reconciliation, monotonicity, and the reconnect send
   ordering.
3. **Playwright** for the one seam the other two provably cannot reach: a real
   `coverage_update` arriving over a live WebSocket and repainting the rail.

Playwright runs locally and is not a CI gate — the checked-in workflow is build-only by
convention. All test data is synthetic; per README, no real candidate or customer data.

## Structure

All four layout variants extract from `page.tsx` (currently 356 lines holding every
variant as inline JSX) into their own components, leaving `page.tsx` owning session state
and routing only. The meeting-mode branch moves verbatim, in its own commit with no
behaviour change, so any regression bisects cleanly.

**New components:** `HeroQuestion`, `CoverageRail`, `TranscriptSheet`, `CompetencyEditor`,
`CoverageReview`.
**Modified:** `page.tsx`, `SessionControls`, `SummaryPanel`.
**Reused unchanged:** `TranscriptPanel`, `BriefingDisplay`, `ConnectionStatus`.

## Explicitly out of scope

Two pre-existing defects were found while designing this. Both ship independently and are
tracked separately:

- The WebSocket `gap > REPLAY_BUFFER_SIZE` path in `backend/ws/handler.py` returns without
  sending anything. `WSMessage.session_state_msg` and the frontend's `case "session_state"`
  handler both exist, but no caller ever invokes the factory.
- Audio is silently discarded during STT stream rotation
  (`StreamManager.send_audio`), and the FLAC backup write blocks the event loop
  (`AudioBuffer.chunks`).
