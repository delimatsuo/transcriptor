import assert from "node:assert/strict";
import test from "node:test";

import {
  canOpenRecentInterview,
  recentInterviewStatusLabel,
  reviewWarning,
} from "./sessionReview.ts";
import type { RecentInterview, SessionReview } from "../types/ws.ts";

const completed: RecentInterview = {
  id: "session-1",
  title: "Diretoria de Produto",
  started_at: "2026-08-05T14:00:00Z",
  ended_at: "2026-08-05T15:00:00Z",
  session_status: "completed",
  review_status: "ready",
};

test("only completed, structurally valid interviews can be opened", () => {
  assert.equal(canOpenRecentInterview(completed), true);
  assert.equal(
    canOpenRecentInterview({
      ...completed,
      session_status: "incomplete",
      review_status: "incomplete",
    }),
    false,
  );
  assert.equal(
    canOpenRecentInterview({
      ...completed,
      session_status: null,
      review_status: "corrupt",
    }),
    false,
  );
  assert.equal(
    canOpenRecentInterview({
      ...completed,
      review_status: "incomplete",
    }),
    false,
  );
});

test("missing durable report inputs are loud and never presented as ready", () => {
  const review = {
    review_status: "summary_unavailable",
    regeneration_status: "blocked_source_context",
  } as SessionReview;

  assert.match(reviewWarning(review) ?? "", /não foram persistidos/i);
  assert.equal(
    recentInterviewStatusLabel({
      ...completed,
      review_status: "summary_unavailable",
    }),
    "Relatório indisponível",
  );
});
