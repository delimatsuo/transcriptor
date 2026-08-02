# Live Interview UI (frontend-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the live interview screen around a single prominent "next question", with the transcript collapsed into a bottom sheet, and apply one consistent visual system across setup, live, and review.

**Architecture:** A design-token module and three presentational primitives replace hardcoded style literals. All four layout variants move out of `page.tsx` into their own components — verbatim first, so the moves are bisectable — and only then is the interview layout rewritten. Hero/queue/staleness logic lives in a pure, directly-tested module, keeping React components thin. No backend change whatsoever.

**Tech Stack:** Next.js 15, React 19, TypeScript 5.7, inline styles (no CSS framework), Node 22 built-in test runner with `--experimental-strip-types`, Playwright 1.48+ for browser tests.

## Global Constraints

- **No new runtime dependencies.** `@playwright/test` is added as a **devDependency** only; nothing new lands in `dependencies`.
- **No backend changes.** No file under `backend/` is modified by this plan.
- **UI language is pt-BR** for every user-visible string in the three redesigned surfaces.
- **Coverage/competency tracking is out of scope** — deferred to a separate design. Do not add a coverage rail, competency list, or `coverage_update` handling.
- **Playwright tests must never start a real session.** All backend traffic is mocked via `page.route()` and `page.routeWebSocket()`. Starting a real session opens the user's physical microphone.
- **No real candidate or customer data** in fixtures, per README.
- Tests live beside the code they test in `src/lib/*.test.ts`, run by `npm test`, and are excluded from the Next typecheck via `tsconfig.json`.
- Existing pattern to follow: `frontend/src/lib/transcript.ts` + `transcript.test.ts`.

---

### Task 1: Design tokens

**Files:**
- Create: `frontend/src/lib/tokens.ts`
- Test: `frontend/src/lib/tokens.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `tokens` object with `color`, `space`, `radius`, `text` — consumed by every later task.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/tokens.test.ts`:

```ts
import assert from "node:assert/strict";
import { test } from "node:test";

import { tokens } from "./tokens.ts";

test("every colour role is a valid hex or rgba value", () => {
  const flat = Object.values(tokens.color).flatMap((group) =>
    typeof group === "string" ? [group] : Object.values(group),
  );
  assert.ok(flat.length > 0);
  for (const value of flat) {
    assert.match(
      value,
      /^(#[0-9a-f]{6}|rgba\(\d+, ?\d+, ?\d+, ?[\d.]+\))$/i,
      `bad colour value: ${value}`,
    );
  }
});

test("spacing scale is strictly ascending", () => {
  const steps = Object.values(tokens.space);
  for (let i = 1; i < steps.length; i += 1) {
    assert.ok(
      steps[i] > steps[i - 1],
      `spacing not ascending at index ${i}: ${steps[i - 1]} -> ${steps[i]}`,
    );
  }
});

test("type scale is strictly ascending", () => {
  const steps = Object.values(tokens.text);
  for (let i = 1; i < steps.length; i += 1) {
    assert.ok(steps[i] > steps[i - 1], `type scale not ascending at index ${i}`);
  }
});

test("required colour roles exist", () => {
  for (const role of ["primary", "secondary", "tertiary"] as const) {
    assert.equal(typeof tokens.color.text[role], "string");
  }
  for (const role of ["base", "raised", "sunken"] as const) {
    assert.equal(typeof tokens.color.surface[role], "string");
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `Cannot find module './tokens.ts'`

- [ ] **Step 3: Write the implementation**

Create `frontend/src/lib/tokens.ts`. Values are the ones already in use across the current components, promoted to named roles:

```ts
/**
 * Visual system for T.A.R.S.
 *
 * Components keep inline styles but consume these roles instead of literals,
 * so spacing rhythm and type scale stay consistent across surfaces.
 */
export const tokens = {
  color: {
    text: {
      primary: "#1d1d1f",
      secondary: "#86868b",
      tertiary: "#aeaeb2",
      onAccent: "#ffffff",
    },
    surface: {
      base: "#ffffff",
      raised: "#fafafa",
      sunken: "#f5f5f7",
    },
    border: {
      subtle: "#f5f5f7",
      strong: "#d2d2d7",
    },
    accent: "#007aff",
    success: "#34c759",
    warn: "#ff9500",
    danger: "#ff3b30",
    dangerWash: "rgba(255, 59, 48, 0.06)",
    successWash: "rgba(52, 199, 89, 0.1)",
  },
  space: {
    xs: 4,
    sm: 8,
    md: 12,
    lg: 16,
    xl: 24,
    xxl: 32,
    xxxl: 40,
  },
  radius: {
    sm: 8,
    md: 12,
    lg: 16,
    pill: 100,
  },
  text: {
    micro: 10,
    caption: 11,
    small: 13,
    body: 15,
    title: 17,
    hero: 22,
    display: 28,
  },
} as const;

export type Tokens = typeof tokens;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS — 4 new tests plus the 5 existing `transcript.test.ts` tests, 9 total.

- [ ] **Step 5: Verify the app still typechecks**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit code 0, no output.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/tokens.ts frontend/src/lib/tokens.test.ts
git commit -m "feat(frontend): add design token module"
```

---

### Task 2: UI primitives

**Files:**
- Create: `frontend/src/components/ui/Panel.tsx`
- Create: `frontend/src/components/ui/Label.tsx`
- Create: `frontend/src/components/ui/Chip.tsx`

**Interfaces:**
- Consumes: `tokens` from Task 1.
- Produces: `<Panel>`, `<Label>`, `<Chip tone>` used by Tasks 6, 7, 8.

These are presentational components with no branching logic. They are verified by
`tsc --noEmit` and exercised by the Playwright tests in Tasks 3 and 10. No unit test is
written for them, because a test asserting that a `<div>` receives a style object restates
the implementation rather than checking behaviour.

- [ ] **Step 1: Create Panel**

Create `frontend/src/components/ui/Panel.tsx`:

```tsx
"use client";

import type { CSSProperties, ReactNode } from "react";
import { tokens } from "@/lib/tokens";

interface Props {
  children: ReactNode;
  tone?: "base" | "raised" | "sunken";
  style?: CSSProperties;
}

export default function Panel({ children, tone = "base", style }: Props) {
  return (
    <div
      style={{
        backgroundColor: tokens.color.surface[tone],
        borderRadius: tokens.radius.md,
        padding: tokens.space.lg,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
```

- [ ] **Step 2: Create Label**

Create `frontend/src/components/ui/Label.tsx`:

```tsx
"use client";

import type { ReactNode } from "react";
import { tokens } from "@/lib/tokens";

interface Props {
  children: ReactNode;
}

/** Small uppercase section heading, e.g. "PRÓXIMA PERGUNTA". */
export default function Label({ children }: Props) {
  return (
    <div
      style={{
        fontSize: tokens.text.caption,
        fontWeight: 600,
        color: tokens.color.text.secondary,
        textTransform: "uppercase",
        letterSpacing: "0.5px",
      }}
    >
      {children}
    </div>
  );
}
```

- [ ] **Step 3: Create Chip**

Create `frontend/src/components/ui/Chip.tsx`:

```tsx
"use client";

import type { ReactNode } from "react";
import { tokens } from "@/lib/tokens";

type Tone = "neutral" | "accent" | "success" | "warn";

interface Props {
  children: ReactNode;
  tone?: Tone;
}

const TONE_STYLES: Record<Tone, { color: string; backgroundColor: string }> = {
  neutral: {
    color: tokens.color.text.primary,
    backgroundColor: tokens.color.surface.sunken,
  },
  accent: {
    color: tokens.color.accent,
    backgroundColor: tokens.color.surface.sunken,
  },
  success: {
    color: tokens.color.success,
    backgroundColor: tokens.color.successWash,
  },
  warn: {
    color: tokens.color.warn,
    backgroundColor: tokens.color.dangerWash,
  },
};

export default function Chip({ children, tone = "neutral" }: Props) {
  return (
    <span
      style={{
        ...TONE_STYLES[tone],
        fontSize: tokens.text.micro,
        fontWeight: 600,
        padding: `3px ${tokens.space.md}px`,
        borderRadius: tokens.radius.pill,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}
```

- [ ] **Step 4: Verify typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit code 0, no output.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/
git commit -m "feat(frontend): add Panel, Label and Chip primitives"
```

---

### Task 3: Playwright harness and meeting-mode baseline

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/fixtures.ts`
- Create: `frontend/e2e/meeting-mode.spec.ts`
- Modify: `frontend/.gitignore` (or repo `.gitignore`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mockSession(page)` helper used by Task 10, returning
  `{ transcript(text, speaker, isFinal): Promise<void>, suggestion(questions): Promise<void> }`
  — both methods are `async` and must be `await`ed by callers (see the race-condition note
  below); `npm run e2e`.

This task exists **before** the extraction so the meeting-mode smoke test passes against
the *current* code, proving the later verbatim move changed nothing. Do not reorder it.

All backend traffic is mocked. `page.routeWebSocket()` (Playwright 1.48+) does not connect
to a real server by default, so no backend and no microphone are involved.

**Race condition, and why the fixture below awaits a readiness signal.** Playwright's own
docs (`https://playwright.dev/docs/mock#modify-websockets`) document no synchronization
guarantee between `page.routeWebSocket()` registering a handler and the page's own
`new WebSocket()` call actually reaching that handler — it happens over an async CDP
round-trip. Calling `server.transcript(...)` synchronously right after a UI action that
triggers `new WebSocket()` is a real, reproducible race: on a cold dev-server compile the
app is slow enough that it usually resolves in the test's favor, but once the dev server is
warm (the state every subsequent Task 4 re-run will be in) the frame is silently dropped
most of the time, because `sendFrame` is still `null` when `transcript()` fires. The fixture
below closes over a `socketReady` promise that resolves inside the `routeWebSocket` handler,
and `transcript()`/`suggestion()` `await` it before sending — making the two-party
synchronization explicit rather than hoping the race resolves favorably.

- [ ] **Step 1: Install Playwright as a devDependency**

```bash
cd frontend && npm install --save-dev @playwright/test && npx playwright install chromium
```

Expected: `@playwright/test` appears in `devDependencies`; Chromium downloads.

- [ ] **Step 2: Add the config**

Create `frontend/playwright.config.ts`:

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://localhost:3100",
  },
  webServer: {
    command: "npm run dev -- -p 3100",
    url: "http://localhost:3100",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
```

Port 3100 is used deliberately: 3000-3002 are occupied by an unrelated application on the
owner's machine, and 3003 is used for manual testing.

- [ ] **Step 3: Add the mock fixture**

Create `frontend/e2e/fixtures.ts`:

```ts
import type { Page } from "@playwright/test";

export const SESSION_ID = "e2e000000000000000000000000000ff";

/**
 * Mocks every backend call the app makes, so no real session is ever created.
 * Starting a real session opens the machine's physical microphone — tests must
 * never do that.
 *
 * Returns a `send` function that pushes a server frame to the page.
 */
export async function mockSession(page: Page, mode: "meeting" | "interview") {
  await page.route("**/api/sessions**", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: SESSION_ID, mode, status: "active" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ sessions: [] }),
    });
  });

  let sendFrame: ((data: string) => void) | null = null;
  let markSocketReady: () => void;
  const socketReady = new Promise<void>((resolve) => {
    markSocketReady = resolve;
  });

  await page.routeWebSocket(/\/ws\//, (ws) => {
    sendFrame = (data: string) => ws.send(data);
    markSocketReady();
    ws.onMessage(() => {
      // client keepalives — ignore
    });
  });

  let seq = 0;

  return {
    async transcript(text: string, speaker: string, isFinal: boolean) {
      await socketReady;
      seq += 1;
      sendFrame?.(
        JSON.stringify({
          type: "transcript_delta",
          session_id: SESSION_ID,
          sequence_number: seq,
          timestamp: new Date(0).toISOString(),
          payload: {
            segment: {
              id: `seg-${seq}`,
              text,
              speaker,
              start_time: seq,
              end_time: seq + 1,
              confidence: 0.9,
              sequence_number: seq,
              is_final: isFinal,
            },
          },
        }),
      );
    },
    async suggestion(questions: string[]) {
      await socketReady;
      seq += 1;
      sendFrame?.(
        JSON.stringify({
          type: "suggestion",
          session_id: SESSION_ID,
          sequence_number: seq,
          timestamp: new Date(0).toISOString(),
          payload: { questions, markdown: "", context: "" },
        }),
      );
    },
  };
}
```

- [ ] **Step 4: Write the meeting-mode baseline test**

Create `frontend/e2e/meeting-mode.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

import { mockSession } from "./fixtures";

test("meeting mode renders transcript segments from the socket", async ({ page }) => {
  const server = await mockSession(page, "meeting");

  await page.goto("/");
  await page.getByRole("button", { name: /start session|iniciar/i }).click();

  await server.transcript("boa tarde a todos", "Entrevistador", true);

  await expect(page.getByText("boa tarde a todos")).toBeVisible();
  await expect(page.getByText("Entrevistador")).toBeVisible();
});
```

- [ ] **Step 5: Add scripts and ignore artifacts**

In `frontend/package.json`, add to `scripts`:

```json
"e2e": "playwright test"
```

Append to `frontend/.gitignore` (create the file if absent):

```
/test-results/
/playwright-report/
/blob-report/
```

- [ ] **Step 6: Run against the CURRENT code and verify it PASSES, repeatedly**

Run: `cd frontend && npm run e2e`
Expected: 1 passed.

Then run it **three more times in a row** (`for i in 1 2 3; do npm run e2e; done`), all
against the already-warm dev server started by the first run. Expected: 1 passed on every
run. A single green run is not sufficient evidence — the race this fixture guards against
(see above) is exactly the kind of failure that a cold-start run can mask and a warm rerun
exposes. This is the baseline test; it must pass reliably **before** any extraction.

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/playwright.config.ts frontend/e2e/ frontend/.gitignore
git commit -m "test(frontend): add Playwright harness and meeting-mode baseline"
```

---

### Task 4: Extract the four layout variants verbatim

**Files:**
- Create: `frontend/src/components/views/PreSessionView.tsx`
- Create: `frontend/src/components/views/MeetingLiveView.tsx`
- Create: `frontend/src/components/views/PostSessionView.tsx`
- Create: `frontend/src/components/views/InterviewLiveView.tsx`
- Modify: `frontend/src/app/page.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces: four view components. `InterviewLiveView` is rewritten in Task 8; the other three are never touched again by this plan.

**No behaviour changes in this task.** Move the JSX exactly as written, passing what it
currently reads as props. The variants are currently derived from three interleaved
booleans (`isInterview`, `isPostSession`, `hasContent`) with shared header and error chrome
around them — only the inner branch bodies move; the header, error banner, and outer flex
container stay in `page.tsx`.

Commit each view separately so a regression bisects to one move.

- [ ] **Step 1: Extract PreSessionView**

Create `frontend/src/components/views/PreSessionView.tsx` containing the JSX currently
inside the `{!hasContent && ( ... )}` branch of `page.tsx`, with `preInterviewBriefing`
as its only prop:

```tsx
"use client";

import BriefingDisplay from "@/components/BriefingDisplay";

interface Props {
  preInterviewBriefing: string;
}

export default function PreSessionView({ preInterviewBriefing }: Props) {
  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: preInterviewBriefing ? "stretch" : "center",
        justifyContent: preInterviewBriefing ? "flex-start" : "center",
        gap: 8,
        padding: preInterviewBriefing ? "24px 28px" : 40,
        overflowY: "auto",
      }}
    >
      {preInterviewBriefing ? (
        <BriefingDisplay markdown={preInterviewBriefing} />
      ) : (
        <>
          <h2
            style={{
              fontSize: 28,
              fontWeight: 600,
              color: "#1d1d1f",
              margin: 0,
              letterSpacing: "-0.5px",
            }}
          >
            Ready to begin
          </h2>
          <p
            style={{
              fontSize: 15,
              color: "#86868b",
              margin: 0,
              textAlign: "center",
              maxWidth: 400,
              lineHeight: 1.5,
            }}
          >
            Configure your session above and start recording. T.A.R.S. will
            transcribe and assist in real time.
          </p>
        </>
      )}
    </div>
  );
}
```

Replace that branch in `page.tsx` with:

```tsx
{!hasContent && (
  <PreSessionView preInterviewBriefing={preInterviewBriefing} />
)}
```

and add `import PreSessionView from "@/components/views/PreSessionView";`.

Copy strings stay English here — Task 9 translates them.

- [ ] **Step 2: Verify and commit**

```bash
cd frontend && npx tsc --noEmit && npm run e2e
```
Expected: typecheck exit 0; 1 passed.

```bash
git add frontend/src/components/views/PreSessionView.tsx frontend/src/app/page.tsx
git commit -m "refactor(frontend): extract PreSessionView verbatim"
```

- [ ] **Step 3: Extract MeetingLiveView**

Create `frontend/src/components/views/MeetingLiveView.tsx` from the
`{isActive && !isInterview && ( ... )}` branch:

```tsx
"use client";

import TranscriptPanel from "@/components/TranscriptPanel";
import SummaryPanel from "@/components/SummaryPanel";
import SuggestionsPanel from "@/components/SuggestionsPanel";
import type { SuggestionEntry, TranscriptSegment } from "@/types/ws";

interface Props {
  transcript: TranscriptSegment[];
  suggestionHistory: SuggestionEntry[];
  summary: string;
  isSummaryFinal: boolean;
}

export default function MeetingLiveView({
  transcript,
  suggestionHistory,
  summary,
  isSummaryFinal,
}: Props) {
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <TranscriptPanel segments={transcript} />
      {suggestionHistory.length > 0 && (
        <div style={{ borderTop: "1px solid #f5f5f7", maxHeight: 200, overflow: "auto" }}>
          <SuggestionsPanel suggestionHistory={suggestionHistory} />
        </div>
      )}
      <SummaryPanel summary={summary} isFinal={isSummaryFinal} />
    </div>
  );
}
```

Replace the branch with:

```tsx
{isActive && !isInterview && (
  <MeetingLiveView
    transcript={transcript}
    suggestionHistory={suggestionHistory}
    summary={summary}
    isSummaryFinal={isSummaryFinal}
  />
)}
```

- [ ] **Step 4: Verify and commit — this is the move the baseline test guards**

```bash
cd frontend && npx tsc --noEmit && npm run e2e
```
Expected: typecheck exit 0; **1 passed**. If this fails, the move was not verbatim — fix it before continuing.

```bash
git add frontend/src/components/views/MeetingLiveView.tsx frontend/src/app/page.tsx
git commit -m "refactor(frontend): extract MeetingLiveView verbatim"
```

- [ ] **Step 5: Extract PostSessionView**

Create `frontend/src/components/views/PostSessionView.tsx` from the `{isPostSession && ( ... )}`
branch, taking props `transcript`, `summary`, `isSummaryFinal`, `isInterview`, and
`onNewSession: () => void` (replacing the inline `onClick` that calls `setSessionId(null)`
and `setIsActive(false)`). Move the JSX exactly as it stands, including the
`Session Complete` heading and the nested `TranscriptPanel` and `SummaryPanel`.

In `page.tsx`:

```tsx
{isPostSession && (
  <PostSessionView
    transcript={transcript}
    summary={summary}
    isSummaryFinal={isSummaryFinal}
    isInterview={isInterview}
    onNewSession={() => {
      setSessionId(null);
      setIsActive(false);
    }}
  />
)}
```

- [ ] **Step 6: Verify and commit**

```bash
cd frontend && npx tsc --noEmit && npm run e2e
```
Expected: typecheck exit 0; 1 passed.

```bash
git add frontend/src/components/views/PostSessionView.tsx frontend/src/app/page.tsx
git commit -m "refactor(frontend): extract PostSessionView verbatim"
```

- [ ] **Step 7: Extract InterviewLiveView**

Create `frontend/src/components/views/InterviewLiveView.tsx` from the
`{isActive && isInterview && ( ... )}` branch — the 60/40 split with `TranscriptPanel` and
`SuggestionsPanel` — taking props `transcript`, `suggestionHistory`, `preInterviewBriefing`.
Wire it in `page.tsx` the same way. Still verbatim; Task 8 rewrites it.

- [ ] **Step 8: Verify and commit**

```bash
cd frontend && npx tsc --noEmit && npm run e2e
```
Expected: typecheck exit 0; 1 passed.

```bash
git add frontend/src/components/views/InterviewLiveView.tsx frontend/src/app/page.tsx
git commit -m "refactor(frontend): extract InterviewLiveView verbatim"
```

---

### Task 5: Hero and queue logic

**Files:**
- Modify: `frontend/src/types/ws.ts`
- Modify: `frontend/src/hooks/useWebSocket.ts`
- Create: `frontend/src/lib/interviewQueue.ts`
- Test: `frontend/src/lib/interviewQueue.test.ts`

**Interfaces:**
- Consumes: `SuggestionEntry` from `@/types/ws`.
- Produces:
  - `SuggestionEntry` gains `sequenceNumber: number`.
  - `interface HeroState { dismissedThroughSeq: number }`
  - `const initialHeroState: HeroState`
  - `selectHero(history: SuggestionEntry[], state: HeroState): SuggestionEntry | null`
  - `isHeroStale(hero: SuggestionEntry | null, history: SuggestionEntry[]): boolean`
  - `queueCount(history: SuggestionEntry[], hero: SuggestionEntry | null): number`
  - `dismissHero(hero: SuggestionEntry | null, state: HeroState): HeroState`

Staleness derives from the **server-assigned** `sequence_number`, not client receive-time.
`useWebSocket` currently stamps `Date.now()` on receipt, which is wrong under replay because
replayed messages get fresh timestamps.

- [ ] **Step 1: Add sequenceNumber to the type**

In `frontend/src/types/ws.ts`, change `SuggestionEntry`:

```ts
export interface SuggestionEntry {
  questions: string[];
  markdown?: string;
  timestamp: number;
  sequenceNumber: number;
}
```

- [ ] **Step 2: Populate it in the hook**

In `frontend/src/hooks/useWebSocket.ts`, in `case "suggestion"`, add the field:

```ts
      case "suggestion": {
        const payload = msg.payload as unknown as Suggestion;
        const entry: SuggestionEntry = {
          questions: payload.questions,
          markdown: payload.markdown,
          timestamp: Date.now(),
          sequenceNumber: msg.sequence_number,
        };
        setSuggestionHistory((prev) => [...prev, entry]);
        break;
      }
```

And in `case "session_state"`, the synthesised entry also needs one:

```ts
          setSuggestionHistory((prev) => [
            ...prev,
            {
              questions: state.pending_suggestions,
              timestamp: Date.now(),
              sequenceNumber: msg.sequence_number,
            },
          ]);
```

- [ ] **Step 3: Write the failing test**

Create `frontend/src/lib/interviewQueue.test.ts`:

```ts
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  dismissHero,
  initialHeroState,
  isHeroStale,
  queueCount,
  selectHero,
} from "./interviewQueue.ts";
import type { SuggestionEntry } from "@/types/ws";

function entry(sequenceNumber: number, question: string): SuggestionEntry {
  return {
    questions: [question],
    timestamp: sequenceNumber * 1000,
    sequenceNumber,
  };
}

test("hero is null when there are no suggestions", () => {
  assert.equal(selectHero([], initialHeroState), null);
});

test("hero is the oldest undismissed batch", () => {
  const history = [entry(5, "primeira"), entry(9, "segunda")];
  assert.equal(selectHero(history, initialHeroState)?.questions[0], "primeira");
});

test("dismissing advances to the next batch", () => {
  const history = [entry(5, "primeira"), entry(9, "segunda")];
  const hero = selectHero(history, initialHeroState);
  const next = dismissHero(hero, initialHeroState);
  assert.equal(selectHero(history, next)?.questions[0], "segunda");
});

test("dismissing the last batch leaves no hero", () => {
  const history = [entry(5, "unica")];
  const state = dismissHero(selectHero(history, initialHeroState), initialHeroState);
  assert.equal(selectHero(history, state), null);
});

test("hero is stale when a newer batch exists", () => {
  const history = [entry(5, "primeira"), entry(9, "segunda")];
  const hero = selectHero(history, initialHeroState);
  assert.equal(isHeroStale(hero, history), true);
});

test("hero is not stale when it is the newest batch", () => {
  const history = [entry(5, "primeira")];
  const hero = selectHero(history, initialHeroState);
  assert.equal(isHeroStale(hero, history), false);
});

test("queue counts only batches newer than the hero", () => {
  const history = [entry(5, "a"), entry(9, "b"), entry(12, "c")];
  const hero = selectHero(history, initialHeroState);
  assert.equal(queueCount(history, hero), 2);
});

test("queue count is zero when there is no hero", () => {
  assert.equal(queueCount([], null), 0);
});

test("out-of-order arrivals are ordered by sequence number, not arrival", () => {
  // Replayed frames can arrive after live ones.
  const history = [entry(9, "segunda"), entry(5, "primeira")];
  assert.equal(selectHero(history, initialHeroState)?.questions[0], "primeira");
  assert.equal(queueCount(history, selectHero(history, initialHeroState)), 1);
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `Cannot find module './interviewQueue.ts'`

- [ ] **Step 5: Write the implementation**

Create `frontend/src/lib/interviewQueue.ts`:

```ts
import type { SuggestionEntry } from "@/types/ws";

/**
 * Which suggestion batches the interviewer has already worked through.
 *
 * Ordering is by the server-assigned sequence number rather than arrival time,
 * because replayed frames arrive after live ones and are stamped on receipt.
 */
export interface HeroState {
  dismissedThroughSeq: number;
}

export const initialHeroState: HeroState = { dismissedThroughSeq: 0 };

function bySequence(a: SuggestionEntry, b: SuggestionEntry): number {
  return a.sequenceNumber - b.sequenceNumber;
}

/** The oldest batch the interviewer has not yet dismissed. */
export function selectHero(
  history: SuggestionEntry[],
  state: HeroState,
): SuggestionEntry | null {
  const pending = history
    .filter((e) => e.sequenceNumber > state.dismissedThroughSeq)
    .sort(bySequence);
  return pending[0] ?? null;
}

/** True when the conversation has moved past the displayed question. */
export function isHeroStale(
  hero: SuggestionEntry | null,
  history: SuggestionEntry[],
): boolean {
  if (!hero) return false;
  return history.some((e) => e.sequenceNumber > hero.sequenceNumber);
}

/** How many undismissed batches sit behind the hero. */
export function queueCount(
  history: SuggestionEntry[],
  hero: SuggestionEntry | null,
): number {
  if (!hero) return 0;
  return history.filter((e) => e.sequenceNumber > hero.sequenceNumber).length;
}

export function dismissHero(
  hero: SuggestionEntry | null,
  state: HeroState,
): HeroState {
  if (!hero) return state;
  return { dismissedThroughSeq: Math.max(state.dismissedThroughSeq, hero.sequenceNumber) };
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS — 9 new tests; 18 total across three files.

- [ ] **Step 7: Verify typecheck and commit**

```bash
cd frontend && npx tsc --noEmit
```
Expected: exit 0.

```bash
git add frontend/src/types/ws.ts frontend/src/hooks/useWebSocket.ts frontend/src/lib/interviewQueue.ts frontend/src/lib/interviewQueue.test.ts
git commit -m "feat(frontend): derive hero and queue state from server sequence numbers"
```

---

### Task 6: HeroQuestion component

**Files:**
- Create: `frontend/src/components/HeroQuestion.tsx`

**Interfaces:**
- Consumes: `tokens` (Task 1), `Label`/`Chip` (Task 2), `SuggestionEntry` (Task 5).
- Produces: `<HeroQuestion hero isStale queueCount onDismiss />` used by Task 8.

- [ ] **Step 1: Create the component**

```tsx
"use client";

import Chip from "@/components/ui/Chip";
import Label from "@/components/ui/Label";
import { tokens } from "@/lib/tokens";
import type { SuggestionEntry } from "@/types/ws";

interface Props {
  hero: SuggestionEntry | null;
  isStale: boolean;
  queueCount: number;
  onDismiss: () => void;
}

export default function HeroQuestion({
  hero,
  isStale,
  queueCount,
  onDismiss,
}: Props) {
  if (!hero) {
    return (
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: tokens.space.md,
          padding: tokens.space.xl,
        }}
      >
        <p style={{ color: tokens.color.text.secondary, fontSize: tokens.text.body, margin: 0 }}>
          Ouvindo a conversa...
        </p>
        <p style={{ color: tokens.color.text.tertiary, fontSize: tokens.text.small, margin: 0 }}>
          As sugestões aparecem conforme a entrevista avança
        </p>
      </div>
    );
  }

  const question = hero.questions[0] ?? "";

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: tokens.space.md,
        padding: `${tokens.space.xl}px ${tokens.space.xxl}px`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: tokens.space.md }}>
        <Label>Próxima pergunta</Label>
        {isStale && <Chip tone="warn">conversa avançou</Chip>}
      </div>

      <p
        style={{
          fontSize: tokens.text.hero,
          lineHeight: 1.45,
          fontWeight: 500,
          letterSpacing: "-0.2px",
          color: tokens.color.text.primary,
          margin: 0,
        }}
      >
        {question}
      </p>

      <div style={{ display: "flex", alignItems: "center", gap: tokens.space.md }}>
        <button
          onClick={onDismiss}
          style={{
            padding: `${tokens.space.sm}px ${tokens.space.lg}px`,
            backgroundColor: tokens.color.surface.base,
            color: tokens.color.accent,
            border: `1px solid ${tokens.color.border.strong}`,
            borderRadius: tokens.radius.pill,
            fontWeight: 500,
            fontSize: tokens.text.small,
            cursor: "pointer",
          }}
        >
          Próxima
        </button>
        {queueCount > 0 && <Chip>{queueCount} na fila</Chip>}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/HeroQuestion.tsx
git commit -m "feat(frontend): add HeroQuestion component"
```

---

### Task 7: TranscriptSheet component

**Files:**
- Create: `frontend/src/components/TranscriptSheet.tsx`

**Interfaces:**
- Consumes: `tokens` (Task 1), existing `TranscriptPanel`.
- Produces: `<TranscriptSheet segments open onToggle />` used by Task 8.

`TranscriptPanel`'s root div is styled `flex: 1, overflowY: "auto"` and measures its own
scroll position (`containerRef`) to decide whether to auto-scroll on new segments — but
`flex: 1` only takes effect inside a `display: flex` parent. The wrapper below is therefore
`display: "flex", flexDirection: "column"` with `overflow: "hidden"` (not `"auto"`), bounded
by `maxHeight`, so `TranscriptPanel`'s own div — not the wrapper — becomes the actual
scrolling element, exactly as it already does in `MeetingLiveView`. A plain block wrapper
with its own `overflowY: "auto"` would make the *wrapper* the scrolling element instead,
leaving `TranscriptPanel`'s scroll-position math measuring a box that never overflows —
so its "stay near the bottom unless the user scrolled up" heuristic would always read "at
the bottom" and yank the view down on every new segment regardless of where the user
actually scrolled.

- [ ] **Step 1: Create the component**

```tsx
"use client";

import TranscriptPanel from "@/components/TranscriptPanel";
import { tokens } from "@/lib/tokens";
import type { TranscriptSegment } from "@/types/ws";

interface Props {
  segments: TranscriptSegment[];
  open: boolean;
  onToggle: () => void;
}

export default function TranscriptSheet({ segments, open, onToggle }: Props) {
  return (
    <div
      style={{
        borderTop: `1px solid ${tokens.color.border.subtle}`,
        backgroundColor: tokens.color.surface.base,
        flexShrink: 0,
      }}
    >
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: `${tokens.space.md}px ${tokens.space.xl}px`,
          background: "none",
          border: "none",
          cursor: "pointer",
          fontSize: tokens.text.small,
          color: tokens.color.text.secondary,
        }}
      >
        <span>Transcrição</span>
        <span aria-hidden="true">{open ? "⌄" : "⌃"}</span>
      </button>

      {open && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            maxHeight: 320,
            overflow: "hidden",
          }}
        >
          <TranscriptPanel segments={segments} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TranscriptSheet.tsx
git commit -m "feat(frontend): add collapsible TranscriptSheet"
```

---

### Task 8: Rewrite InterviewLiveView

**Files:**
- Modify: `frontend/src/components/views/InterviewLiveView.tsx`

**Interfaces:**
- Consumes: `HeroQuestion` (Task 6), `TranscriptSheet` (Task 7), `interviewQueue` (Task 5), `tokens` (Task 1).
- Produces: the redesigned live interview layout.

- [ ] **Step 1: Replace the file contents**

```tsx
"use client";

import { useState } from "react";

import HeroQuestion from "@/components/HeroQuestion";
import TranscriptSheet from "@/components/TranscriptSheet";
import {
  dismissHero,
  initialHeroState,
  isHeroStale,
  queueCount,
  selectHero,
} from "@/lib/interviewQueue";
import { tokens } from "@/lib/tokens";
import type { SuggestionEntry, TranscriptSegment } from "@/types/ws";

interface Props {
  transcript: TranscriptSegment[];
  suggestionHistory: SuggestionEntry[];
}

export default function InterviewLiveView({
  transcript,
  suggestionHistory,
}: Props) {
  const [heroState, setHeroState] = useState(initialHeroState);
  const [sheetOpen, setSheetOpen] = useState(false);

  const hero = selectHero(suggestionHistory, heroState);
  const stale = isHeroStale(hero, suggestionHistory);
  const queued = queueCount(suggestionHistory, hero);

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        backgroundColor: tokens.color.surface.base,
      }}
    >
      <HeroQuestion
        hero={hero}
        isStale={stale}
        queueCount={queued}
        onDismiss={() => setHeroState((s) => dismissHero(hero, s))}
      />
      <TranscriptSheet
        segments={transcript}
        open={sheetOpen}
        onToggle={() => setSheetOpen((o) => !o)}
      />
    </div>
  );
}
```

- [ ] **Step 2: Drop the now-unused prop in page.tsx**

`InterviewLiveView` no longer takes `preInterviewBriefing`. Update the call site in
`frontend/src/app/page.tsx`:

```tsx
{isActive && isInterview && (
  <InterviewLiveView
    transcript={transcript}
    suggestionHistory={suggestionHistory}
  />
)}
```

If `preInterviewBriefing` becomes unused in `page.tsx`, keep the state — `SessionControls`
still sets it via `onBriefingReady` and `PreSessionView` still reads it.

- [ ] **Step 3: Verify typecheck, unit tests, and the meeting baseline**

```bash
cd frontend && npx tsc --noEmit && npm test && npm run e2e
```
Expected: typecheck exit 0; 18 unit tests pass; 1 e2e passes (meeting mode is unaffected).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/views/InterviewLiveView.tsx frontend/src/app/page.tsx
git commit -m "feat(frontend): rebuild live interview around a single hero question"
```

---

### Task 9: pt-BR copy pass

**Files:**
- Modify: `frontend/src/components/views/PreSessionView.tsx`
- Modify: `frontend/src/components/views/PostSessionView.tsx`
- Modify: `frontend/src/components/SessionControls.tsx`
- Modify: `frontend/src/components/SummaryPanel.tsx`
- Modify: `frontend/src/components/TranscriptPanel.tsx`
- Modify: `frontend/e2e/meeting-mode.spec.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces: no API change — strings only.

Translate every user-visible string in the three redesigned surfaces. Exact replacements:

| File | English | pt-BR |
|---|---|---|
| `PreSessionView.tsx` | `Ready to begin` | `Tudo pronto` |
| `PreSessionView.tsx` | `Configure your session above and start recording. T.A.R.S. will transcribe and assist in real time.` | `Configure a sessão acima e comece a gravar. O T.A.R.S. transcreve e ajuda em tempo real.` |
| `PostSessionView.tsx` | `Session Complete` | `Sessão concluída` |
| `PostSessionView.tsx` | `Review your interview below` / `Review your meeting below` | `Revise sua entrevista abaixo` / `Revise sua reunião abaixo` |
| `PostSessionView.tsx` | `New Session` | `Nova sessão` |
| `PostSessionView.tsx` | `Full Transcript` | `Transcrição completa` |
| `SessionControls.tsx:225` | `Session title (optional)` | `Título da sessão (opcional)` |
| `SessionControls.tsx:263` | `Meeting` | `Reunião` |
| `SessionControls.tsx:264` | `Interview` | `Entrevista` |
| `SessionControls.tsx:192` | `Recording` | `Gravando` |
| `SessionControls.tsx:283` | `Starting...` (the `loading` branch of the button-label ternary) | `Iniciando...` |
| `SessionControls.tsx:307` | `Interview Preparation` | `Preparação da entrevista` |
| `SessionControls.tsx:323` | `Candidate name` | `Nome do candidato` |
| `SessionControls.tsx:380` | `Upload CV / Resume` | `Enviar currículo` |
| `SessionControls.tsx:386` | `Paste job description here` | `Cole a descrição da vaga aqui` |
| `SessionControls.tsx:74,83` | `Analysis failed` (both occurrences — a thrown-error fallback and a caught-error fallback for the same failure) | `Falha na análise` |
| `SessionControls.tsx:461` | `Retry` | `Tentar novamente` |
| `SummaryPanel.tsx` | `Download Report` | `Baixar relatório` |
| `SummaryPanel.tsx` | `Download Transcript` | `Baixar transcrição` |
| `SummaryPanel.tsx:89` | `Session Summary` (source is Title Case; a parent `<h2>` applies `textTransform: "uppercase"`, so it renders as `SESSION SUMMARY` — translate the source string, not the rendered casing) | `Resumo da sessão` |
| `SummaryPanel.tsx:88` | `Interview Assessment` (sibling branch of the same three-way ternary as the row above — `isFinal && isInterview`) | `Avaliação da entrevista` |
| `SummaryPanel.tsx:90` | `Rolling Summary` (third branch of the same ternary — `!isFinal`) | `Resumo em andamento` |
| `TranscriptPanel.tsx` | `Waiting for speech...` | `Aguardando fala...` |
| `TranscriptPanel.tsx` | `transcribing...` | `transcrevendo...` |

Also translate the remaining `Start Session` / `Stop Session` button labels in
`SessionControls.tsx` to `Iniciar sessão` / `Encerrar sessão`, and the downloaded filenames
in `SummaryPanel.tsx` from `interview-assessment-report.txt` / `interview-transcript.txt` to
`relatorio-entrevista.txt` / `transcricao-entrevista.txt`.

Line numbers above are as of commit `20c4af4` (after Task 8) — verify each string is still
at the stated location before editing, since earlier tasks may have shifted lines slightly;
if a string has moved, find it by content, not by trusting the line number blindly.

- [ ] **Step 1: Apply every replacement in the table**

- [ ] **Step 2: Update the e2e selector**

The baseline test matches `/start session|iniciar/i`, which already covers the new label —
confirm it still matches, and tighten it to `/iniciar sessão/i` now that the English label
is gone.

- [ ] **Step 3: Verify nothing English remains in the redesigned surfaces**

Run both commands:
```bash
cd frontend && grep -rnE '"(Ready to begin|Session Complete|New Session|Full Transcript|Download Report|Download Transcript|Waiting for speech|Start Session|Stop Session|Starting\.\.\.|Interview Assessment|Rolling Summary|Analysis failed|Upload CV / Resume)"' src/components src/app
cd frontend && grep -rnE '^\s*(Recording|Retry|Interview Preparation)\s*$' src/components src/app
```
Expected: no output from either command.

**Why two commands, and why this replaced the original single `>(...)`-prefixed grep:** the
first pass through this task shipped with eight strings still in English — six missing from
the original replacement table entirely (`Recording`, `Interview Preparation`,
`Upload CV / Resume`, `Retry`, `Starting...`, `Analysis failed` ×2), plus two more
(`Interview Assessment`, `Rolling Summary`) found only by directly reading the files rather
than trusting a grep. The original verification command, `grep -rnE ">(...)"`, required the
`>` and the target text to be **on the same source line** — which fails for the multi-line
JSX this codebase actually uses (e.g. a closing `>` on one line and the text node on the
next). It returned "no output" against a file that still had English in it, and that false
"clean" result was the thing that let a plan-mandated gap slip through review undetected.
The two commands above split by how each string actually appears: quoted string literals
(command 1) and bare JSX text nodes on their own line (command 2, anchored on the whole
line so it can't match a same-named identifier elsewhere in the file). If you are re-running
this task from scratch rather than fixing a specific gap, don't trust either grep alone as a
substitute for reading the six files yourself — they cover the strings known to be missing
as of this amendment, not a general guarantee.

- [ ] **Step 4: Verify typecheck, tests, and e2e**

```bash
cd frontend && npx tsc --noEmit && npm test && npm run e2e
```
Expected: typecheck exit 0; 18 unit tests pass; 1 e2e passes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): translate interface to pt-BR"
```

---

### Task 10: End-to-end coverage of the hero seam

**Files:**
- Create: `frontend/e2e/interview-hero.spec.ts`

**Interfaces:**
- Consumes: `mockSession` from `frontend/e2e/fixtures.ts` (Task 3).
- Produces: nothing consumed elsewhere.

This covers the seam the pure tests cannot reach: a suggestion arriving over a live socket
and repainting the hero. On 2026-08-01 a feature passed 31 unit tests while being entirely
non-functional because every defect sat on exactly this kind of boundary.

- [ ] **Step 1: Write the test**

```ts
import { expect, test } from "@playwright/test";

import { mockSession } from "./fixtures";

test("a suggestion from the socket becomes the hero question", async ({ page }) => {
  const server = await mockSession(page, "interview");

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("button", { name: /iniciar sessão/i }).click();

  await expect(page.getByText(/ouvindo a conversa/i)).toBeVisible();

  await server.suggestion(["Como você estruturou essa equipe?"]);

  await expect(page.getByText("Como você estruturou essa equipe?")).toBeVisible();
  await expect(page.getByText(/próxima pergunta/i)).toBeVisible();
});

test("a newer batch marks the hero stale and fills the queue", async ({ page }) => {
  const server = await mockSession(page, "interview");

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("button", { name: /iniciar sessão/i }).click();

  await server.suggestion(["Primeira pergunta"]);
  await expect(page.getByText("Primeira pergunta")).toBeVisible();

  await server.suggestion(["Segunda pergunta"]);

  await expect(page.getByText(/conversa avançou/i)).toBeVisible();
  await expect(page.getByText(/1 na fila/i)).toBeVisible();
  // The first question must NOT be replaced — it holds until dismissed.
  await expect(page.getByText("Primeira pergunta")).toBeVisible();
});

test("dismissing advances to the queued question", async ({ page }) => {
  const server = await mockSession(page, "interview");

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("button", { name: /iniciar sessão/i }).click();

  await server.suggestion(["Primeira pergunta"]);
  await server.suggestion(["Segunda pergunta"]);
  await expect(page.getByText("Primeira pergunta")).toBeVisible();

  await page.getByRole("button", { name: /^próxima$/i }).click();

  await expect(page.getByText("Segunda pergunta")).toBeVisible();
  await expect(page.getByText("Primeira pergunta")).toHaveCount(0);
});

test("the transcript sheet opens over the hero", async ({ page }) => {
  const server = await mockSession(page, "interview");

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("button", { name: /iniciar sessão/i }).click();

  await server.transcript("eu liderei essa transformação", "Candidato", true);

  // Collapsed by default — transcript text is not on screen.
  await expect(page.getByText("eu liderei essa transformação")).toHaveCount(0);

  await page.getByRole("button", { name: /transcrição/i }).click();

  await expect(page.getByText("eu liderei essa transformação")).toBeVisible();
});
```

- [ ] **Step 2: Run the full e2e suite**

Run: `cd frontend && npm run e2e`
Expected: 5 passed (1 meeting baseline + 4 interview).

- [ ] **Step 3: Run everything once more**

```bash
cd frontend && npx tsc --noEmit && npm test && npm run e2e && npm run build
```
Expected: typecheck exit 0; 18 unit tests pass; 5 e2e pass; build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/interview-hero.spec.ts
git commit -m "test(frontend): cover the hero question WebSocket seam end to end"
```

---

## Self-review notes

**Spec coverage.** Tokens and primitives → Tasks 1-2. `page.tsx` decomposition with verbatim
moves first → Task 4. Hero, queue, dismissal, sequence-number staleness → Tasks 5-6, 8.
Transcript sheet → Tasks 7-8. pt-BR copy → Task 9. Meeting-mode smoke proving no behaviour
change → Task 3 (baseline) re-run after every extraction in Task 4. Playwright WebSocket seam
→ Task 10. Pre-session and post-session visual redesign is delivered by the token adoption in
Task 9's copy pass plus the extraction in Task 4; those two views keep their existing layout,
as the amended spec specifies "visual redesign only, no functional change".

**Deliberately not in this plan.** Coverage rail, competency editing, `coverage_update`,
report gating, and any backend edit — all deferred per the amended spec.

**Type consistency.** `SuggestionEntry.sequenceNumber` is added in Task 5 and used in Tasks
5, 6, 8. `HeroState`/`initialHeroState` are defined in Task 5 and consumed in Task 8.
`selectHero`/`isHeroStale`/`queueCount`/`dismissHero` keep identical signatures across Tasks
5 and 8. `mockSession` is defined in Task 3 and reused in Task 10 with the same shape.

**Known risk.** Task 4 Step 3's claim that the meeting move is "verbatim" is guarded by the
Task 3 baseline; if it fails, the move was not verbatim. `page.tsx`'s three interleaved
booleans mean the branch bodies move but the surrounding chrome does not — expect this to
need care rather than a clean cut.
