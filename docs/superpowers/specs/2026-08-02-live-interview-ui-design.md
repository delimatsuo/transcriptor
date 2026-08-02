# Live interview UI redesign — design

**Date:** 2026-08-02
**Status:** approved for planning (amended 2026-08-02 after adversarial review)
**Scope:** frontend only — live interview screen, pre-session setup, post-session review
**Review:** [`2026-08-02-live-interview-ui-design-REVIEW.md`](2026-08-02-live-interview-ui-design-REVIEW.md)

> **Amendment note.** The first version of this spec also specified a competency
> coverage-tracking model (JD-derived competencies, LLM-judged coverage states, a
> `coverage_update` WebSocket message, and a coverage rail in the live UI). Adversarial
> review returned NEEDS_REVISION with six critical findings against that half, and
> established that a better, already-tested implementation exists in
> `build/worktrees/interview-coverage-lab`. The coverage model has been **removed from this
> spec** and returned to design. See "Deferred: coverage model" below. Everything remaining
> here is frontend-only and requires no backend change.

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
- Within a live session, nothing the interviewer is reading is replaced or discarded by
  another speaker's activity or by the arrival of a newer suggestion.
- The interface is visually consistent across setup, live, and review.

Note on the second goal: a **browser reload still loses live session state** (hero, queue,
dismissals). That is pre-existing behaviour, not introduced here — `connect()` replays only
when `last_seq > 0`, and the client resets its sequence marker on connect. Fixing it
requires the unimplemented `session_state` path, which is tracked separately and is out of
scope.

## Non-goals

- Competency coverage tracking of any kind — deferred, see below.
- Live meeting mode gains no new behaviour. Its layout code moves file, unchanged.
- No CSS framework, no new runtime dependencies.
- No backend changes. No changes to audio capture, STT, LLM prompts, or speaker labelling.
- Not an i18n project: the UI ships in pt-BR only, with no translation layer.

## Locked decisions

| Decision | Choice |
|---|---|
| Primary glance value | The single best next question |
| Transcript during live | Collapsed, one tap away |
| Question lifecycle | Holds its place until dismissed; visibly marks itself stale |
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

Three regions.

**Status bar** — elapsed time with recording indicator, candidate name and role, connection
state. Quiet, always present.

**Hero** (dominant) — kicker `Próxima pergunta`, the question at large type, and the stale
indicator when applicable. The only element competing for the eye. With no coverage rail it
occupies the full width.

**Footer** — queue chip (`3 na fila`) and transcript toggle (`Transcrição ⌃`).

The transcript is a collapsible bottom sheet, collapsed by default, expanding over the hero
without reflowing it. `TranscriptPanel` is reused inside the sheet; its placement was the
problem, not the component. Its height and scroll behaviour must be verified in the new
container — it currently assumes it owns its scroll region.

### Hero and queue semantics

The hero is whichever suggestion the interviewer has not yet dismissed. Batches newer than
the hero constitute the queue; the footer chip shows the count of **undismissed batches**
newer than the hero, not the total number of individual questions they contain. Dismissing
advances to the next batch.

Staleness is derived on the frontend from the server-assigned `sequence_number` on each
message — **not** from client receive-time. `useWebSocket` currently stamps `Date.now()` on
receipt, which is wrong under replay (replayed messages get fresh timestamps); this design
uses the sequence number the server already sets. Staleness therefore requires no new
backend state and cannot desynchronize.

All hero, queue, and dismissal logic lives in a pure module under `frontend/src/lib/`,
tested directly, following the precedent set by `src/lib/transcript.ts`.

## Surface: pre-session setup

Visual redesign only: tokens, spacing rhythm, pt-BR copy, and the calm treatment applied to
the existing upload and session-start controls. No functional change.

## Surface: post-session review

Visual redesign only: tokens, spacing rhythm, pt-BR copy, applied to the existing
transcript and assessment report. No functional change. The existing download controls stay
as they are.

## Structure

All four layout variants extract from `page.tsx` (currently 356 lines holding every variant
as inline JSX) into their own components, leaving `page.tsx` owning session state and
routing only.

Sequencing matters for rollback: land the **verbatim moves first**, each in its own commit
with no behaviour change, then rewrite the interview layout. Note the variants are currently
derived from three interleaved booleans (`isInterview`, `isPostSession`, `hasContent`) with
shared header and error chrome, so "verbatim" requires care — budget for it rather than
assuming a clean cut.

**New components:** `HeroQuestion`, `TranscriptSheet`.
**Modified:** `page.tsx`, `SessionControls`, `SummaryPanel`.
**Reused:** `TranscriptPanel`, `BriefingDisplay`, `ConnectionStatus`.

## Error handling and degradation

- No suggestions yet → the hero shows the existing listening state, not an empty panel.
- WebSocket disconnected → existing `ConnectionStatus` behaviour is unchanged.
- Browser reload → live session state is lost, as today. Not addressed here.

## Testing

Three layers, chosen deliberately: on 2026-08-01 a feature was found to be entirely
non-functional despite 31 passing unit tests, because all four of its defects sat on
integration boundaries no unit test touched.

1. **Pure logic** in `src/lib/`, tested with the existing zero-dependency Node test-runner
   pattern established by `src/lib/transcript.test.ts`: staleness derivation from sequence
   numbers, queue counting, dismissal advancement.
2. **A meeting-mode smoke test.** This spec promises the meeting layout is unchanged while
   moving its code; nothing currently verifies that, and no existing test touches
   `page.tsx`.
3. **Playwright** for the seam pure functions cannot reach: a suggestion arriving over a
   live WebSocket and repainting the hero, and the transcript sheet opening over it.

Playwright runs locally and is not a CI gate — the checked-in workflow is build-only by
convention. All test data is synthetic; per README, no real candidate or customer data.

## Deferred: coverage model

Competency extraction, coverage judging, the coverage rail, and coverage-corrected report
generation are **removed from this spec** and require a new design.

Any future design must start from the existing implementation in
`build/worktrees/interview-coverage-lab` — `frontend/src/interview-coverage/contract.ts`
(731 lines), `projector.ts`, `interview-coverage-v1.schema.json`, and a 910-line test suite
— rather than re-deriving one. That contract models three **orthogonal** axes:

```ts
export type CoverageExplorationState   = "missing" | "explored";
export type CoverageEvidenceState      = "none" | "insufficient" | "sufficient";
export type CoverageContradictionState = "none" | "open";
```

This is strictly better than the flat `untouched | mentioned | explored` model this spec
originally proposed, which collapsed exploration into evidence sufficiency and could not
represent a competency that was explored and answered badly.

The review report additionally records six critical findings any future coverage design must
resolve, including: the job description is untrusted third-party input and is unguarded in
every prompt it reaches; coverage would collide with the report prompt's hardcoded
five-competency 1-5 rubric; `stop_session` generates the report before corrections can be
made; and coverage judging silently depends on speaker attribution that was live-tested on
2026-08-01 and found broken.

## Out of scope, tracked separately

- The WebSocket `gap > REPLAY_BUFFER_SIZE` path returns without sending anything;
  `WSMessage.session_state_msg` and the frontend's `case "session_state"` handler both exist
  but no caller invokes the factory.
- Audio is silently discarded during STT stream rotation (`StreamManager.send_audio`), and
  the FLAC backup write blocks the event loop (`AudioBuffer.chunks`).
- `ws_manager.cleanup_session` is defined and never called.
