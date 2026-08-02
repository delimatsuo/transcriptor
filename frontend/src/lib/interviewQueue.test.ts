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
