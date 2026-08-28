import assert from "node:assert/strict";
import test from "node:test";
import {
  IAP_API_ORIGIN,
  IAP_FRONTEND_ORIGIN,
  IAP_STREAM_WS_URL,
  IAP_WS_URL,
  parseRuntimeConfig,
} from "./runtimeConfig.ts";

const iapEnv = {
  NEXT_PUBLIC_AUTH_MODE: "iap",
  NEXT_PUBLIC_API_URL: IAP_API_ORIGIN,
  NEXT_PUBLIC_WS_URL: IAP_WS_URL,
  NEXT_PUBLIC_WS_STREAM_URL: IAP_STREAM_WS_URL,
  NEXT_PUBLIC_FRONTEND_ORIGIN: IAP_FRONTEND_ORIGIN,
};

test("IAP runtime config accepts only the approved direct topology", () => {
  const config = parseRuntimeConfig(iapEnv);
  assert.equal(config.iap, true);
  assert.equal(config.credentials, "include");
  assert.equal(config.apiOrigin, IAP_API_ORIGIN);
  assert.equal(config.wsUrl, IAP_WS_URL);
  assert.equal(config.streamWsUrl, IAP_STREAM_WS_URL);
});

for (const [name, key, value] of [
  ["loopback API", "NEXT_PUBLIC_API_URL", "http://127.0.0.1:8000"],
  ["HTTP API", "NEXT_PUBLIC_API_URL", "http://api.tars.ellaexecutivesearch.com"],
  ["WS instead of WSS", "NEXT_PUBLIC_WS_URL", "ws://api.tars.ellaexecutivesearch.com/ws"],
  ["proxy-relative WS", "NEXT_PUBLIC_WS_URL", "/ws"],
  ["wrong WS path", "NEXT_PUBLIC_WS_URL", `${IAP_API_ORIGIN}/proxy/ws`],
  ["wrong stream host", "NEXT_PUBLIC_WS_STREAM_URL", "wss://evil.example/api/stream/native"],
  ["query", "NEXT_PUBLIC_API_URL", `${IAP_API_ORIGIN}?x=1`],
  ["credentials", "NEXT_PUBLIC_API_URL", "https://user:pass@api.tars.ellaexecutivesearch.com"],
  ["bypass", "NEXT_PUBLIC_AUTH_BYPASS", "1"],
] as const) {
  test(`IAP rejects ${name}`, () => {
    assert.throws(() => parseRuntimeConfig({ ...iapEnv, [key]: value }));
  });
}

test("Firebase/local defaults remain available", () => {
  const config = parseRuntimeConfig({ NEXT_PUBLIC_AUTH_MODE: "firebase" });
  assert.equal(config.iap, false);
  assert.equal(config.credentials, "include");
  assert.match(config.apiOrigin, /^http:\/\//);
});
