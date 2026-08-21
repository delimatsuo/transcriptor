import assert from "node:assert/strict";
import { test } from "node:test";

import { buildStreamUrl } from "./streamUrl.ts";

test("appends encoded stream key", () => {
  assert.equal(
    buildStreamUrl("ws://h/api/stream/native", "s1", "k/+="),
    "ws://h/api/stream/native/s1?stream_key=k%2F%2B%3D",
  );
});

test("omits query without key", () => {
  assert.equal(
    buildStreamUrl("ws://h/api/stream/native", "s1"),
    "ws://h/api/stream/native/s1",
  );
});
