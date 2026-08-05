import assert from "node:assert/strict";
import { test } from "node:test";

import { requestSessionStop, type StopFetch } from "./sessionStop.ts";

test("confirmed incomplete stop returns a terminal warning", async () => {
  const fetcher: StopFetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ status: "incomplete" }),
  });

  const outcome = await requestSessionStop(fetcher, "/stop");

  assert.equal(outcome.status, "incomplete");
  assert.match(outcome.warning ?? "", /Transcrição incompleta/);
});

test("non-2xx stop remains unconfirmed so the caller can stay live", async () => {
  const fetcher: StopFetch = async () => ({
    ok: false,
    status: 503,
    json: async () => ({}),
  });

  await assert.rejects(
    requestSessionStop(fetcher, "/stop"),
    /stop failed \(503\)/,
  );
});

test("malformed success remains unconfirmed", async () => {
  const fetcher: StopFetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ status: "active" }),
  });

  await assert.rejects(
    requestSessionStop(fetcher, "/stop"),
    /no terminal status/,
  );
});
