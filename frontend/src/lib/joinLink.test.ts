import assert from "node:assert/strict";
import { test } from "node:test";

import { buildJoinLink } from "./joinLink.ts";

test("builds deep link with gateway", () => {
  assert.equal(
    buildJoinLink(
      "sess_123",
      "key_abc",
      "ws://127.0.0.1:8000/api/stream/native",
    ),
    "tars-companion://join?session=sess_123&key=key_abc&gateway=ws%3A%2F%2F127.0.0.1%3A8000%2Fapi%2Fstream%2Fnative",
  );
});

test("omits gateway when not provided", () => {
  assert.equal(
    buildJoinLink("sess_456", "key_def"),
    "tars-companion://join?session=sess_456&key=key_def",
  );
});

test("encodes special characters in key and session", () => {
  assert.equal(
    buildJoinLink("s/1", "k/+="),
    "tars-companion://join?session=s%2F1&key=k%2F%2B%3D",
  );
});
