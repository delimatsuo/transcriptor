import assert from "node:assert/strict";
import { test } from "node:test";

import { buildStreamSocketConfig } from "./streamUrl.ts";

test("builds keyless URL and exact protocols tuple", () => {
  const config = buildStreamSocketConfig(
    "ws://127.0.0.1:8000/api/stream/native",
    "sess-123",
    "secret-key-abc",
    ["microphone"],
  );
  assert.equal(config.url, "ws://127.0.0.1:8000/api/stream/native/sess-123");
  assert.deepEqual(config.protocols, ["tars-stream", "secret-key-abc"]);
  assert.ok(!config.url.includes("secret-key-abc"));
  assert.ok(!config.url.includes("?"));
});

test("formats microphone hello as first application message", () => {
  const config = buildStreamSocketConfig(
    "ws://127.0.0.1:8000/api/stream/native",
    "sess-123",
    "secret-key-abc",
    ["microphone"],
  );
  const parsed = JSON.parse(config.hello);
  assert.deepEqual(parsed, {
    type: "hello",
    sources: ["microphone"],
  });
});

test("canonicalizes and deduplicates two-source hello", () => {
  const config = buildStreamSocketConfig(
    "ws://127.0.0.1:8000/api/stream/native",
    "sess-123",
    "secret-key-abc",
    ["system_audio", "microphone", "system_audio"],
  );
  const parsed = JSON.parse(config.hello);
  assert.deepEqual(parsed, {
    type: "hello",
    sources: ["microphone", "system_audio"],
  });
});

test("rejects missing stream key, empty sources, or invalid sources", () => {
  assert.throws(
    () =>
      buildStreamSocketConfig(
        "ws://127.0.0.1:8000/api/stream/native",
        "sess-123",
        "",
        ["microphone"],
      ),
    /Chave do fluxo de áudio ausente ou inválida/,
  );

  assert.throws(
    () =>
      buildStreamSocketConfig(
        "ws://127.0.0.1:8000/api/stream/native",
        "sess-123",
        "key",
        [],
      ),
    /Nenhuma fonte de áudio especificada/,
  );

  assert.throws(
    () =>
      buildStreamSocketConfig(
        "ws://127.0.0.1:8000/api/stream/native",
        "sess-123",
        "key",
        ["invalid_source" as any],
      ),
    /Fonte de áudio desconhecida/,
  );
});
