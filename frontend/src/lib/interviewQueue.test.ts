import assert from "node:assert/strict";
import { test } from "node:test";

import {
  dismissHero,
  entryHasContent,
  initialHeroState,
  isHeroStale,
  queueCount,
  questionLog,
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

function entryWithMarkdown(
  sequenceNumber: number,
  questions: string[],
  markdown: string,
): SuggestionEntry {
  return { questions, markdown, timestamp: sequenceNumber * 1000, sequenceNumber };
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

test("an entry with a question has content", () => {
  assert.equal(entryHasContent(entry(1, "pergunta")), true);
});

test("an entry with no question but real markdown has content", () => {
  assert.equal(entryHasContent(entryWithMarkdown(1, [], "### Notas\nAlgum texto.")), true);
});

test("an entry with no question and no markdown has no content", () => {
  assert.equal(entryHasContent(entryWithMarkdown(1, [], "")), false);
});

test("an entry with only whitespace in both fields has no content", () => {
  assert.equal(entryHasContent(entryWithMarkdown(1, ["   "], "   \n  ")), false);
});

test("selectHero skips a content-less entry and picks the next real one", () => {
  const history = [
    entryWithMarkdown(5, [], ""),
    entry(9, "pergunta real"),
  ];
  assert.equal(selectHero(history, initialHeroState)?.questions[0], "pergunta real");
});

test("selectHero returns an entry with only markdown content", () => {
  const history = [entryWithMarkdown(5, [], "### Só markdown")];
  const hero = selectHero(history, initialHeroState);
  assert.equal(hero?.markdown, "### Só markdown");
});

test("queueCount does not count content-less entries", () => {
  const history = [
    entry(5, "primeira"),
    entryWithMarkdown(7, [], ""),
    entry(9, "segunda"),
  ];
  const hero = selectHero(history, initialHeroState);
  assert.equal(queueCount(history, hero), 1);
});

test("questionLog on empty history is empty", () => {
  const log = questionLog([], initialHeroState);
  assert.equal(log.atual, null);
  assert.deepEqual(log.fila, []);
  assert.deepEqual(log.anteriores, []);
});

test("questionLog with fresh state: oldest is atual, rest queue in order", () => {
  const history = [entry(9, "segunda"), entry(5, "primeira"), entry(12, "terceira")];
  const log = questionLog(history, initialHeroState);
  assert.equal(log.atual?.questions[0], "primeira");
  assert.deepEqual(log.fila.map((e) => e.questions[0]), ["segunda", "terceira"]);
  assert.deepEqual(log.anteriores, []);
});

test("questionLog moves dismissed batches to anteriores, newest first", () => {
  const history = [entry(5, "primeira"), entry(9, "segunda"), entry(12, "terceira")];
  let state = dismissHero(selectHero(history, initialHeroState), initialHeroState);
  state = dismissHero(selectHero(history, state), state);
  const log = questionLog(history, state);
  assert.deepEqual(log.anteriores.map((e) => e.questions[0]), ["segunda", "primeira"]);
  assert.equal(log.atual?.questions[0], "terceira");
  assert.deepEqual(log.fila, []);
});

test("questionLog excludes content-less entries from every group", () => {
  const history = [
    entry(5, "primeira"),
    entryWithMarkdown(7, [], ""),
    entry(9, "segunda"),
    entryWithMarkdown(10, [], "   "),
  ];
  const state = dismissHero(selectHero(history, initialHeroState), initialHeroState);
  const log = questionLog(history, state);
  assert.deepEqual(log.anteriores.map((e) => e.sequenceNumber), [5]);
  assert.equal(log.atual?.sequenceNumber, 9);
  assert.deepEqual(log.fila, []);
});

test("questionLog with everything dismissed keeps full history browsable", () => {
  const history = [entry(5, "primeira"), entry(9, "segunda")];
  let state = dismissHero(selectHero(history, initialHeroState), initialHeroState);
  state = dismissHero(selectHero(history, state), state);
  const log = questionLog(history, state);
  assert.equal(log.atual, null);
  assert.deepEqual(log.fila, []);
  assert.deepEqual(log.anteriores.map((e) => e.questions[0]), ["segunda", "primeira"]);
});
