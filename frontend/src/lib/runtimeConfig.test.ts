import assert from "node:assert/strict";
import { test } from "node:test";

import {
  parsePublicRuntimeConfig,
  isTrustedApiDestination,
  apiUrl,
} from "./runtimeConfig.ts";

test("production bypass is disabled for every input including 1", () => {
  const base = {
    NODE_ENV: "production",
    NEXT_PUBLIC_AUTH_BYPASS: "1",
    NEXT_PUBLIC_FIREBASE_API_KEY: "AIza" + "A".repeat(35),
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "tars-pilot.firebaseapp.com",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "pilot-proj-1",
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "tars-pilot.appspot.com",
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
    NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abcdef0123456789",
    NEXT_PUBLIC_API_URL: "https://backend.tars-pilot.org",
    NEXT_PUBLIC_WS_URL: "wss://backend.tars-pilot.org/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "wss://backend.tars-pilot.org/api/stream/native",
  };

  const config = parsePublicRuntimeConfig(base);
  assert.equal(config.ok, true);
  if (config.ok) {
    assert.equal(config.value.authBypassEnabled, false);
  }
});

test("development bypass enabled only for exact 1", () => {
  const devBase = {
    NODE_ENV: "development",
    NEXT_PUBLIC_AUTH_BYPASS: "1",
  };
  const config = parsePublicRuntimeConfig(devBase);
  assert.equal(config.ok, true);
  if (config.ok) {
    assert.equal(config.value.authBypassEnabled, true);
  }

  const devZero = parsePublicRuntimeConfig({
    NODE_ENV: "development",
    NEXT_PUBLIC_AUTH_BYPASS: "0",
  });
  // Without Firebase config and bypass=0, config is invalid
  assert.equal(devZero.ok, false);
});

test("production missing URLs or insecure URLs fail", () => {
  const prodInsecure = {
    NODE_ENV: "production",
    NEXT_PUBLIC_AUTH_BYPASS: "0",
    NEXT_PUBLIC_FIREBASE_API_KEY: "AIza" + "A".repeat(35),
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "tars-pilot.firebaseapp.com",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "pilot-proj-1",
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "tars-pilot.appspot.com",
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
    NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abcdef0123456789",
    NEXT_PUBLIC_API_URL: "http://backend.tars-pilot.org",
    NEXT_PUBLIC_WS_URL: "ws://backend.tars-pilot.org/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "ws://backend.tars-pilot.org/api/stream/native",
  };

  const res = parsePublicRuntimeConfig(prodInsecure);
  assert.equal(res.ok, false);
});

test("production rejects localhost or loopback", () => {
  const prodLoopback = {
    NODE_ENV: "production",
    NEXT_PUBLIC_AUTH_BYPASS: "0",
    NEXT_PUBLIC_FIREBASE_API_KEY: "AIza" + "A".repeat(35),
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "tars-pilot.firebaseapp.com",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "pilot-proj-1",
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "tars-pilot.appspot.com",
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
    NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abcdef0123456789",
    NEXT_PUBLIC_API_URL: "https://127.0.0.1:8000",
    NEXT_PUBLIC_WS_URL: "wss://127.0.0.1:8000/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "wss://127.0.0.1:8000/api/stream/native",
  };

  const res = parsePublicRuntimeConfig(prodLoopback);
  assert.equal(res.ok, false);
});

test("production URL hostname and port consistency", () => {
  const prodInconsistent = {
    NODE_ENV: "production",
    NEXT_PUBLIC_AUTH_BYPASS: "0",
    NEXT_PUBLIC_FIREBASE_API_KEY: "AIza" + "A".repeat(35),
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "tars-pilot.firebaseapp.com",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "pilot-proj-1",
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "tars-pilot.appspot.com",
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
    NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abcdef0123456789",
    NEXT_PUBLIC_API_URL: "https://backend-a.tars-pilot.org",
    NEXT_PUBLIC_WS_URL: "wss://backend-b.tars-pilot.org/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "wss://backend-a.tars-pilot.org/api/stream/native",
  };

  const res = parsePublicRuntimeConfig(prodInconsistent);
  assert.equal(res.ok, false);
});

test("Firebase public config field grammars", () => {
  const base = {
    NODE_ENV: "production",
    NEXT_PUBLIC_AUTH_BYPASS: "0",
    NEXT_PUBLIC_FIREBASE_API_KEY: "AIza" + "A".repeat(35),
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "tars-pilot.firebaseapp.com",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "pilot-proj-1",
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "tars-pilot.appspot.com",
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
    NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abcdef0123456789",
    NEXT_PUBLIC_API_URL: "https://backend.tars-pilot.org",
    NEXT_PUBLIC_WS_URL: "wss://backend.tars-pilot.org/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "wss://backend.tars-pilot.org/api/stream/native",
  };

  // Bad API key
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_API_KEY: "bad_key" }).ok, false);
  // Bad Auth Domain (single label or containing example)
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "singlelabel" }).ok, false);
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "auth.example.com" }).ok, false);
  // Bad Storage Bucket containing example
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "bucket.example.com" }).ok, false);
  // Bad Project ID (too short or containing example)
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_PROJECT_ID: "p" }).ok, false);
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_PROJECT_ID: "example-proj" }).ok, false);
  // Bad Sender ID
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "abc" }).ok, false);
  // Bad App ID
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_APP_ID: "2:web:123" }).ok, false);
});

test("isTrustedApiDestination and apiUrl helper", () => {
  const base = {
    NODE_ENV: "production",
    NEXT_PUBLIC_AUTH_BYPASS: "0",
    NEXT_PUBLIC_FIREBASE_API_KEY: "AIza" + "A".repeat(35),
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "auth.ellaexecutivesearch.com",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "pilot-proj-1",
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "bucket.ellaexecutivesearch.com",
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
    NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abcdef0123456789",
    NEXT_PUBLIC_API_URL: "https://backend.ellaexecutivesearch.com",
    NEXT_PUBLIC_WS_URL: "wss://backend.ellaexecutivesearch.com/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "wss://backend.ellaexecutivesearch.com/api/stream/native",
  };

  const parsed = parsePublicRuntimeConfig(base);
  assert.equal(parsed.ok, true);
  if (parsed.ok) {
    const config = parsed.value;
    assert.equal(apiUrl("/api/me", config), "https://backend.ellaexecutivesearch.com/api/me");
    assert.equal(isTrustedApiDestination("https://backend.ellaexecutivesearch.com/api/me", config), true);
    assert.equal(isTrustedApiDestination("https://backend.ellaexecutivesearch.com/api/sessions/s1", config), true);
    assert.equal(isTrustedApiDestination("https://attacker.com/api/me", config), false);
    assert.equal(isTrustedApiDestination("https://backend.ellaexecutivesearch.com/not-api", config), false);

    // Fixes-2/3 apiUrl rejection cases
    assert.throws(() => apiUrl("api/me", config)); // slashless rejected
    assert.throws(() => apiUrl("/api/..%2fsecret", config)); // traversal
    assert.throws(() => apiUrl("/api/../secret", config)); // traversal
    assert.throws(() => apiUrl("/api/./secret", config)); // dot segment
    assert.throws(() => apiUrl("/api/me#fragment", config)); // fragment rejected
    assert.throws(() => apiUrl("/api/me\\something", config)); // backslash rejected
    assert.throws(() => apiUrl("/api/me%5csomething", config)); // encoded backslash rejected
    assert.throws(() => apiUrl("//backend.ellaexecutivesearch.com/api/me", config)); // protocol-relative rejected
    assert.throws(() => apiUrl("/api/@user", config)); // userinfo rejected

    // Fixes-2/3 isTrustedApiDestination confusion cases
    assert.equal(isTrustedApiDestination("https://backend.ellaexecutivesearch.com/api/me#fragment", config), false);
    assert.equal(isTrustedApiDestination("https://backend.ellaexecutivesearch.com/api/../not-api", config), false);
    assert.equal(isTrustedApiDestination("https://backend.ellaexecutivesearch.com/api/./me", config), false);
    assert.equal(isTrustedApiDestination("https://backend.ellaexecutivesearch.com/api/%2e%2e/not-api", config), false);
    assert.equal(isTrustedApiDestination("https://backend.ellaexecutivesearch.com/api/me\\x", config), false);
    assert.equal(isTrustedApiDestination("https://backend.ellaexecutivesearch.com/api/me%5cx", config), false);
    assert.equal(isTrustedApiDestination("https://user:pass@backend.ellaexecutivesearch.com/api/me", config), false);
    assert.equal(isTrustedApiDestination("//backend.ellaexecutivesearch.com/api/me", config), false);
  }
});

test("Fixes-2/3 grammar boundary checks", () => {
  const base = {
    NODE_ENV: "development",
    NEXT_PUBLIC_AUTH_BYPASS: "0",
    NEXT_PUBLIC_FIREBASE_API_KEY: "AIza" + "A".repeat(35),
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "auth.ellaexecutivesearch.com",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "pilot-proj-1",
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "bucket.ellaexecutivesearch.com",
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
    NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abcdef0123456789",
    NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
    NEXT_PUBLIC_WS_URL: "ws://127.0.0.1:8000/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "ws://127.0.0.1:8000/api/stream/native",
  };

  // Digit-led project ID rejected
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_PROJECT_ID: "1pilot-proj" }).ok, false);

  // App ID 8-char suffix accepted, 7-char rejected
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456:web:12345678" }).ok, true);
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456:web:1234567" }).ok, false);

  // Placeholders rejected
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "your-project.firebaseapp.com" }).ok, false);
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "<your-bucket>" }).ok, false);
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "auth.example.com" }).ok, false);

  // URL checks: 0.0.0.0 rejected, empty port rejected, FTP rejected
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_API_URL: "http://0.0.0.0:8000" }).ok, false);
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_API_URL: "http://127.0.0.1:" }).ok, false);
  assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_API_URL: "ftp://127.0.0.1:8000" }).ok, false);

  // Section C: Exact loopback spelling checks (alternate spellings rejected)
  const rejectedDevUrls = [
    "http://127.1:8000",
    "http://2130706433:8000",
    "http://0177.0.0.1:8000",
    "http://127.000.000.001:8000",
    "http://127.0.0.1.:8000",
    "http://LOCALHOST:8000",
    "http://Localhost:8000",
    "http://[0:0:0:0:0:0:0:1]:8000",
    "HTTP://127.0.0.1:8000",
  ];
  for (const badUrl of rejectedDevUrls) {
    assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_API_URL: badUrl }).ok, false);
    const wsPrefix = badUrl.startsWith("HTTP:") ? "WS:" : "ws:";
    const wsUrl = badUrl.replace(/^[a-zA-Z]+:/, wsPrefix) + "/ws";
    const wsStreamUrl = badUrl.replace(/^[a-zA-Z]+:/, wsPrefix) + "/api/stream/native";
    assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_WS_URL: wsUrl }).ok, false);
    assert.equal(parsePublicRuntimeConfig({ ...base, NEXT_PUBLIC_WS_STREAM_URL: wsStreamUrl }).ok, false);
  }

  // Section A: Single-property mutation tables for all three URLs against valid baseline
  const validApi = "http://127.0.0.1:8000";
  const validWs = "ws://127.0.0.1:8000/ws";
  const validWsStream = "ws://127.0.0.1:8000/api/stream/native";

  const badUrlMutations = [
    // userinfo
    "http://user:pass@127.0.0.1:8000",
    // empty/nonempty ? and #
    "http://127.0.0.1:8000?",
    "http://127.0.0.1:8000#",
    "http://127.0.0.1:8000?probe=1",
    "http://127.0.0.1:8000#section",
    // percent and backslash
    "http://127.0.0.1:8000%2f",
    "http://127.0.0.1:8000\\api",
    // controls and non-ASCII
    "http://127.0.0.1:8000\x00",
    "http://127.0.0.1:8000\x1f",
    "http://127.0.0.1:8000/café",
    // padding
    " http://127.0.0.1:8000",
    "http://127.0.0.1:8000 ",
    // empty/malformed port
    "http://127.0.0.1:",
    "http://127.0.0.1:abc",
    // scheme case tricks
    "HTTP://127.0.0.1:8000",
    "Http://127.0.0.1:8000",
    // alternate loopback spellings
    "http://127.1:8000",
    "http://2130706433:8000",
    "http://0177.0.0.1:8000",
    "http://127.000.000.001:8000",
    "http://127.0.0.1.:8000",
    "http://LOCALHOST:8000",
    "http://Localhost:8000",
    "http://[0:0:0:0:0:0:0:1]:8000",
    // raw dot segments
    "http://127.0.0.1:8000/.",
    "http://127.0.0.1:8000/segment/..",
  ];

  for (const badApi of badUrlMutations) {
    assert.equal(
      parsePublicRuntimeConfig({
        ...base,
        NEXT_PUBLIC_API_URL: badApi,
        NEXT_PUBLIC_WS_URL: validWs,
        NEXT_PUBLIC_WS_STREAM_URL: validWsStream,
      }).ok,
      false
    );
  }

  const badWsMutations = [
    "ws://user:pass@127.0.0.1:8000/ws",
    "ws://127.0.0.1:8000/ws?",
    "ws://127.0.0.1:8000/ws#",
    "ws://127.0.0.1:8000/ws?probe=1",
    "ws://127.0.0.1:8000/ws#frag",
    "ws://127.0.0.1:8000%2fws",
    "ws://127.0.0.1:8000\\ws",
    "ws://127.0.0.1:8000/ws\x00",
    "ws://127.0.0.1:8000/ws/café",
    " ws://127.0.0.1:8000/ws",
    "ws://127.0.0.1:8000/ws ",
    "ws://127.0.0.1:/ws",
    "ws://127.0.0.1:abc/ws",
    "WS://127.0.0.1:8000/ws",
    "Ws://127.0.0.1:8000/ws",
    "ws://127.1:8000/ws",
    "ws://2130706433:8000/ws",
    "ws://0177.0.0.1:8000/ws",
    "ws://127.000.000.001:8000/ws",
    "ws://127.0.0.1.:8000/ws",
    "ws://LOCALHOST:8000/ws",
    "ws://Localhost:8000/ws",
    "ws://[0:0:0:0:0:0:0:1]:8000/ws",
    "ws://127.0.0.1:8000/ws/.",
    "ws://127.0.0.1:8000/segment/../ws",
  ];

  for (const badWs of badWsMutations) {
    assert.equal(
      parsePublicRuntimeConfig({
        ...base,
        NEXT_PUBLIC_API_URL: validApi,
        NEXT_PUBLIC_WS_URL: badWs,
        NEXT_PUBLIC_WS_STREAM_URL: validWsStream,
      }).ok,
      false
    );
  }

  const badWsStreamMutations = [
    "ws://user:pass@127.0.0.1:8000/api/stream/native",
    "ws://127.0.0.1:8000/api/stream/native?",
    "ws://127.0.0.1:8000/api/stream/native#",
    "ws://127.0.0.1:8000/api/stream/native?probe=1",
    "ws://127.0.0.1:8000/api/stream/native#frag",
    "ws://127.0.0.1:8000%2fapi/stream/native",
    "ws://127.0.0.1:8000\\api/stream/native",
    "ws://127.0.0.1:8000/api/stream/native\x00",
    "ws://127.0.0.1:8000/api/stream/native/café",
    " ws://127.0.0.1:8000/api/stream/native",
    "ws://127.0.0.1:8000/api/stream/native ",
    "ws://127.0.0.1:/api/stream/native",
    "ws://127.0.0.1:abc/api/stream/native",
    "WS://127.0.0.1:8000/api/stream/native",
    "Ws://127.0.0.1:8000/api/stream/native",
    "ws://127.1:8000/api/stream/native",
    "ws://2130706433:8000/api/stream/native",
    "ws://0177.0.0.1:8000/api/stream/native",
    "ws://127.000.000.001:8000/api/stream/native",
    "ws://127.0.0.1.:8000/api/stream/native",
    "ws://LOCALHOST:8000/api/stream/native",
    "ws://Localhost:8000/api/stream/native",
    "ws://[0:0:0:0:0:0:0:1]:8000/api/stream/native",
    "ws://127.0.0.1:8000/api/stream/native/.",
    "ws://127.0.0.1:8000/segment/../api/stream/native",
  ];

  for (const badStream of badWsStreamMutations) {
    assert.equal(
      parsePublicRuntimeConfig({
        ...base,
        NEXT_PUBLIC_API_URL: validApi,
        NEXT_PUBLIC_WS_URL: validWs,
        NEXT_PUBLIC_WS_STREAM_URL: badStream,
      }).ok,
      false
    );
  }

  // Accepted dev loopback literals: localhost, 127.0.0.1, [::1]
  assert.equal(
    parsePublicRuntimeConfig({
      ...base,
      NEXT_PUBLIC_API_URL: "http://localhost:8000",
      NEXT_PUBLIC_WS_URL: "ws://localhost:8000/ws",
      NEXT_PUBLIC_WS_STREAM_URL: "ws://localhost:8000/api/stream/native",
    }).ok,
    true
  );
  assert.equal(
    parsePublicRuntimeConfig({
      ...base,
      NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
      NEXT_PUBLIC_WS_URL: "ws://127.0.0.1:8000/ws",
      NEXT_PUBLIC_WS_STREAM_URL: "ws://127.0.0.1:8000/api/stream/native",
    }).ok,
    true
  );
  assert.equal(
    parsePublicRuntimeConfig({
      ...base,
      NEXT_PUBLIC_API_URL: "http://[::1]:8000",
      NEXT_PUBLIC_WS_URL: "ws://[::1]:8000/ws",
      NEXT_PUBLIC_WS_STREAM_URL: "ws://[::1]:8000/api/stream/native",
    }).ok,
    true
  );
});

test("Section G: Firebase App and Auth Named App Matrix", async () => {
  const { getFirebaseApp, getFirebaseAuth, FirebaseInitializationError, APP_NAME } = await import("./firebase.ts");

  const validConfigRes = parsePublicRuntimeConfig({
    NODE_ENV: "development",
    NEXT_PUBLIC_AUTH_BYPASS: "0",
    NEXT_PUBLIC_FIREBASE_API_KEY: "AIza" + "A".repeat(35),
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "auth.ellaexecutivesearch.com",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "pilot-proj-1",
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "bucket.ellaexecutivesearch.com",
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
    NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abcdef0123456789",
    NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
    NEXT_PUBLIC_WS_URL: "ws://127.0.0.1:8000/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "ws://127.0.0.1:8000/api/stream/native",
  });
  assert.equal(validConfigRes.ok, true);
  if (!validConfigRes.ok) return;
  const validConfig = validConfigRes.value;

  const validOptions = { ...validConfig.firebase! };

  // 1. Invalid config makes 0 SDK calls
  let getAppsCalled = false;
  let initAppCalled = false;
  const invalidConfigRes = { ok: false as const, error: "bad config" };
  const nullApp = getFirebaseApp(
    {
      getApps: () => { getAppsCalled = true; return []; },
      initializeApp: () => { initAppCalled = true; return {} as any; },
    },
    { ...validConfig, authBypassEnabled: true }
  );
  assert.equal(nullApp, null);
  assert.equal(getAppsCalled, false);
  assert.equal(initAppCalled, false);

  // 2. Default/unrelated apps in getApps -> calls initializeApp with APP_NAME exactly once
  let createdApp: any = null;
  const unrelatedApps = [
    { name: "[DEFAULT]", options: { ...validOptions, projectId: "other-proj" } },
    { name: "other-app", options: { ...validOptions } },
  ];
  let initCount = 0;
  const app1 = getFirebaseApp(
    {
      getApps: () => unrelatedApps as any,
      initializeApp: (opts, name) => {
        initCount++;
        assert.equal(name, APP_NAME);
        assert.deepEqual(opts, validOptions);
        createdApp = { name, options: opts };
        return createdApp;
      },
    },
    validConfig
  );
  assert.equal(initCount, 1);
  assert.equal(app1, createdApp);

  // 3. Exact 6-option matching named app reuse
  const existingNamedApp = { name: APP_NAME, options: { ...validOptions } };
  let initCalledOnReuse = false;
  const appReused = getFirebaseApp(
    {
      getApps: () => [existingNamedApp] as any,
      initializeApp: () => { initCalledOnReuse = true; return {} as any; },
    },
    validConfig
  );
  assert.equal(appReused, existingNamedApp);
  assert.equal(initCalledOnReuse, false);

  // 4. Mutate / remove each of the 6 options one at a time -> throws FirebaseInitializationError
  const fields = ["apiKey", "authDomain", "projectId", "storageBucket", "messagingSenderId", "appId"] as const;
  for (const field of fields) {
    const mutatedOptions = { ...validOptions, [field]: "mismatched-val" };
    assert.throws(
      () =>
        getFirebaseApp(
          {
            getApps: () => [{ name: APP_NAME, options: mutatedOptions }] as any,
            initializeApp: () => ({}) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError && err.message.includes("options mismatch")
    );

    const missingOptions = { ...validOptions };
    delete (missingOptions as any)[field];
    assert.throws(
      () =>
        getFirebaseApp(
          {
            getApps: () => [{ name: APP_NAME, options: missingOptions }] as any,
            initializeApp: () => ({}) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError && err.message.includes("options mismatch")
    );
  }

  // 5. Injected getApps / initializeApp / getAuth sentinels scrubbed
  const sentinel = "SUPER_SECRET_FIREBASE_SDK_SENTINEL_555";
  assert.throws(
    () =>
      getFirebaseApp(
        {
          getApps: () => { throw new Error(sentinel); },
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && !err.message.includes(sentinel) && err.cause === undefined
  );

  assert.throws(
    () =>
      getFirebaseApp(
        {
          getApps: () => [],
          initializeApp: () => { throw new Error(sentinel); },
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && !err.message.includes(sentinel) && err.cause === undefined
  );

  assert.throws(
    () =>
      getFirebaseAuth(
        {
          getApp: () => ({ name: APP_NAME, options: validOptions }) as any,
          getAuth: () => { throw new Error(sentinel); },
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && !err.message.includes(sentinel) && err.cause === undefined
  );

  // 6. Hostile Proxy option getters throwing same-class FirebaseInitializationError
  const hostileOptionsProxy = new Proxy({ ...validOptions }, {
    get(target, prop) {
      if (prop === "apiKey") {
        throw new FirebaseInitializationError("RAW_OPTION_SENTINEL");
      }
      return (target as any)[prop];
    },
  });

  // For existing app
  assert.throws(
    () =>
      getFirebaseApp(
        {
          getApps: () => [{ name: APP_NAME, options: hostileOptionsProxy }] as any,
          initializeApp: () => ({}) as any,
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && !err.message.includes("RAW_OPTION_SENTINEL") && err.cause === undefined
  );

  // For created app
  assert.throws(
    () =>
      getFirebaseApp(
        {
          getApps: () => [],
          initializeApp: () => ({ name: APP_NAME, options: hostileOptionsProxy }) as any,
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && !err.message.includes("RAW_OPTION_SENTINEL") && err.cause === undefined
  );

  // For auth app
  assert.throws(
    () =>
      getFirebaseAuth(
        {
          getApp: () => ({ name: APP_NAME, options: hostileOptionsProxy }) as any,
          getAuth: () => ({}) as any,
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && !err.message.includes("RAW_OPTION_SENTINEL") && err.cause === undefined
  );

  // 7. All 6 options independently missing, wrong, or changing for getFirebaseAuth
  for (const k of fields) {
    // Missing
    const missingOptions = { ...validOptions };
    delete (missingOptions as any)[k];

    assert.throws(
      () =>
        getFirebaseAuth(
          {
            getApp: () => ({ name: APP_NAME, options: missingOptions }) as any,
            getAuth: () => ({}) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError && err.message.includes("options mismatch")
    );

    // Wrong value
    const wrongOptions = { ...validOptions, [k]: "wrong-option-val" };
    assert.throws(
      () =>
        getFirebaseAuth(
          {
            getApp: () => ({ name: APP_NAME, options: wrongOptions }) as any,
            getAuth: () => ({}) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError && err.message.includes("options mismatch")
    );

    // Throwing / changing option getter
    const throwingOptions = {
      ...validOptions,
      get [k]() {
        throw new Error("CHANGING_OPTION_SENTINEL");
      },
    };
    assert.throws(
      () =>
        getFirebaseAuth(
          {
            getApp: () => ({ name: APP_NAME, options: throwingOptions }) as any,
            getAuth: () => ({}) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError && !err.message.includes("CHANGING_OPTION_SENTINEL")
    );
  }

  // 8. All 6 options independently missing, wrong, or throwing for createdApp
  for (const k of fields) {
    // Missing
    const missingCreatedOpts = { ...validOptions };
    delete (missingCreatedOpts as any)[k];

    assert.throws(
      () =>
        getFirebaseApp(
          {
            getApps: () => [],
            initializeApp: () => ({ name: APP_NAME, options: missingCreatedOpts }) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError && err.message.includes("initialization failed")
    );

    // Wrong
    const wrongCreatedOpts = { ...validOptions, [k]: "wrong-created-val" };
    assert.throws(
      () =>
        getFirebaseApp(
          {
            getApps: () => [],
            initializeApp: () => ({ name: APP_NAME, options: wrongCreatedOpts }) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError && err.message.includes("initialization failed")
    );

    // Throwing / changing
    const throwingCreatedOpts = {
      ...validOptions,
      get [k]() {
        throw new Error("CHANGING_CREATED_OPTION_SENTINEL");
      },
    };
    assert.throws(
      () =>
        getFirebaseApp(
          {
            getApps: () => [],
            initializeApp: () => ({ name: APP_NAME, options: throwingCreatedOpts }) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError && !err.message.includes("CHANGING_CREATED_OPTION_SENTINEL")
    );
  }

  // 9. Wrong / blank / non-string / throwing name for created app and auth app
  const badNames = ["wrong-app-name", "", 12345, null, undefined];
  for (const badName of badNames) {
    // Created app with bad name
    assert.throws(
      () =>
        getFirebaseApp(
          {
            getApps: () => [],
            initializeApp: () => ({ name: badName, options: validOptions }) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError && err.message.includes("initialization failed")
    );

    // Auth app with bad name
    assert.throws(
      () =>
        getFirebaseAuth(
          {
            getApp: () => ({ name: badName, options: validOptions }) as any,
            getAuth: () => ({}) as any,
          },
          validConfig
        ),
      (err: any) => err instanceof FirebaseInitializationError
    );
  }

  // Throwing name getter
  const throwingNameApp = {
    get name() { throw new Error("THROWING_NAME_SENTINEL"); },
    options: validOptions,
  };
  assert.throws(
    () =>
      getFirebaseApp(
        {
          getApps: () => [],
          initializeApp: () => throwingNameApp as any,
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && !err.message.includes("THROWING_NAME_SENTINEL")
  );

  assert.throws(
    () =>
      getFirebaseAuth(
        {
          getApp: () => throwingNameApp as any,
          getAuth: () => ({}) as any,
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && !err.message.includes("THROWING_NAME_SENTINEL")
  );

  // 10. Comprehensive Change-on-read matrix across 3 modes x (name + 6 options) x 2 sequences
  const testModes: ("existing_list" | "created" | "auth")[] = ["existing_list", "created", "auth"];
  const allTestKeys = ["name", ...fields] as const;

  for (const mode of testModes) {
    for (const testKey of allTestKeys) {
      for (const sequence of ["correct-then-changing", "changing-then-correct"] as const) {
        let readCount = 0;
        let initAppCalls = 0;
        let getAuthCalls = 0;

        const dynamicApp = {
          get name() {
            if (testKey === "name") {
              readCount++;
              if (sequence === "correct-then-changing") {
                return readCount === 1 ? APP_NAME : "mutated-name-on-second-read";
              } else {
                return readCount === 1 ? "wrong-name-on-first-read" : APP_NAME;
              }
            }
            return APP_NAME;
          },
          options: {
            ...validOptions,
            get [testKey === "name" ? "apiKey" : testKey]() {
              if (testKey !== "name") {
                readCount++;
                const expectedVal = validOptions[testKey];
                if (sequence === "correct-then-changing") {
                  return readCount === 1 ? expectedVal : "mutated-opt-on-second-read";
                } else {
                  return readCount === 1 ? "wrong-opt-on-first-read" : expectedVal;
                }
              }
              return validOptions.apiKey;
            },
          },
        };

        let capturedErr: any = null;
        if (mode === "existing_list") {
          assert.throws(
            () => {
              getFirebaseApp(
                {
                  getApps: () => [dynamicApp] as any,
                  initializeApp: () => {
                    initAppCalls++;
                    return {} as any;
                  },
                },
                validConfig
              );
            },
            (err: any) => {
              capturedErr = err;
              return (
                err instanceof FirebaseInitializationError &&
                (err.message.includes("options mismatch") || err.message.includes("shape invalid")) &&
                err.cause === undefined
              );
            },
            `Mode ${mode} key ${testKey} sequence ${sequence} must throw FirebaseInitializationError`
          );
          assert.equal(initAppCalls, 0, `Mode ${mode} key ${testKey} must never call initializeApp`);
        } else if (mode === "created") {
          assert.throws(
            () => {
              getFirebaseApp(
                {
                  getApps: () => [],
                  initializeApp: () => {
                    initAppCalls++;
                    return dynamicApp as any;
                  },
                },
                validConfig
              );
            },
            (err: any) => {
              capturedErr = err;
              return (
                err instanceof FirebaseInitializationError &&
                err.message.includes("initialization failed") &&
                err.cause === undefined
              );
            },
            `Mode ${mode} key ${testKey} sequence ${sequence} must throw FirebaseInitializationError`
          );
          assert.equal(initAppCalls, 1, `Mode ${mode} key ${testKey} must call initializeApp exactly once`);
        } else if (mode === "auth") {
          assert.throws(
            () => {
              getFirebaseAuth(
                {
                  getApp: () => dynamicApp as any,
                  getAuth: () => {
                    getAuthCalls++;
                    return {} as any;
                  },
                },
                validConfig
              );
            },
            (err: any) => {
              capturedErr = err;
              return (
                err instanceof FirebaseInitializationError &&
                err.message.includes("options mismatch") &&
                err.cause === undefined
              );
            },
            `Mode ${mode} key ${testKey} sequence ${sequence} must throw FirebaseInitializationError`
          );
          assert.equal(getAuthCalls, 0, `Mode ${mode} key ${testKey} must never call getAuth`);
        }

        assert.ok(capturedErr, "Error must be captured");
        assert.equal(capturedErr.cause, undefined);
        const ownProps = Object.getOwnPropertyNames(capturedErr);
        assert.equal(ownProps.includes("sentinelProp"), false);
      }
    }
  }

  // 10c. Stable unrelated apps are skipped without reading their options
  let unrelatedOptRead = false;
  const unrelatedWithHostileOpts = {
    name: "unrelated-app-identifier",
    get options() {
      unrelatedOptRead = true;
      throw new Error("Hostile unrelated options should not be read");
    },
  };
  const matchingApp = { name: APP_NAME, options: validOptions };
  const foundReused = getFirebaseApp(
    {
      getApps: () => [unrelatedWithHostileOpts, matchingApp] as any,
      initializeApp: () => ({}) as any,
    },
    validConfig
  );
  assert.equal(foundReused, matchingApp);
  assert.equal(unrelatedOptRead, false, "Options on unrelated apps must not be read");

  // 11. Null / non-object entries in getApps() or null returns from initializeApp/getAuth
  assert.throws(
    () =>
      getFirebaseApp(
        {
          getApps: () => [null] as any,
          initializeApp: () => ({}) as any,
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && err.message.includes("shape invalid")
  );

  assert.throws(
    () =>
      getFirebaseApp(
        {
          getApps: () => ["invalid-string-item"] as any,
          initializeApp: () => ({}) as any,
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && err.message.includes("shape invalid")
  );

  assert.throws(
    () =>
      getFirebaseApp(
        {
          getApps: () => [],
          initializeApp: () => null as any,
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && err.message.includes("initialization failed")
  );

  assert.throws(
    () =>
      getFirebaseAuth(
        {
          getApp: () => null as any,
          getAuth: () => ({}) as any,
        },
        validConfig
      ),
    (err: any) => err instanceof FirebaseInitializationError && err.message.includes("lookup failed")
  );
});

test("Section B.4: Causal raw scheme mutation matrices for production and development baselines", () => {
  const baseFirebase = {
    NEXT_PUBLIC_FIREBASE_API_KEY: "AIza" + "A".repeat(35),
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "auth.ellaexecutivesearch.com",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "pilot-proj-1",
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "bucket.ellaexecutivesearch.com",
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
    NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abcdef0123456789",
  };

  // 1. Production baseline passes immediately
  const prodBase = {
    ...baseFirebase,
    NEXT_PUBLIC_API_URL: "https://backend.ellaexecutivesearch.com",
    NEXT_PUBLIC_WS_URL: "wss://backend.ellaexecutivesearch.com/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "wss://backend.ellaexecutivesearch.com/api/stream/native",
  };
  const prodRes = parsePublicRuntimeConfig(prodBase);
  assert.equal(prodRes.ok, true);

  // Mutate each target raw scheme to uppercase or mixed-case against production baseline
  const prodSchemeMutations = [
    { field: "NEXT_PUBLIC_API_URL", badValues: ["HTTPS://backend.ellaexecutivesearch.com", "Https://backend.ellaexecutivesearch.com"] },
    { field: "NEXT_PUBLIC_WS_URL", badValues: ["WSS://backend.ellaexecutivesearch.com/ws", "Wss://backend.ellaexecutivesearch.com/ws"] },
    { field: "NEXT_PUBLIC_WS_STREAM_URL", badValues: ["WSS://backend.ellaexecutivesearch.com/api/stream/native", "Wss://backend.ellaexecutivesearch.com/api/stream/native"] },
  ];

  for (const item of prodSchemeMutations) {
    for (const badVal of item.badValues) {
      const mutRes = parsePublicRuntimeConfig({
        ...prodBase,
        [item.field]: badVal,
      });
      assert.equal(mutRes.ok, false, `Expected ${item.field}=${badVal} to fail`);
      if (!mutRes.ok) {
        assert.match(mutRes.error, /Scheme must be exact lowercase/);
      }
    }
  }

  // 2. Development baseline passes immediately
  const devBase = {
    ...baseFirebase,
    NEXT_PUBLIC_API_URL: "http://localhost:8000",
    NEXT_PUBLIC_WS_URL: "ws://localhost:8000/ws",
    NEXT_PUBLIC_WS_STREAM_URL: "ws://localhost:8000/api/stream/native",
  };
  const devRes = parsePublicRuntimeConfig(devBase);
  assert.equal(devRes.ok, true);

  // Mutate each target raw scheme to uppercase or mixed-case against development baseline
  const devSchemeMutations = [
    { field: "NEXT_PUBLIC_API_URL", badValues: ["HTTP://localhost:8000", "Http://localhost:8000"] },
    { field: "NEXT_PUBLIC_WS_URL", badValues: ["WS://localhost:8000/ws", "Ws://localhost:8000/ws"] },
    { field: "NEXT_PUBLIC_WS_STREAM_URL", badValues: ["WS://localhost:8000/api/stream/native", "Ws://localhost:8000/api/stream/native"] },
  ];

  for (const item of devSchemeMutations) {
    for (const badVal of item.badValues) {
      const mutRes = parsePublicRuntimeConfig({
        ...devBase,
        [item.field]: badVal,
      });
      assert.equal(mutRes.ok, false, `Expected ${item.field}=${badVal} to fail`);
      if (!mutRes.ok) {
        assert.match(mutRes.error, /Scheme must be exact lowercase/);
      }
    }
  }

  // 3. Safe encoded API path/query helper positives
  if (prodRes.ok) {
    assert.equal(apiUrl("/api/me", prodRes.value), "https://backend.ellaexecutivesearch.com/api/me");
    assert.equal(apiUrl("/api/sessions/s1/notes", prodRes.value), "https://backend.ellaexecutivesearch.com/api/sessions/s1/notes");
    assert.equal(apiUrl("/api/sessions?status=completed&limit=10", prodRes.value), "https://backend.ellaexecutivesearch.com/api/sessions?status=completed&limit=10");
  }
});
