import assert from "node:assert/strict";
import { test } from "node:test";

import { buildJoinLink } from "./joinLink.ts";

test("builds deep link without gateway", () => {
  const link = buildJoinLink("sess_123", "key_abc");
  assert.equal(
    link,
    "tars-companion://join?session=sess_123&key=key_abc",
  );
  assert.equal(link.includes("gateway"), false);
});

test("omits gateway when building deep link", () => {
  const link = buildJoinLink("sess_456", "key_def");
  assert.equal(
    link,
    "tars-companion://join?session=sess_456&key=key_def",
  );
  assert.equal(link.includes("gateway"), false);
});

test("encodes special characters in key and session", () => {
  const link = buildJoinLink("s/1", "k/+=");
  assert.equal(
    link,
    "tars-companion://join?session=s%2F1&key=k%2F%2B%3D",
  );
  assert.equal(link.includes("gateway"), false);
});
