import assert from "node:assert/strict";
import { test } from "node:test";

import { admissionIsCurrent } from "./authAdmission.ts";

test("stale admission cannot commit after a newer account generation", () => {
  const controller = new AbortController();

  assert.equal(
    admissionIsCurrent(controller.signal, 1, 2, "uid-b", "uid-a"),
    false,
  );
});

test("current admission requires the Firebase uid to remain unchanged", () => {
  const controller = new AbortController();

  assert.equal(
    admissionIsCurrent(controller.signal, 3, 3, "uid-b", "uid-b"),
    true,
  );
  assert.equal(
    admissionIsCurrent(controller.signal, 3, 3, "uid-c", "uid-b"),
    false,
  );
});

test("aborted admission cannot commit", () => {
  const controller = new AbortController();
  controller.abort();

  assert.equal(
    admissionIsCurrent(controller.signal, 3, 3, "uid-b", "uid-b"),
    false,
  );
});
