import assert from "node:assert/strict";
import test from "node:test";
import {
  IAP_API_ORIGIN,
  IAP_FRONTEND_ORIGIN,
} from "./runtimeConfig.ts";
import {
  IAP_POLICY_CLOSE_CODE,
  buildIapBootstrapUrl,
  buildIapSignOutUrl,
  fetchIapAdmission,
  IAP_EXPIRY_CLOSE_CODE,
  isIapAttemptCurrent,
  isIapReconnectableClose,
  isIapTerminalClose,
  runIapLogoutLifecycle,
} from "./iapSession.ts";

test("bootstrap and signout URLs are fixed to provider-managed routes", () => {
  assert.equal(
    buildIapBootstrapUrl({ apiOrigin: IAP_API_ORIGIN }),
    `${IAP_API_ORIGIN}/api/auth/bootstrap`,
  );
  assert.equal(
    buildIapSignOutUrl({ apiOrigin: IAP_API_ORIGIN }),
    `${IAP_API_ORIGIN}/?gcp-iap-mode=GCIP_SIGNOUT`,
  );
  assert.notEqual(IAP_FRONTEND_ORIGIN, IAP_API_ORIGIN);
});

test("IAP admission uses credentialed GET and returns only the profile", async () => {
  let called: { input: RequestInfo | URL; init?: RequestInit } | undefined;
  const profile = await fetchIapAdmission(
    { apiOrigin: IAP_API_ORIGIN },
    async (input, init) => {
      called = { input, init };
      return new Response(JSON.stringify({ uid: "u1", email: "a@example.com", org_id: "ella-internal", extra: "ignored" }), { status: 200 });
    },
  );
  assert.deepEqual(profile, { uid: "u1", email: "a@example.com", org_id: "ella-internal" });
  assert.equal(called?.input, `${IAP_API_ORIGIN}/api/me`);
  assert.equal(called?.init?.credentials, "include");
  assert.equal(called?.init?.cache, "no-store");
});

test("invalid admission responses fail closed", async () => {
  const denied = await fetchIapAdmission(
    { apiOrigin: IAP_API_ORIGIN },
    async () => new Response("", { status: 401 }),
  );
  assert.equal(denied, null);
  const malformed = await fetchIapAdmission(
    { apiOrigin: IAP_API_ORIGIN },
    async () => new Response(JSON.stringify({ uid: "u1" }), { status: 200 }),
  );
  assert.equal(malformed, null);
});

test("logout and policy closes are terminal while ordinary expiry reconnects", () => {
  assert.equal(isIapTerminalClose(IAP_POLICY_CLOSE_CODE, "kill_switch"), true);
  assert.equal(isIapTerminalClose(1006, "logout"), true);
  assert.equal(isIapReconnectableClose(IAP_EXPIRY_CLOSE_CODE, "auth_expired"), true);
  assert.equal(isIapReconnectableClose(IAP_POLICY_CLOSE_CODE, "auth_revoked"), false);
});

test("IAP logout cleans up synchronously before request and provider navigation", async () => {
  const order: string[] = [];
  let resolveRequest!: () => void;
  const pending = new Promise<Response>((resolve) => {
    resolveRequest = () => resolve(new Response(null, { status: 204 }));
  });
  const request = runIapLogoutLifecycle(
    { apiOrigin: IAP_API_ORIGIN },
    {
      cleanup: () => order.push("cleanup"),
      navigate: () => order.push("navigate"),
    },
    async () => {
      order.push("request-start");
      return pending;
    },
  );
  assert.deepEqual(order, ["cleanup", "request-start"]);
  resolveRequest();
  await request;
  assert.deepEqual(order, ["cleanup", "request-start", "navigate"]);
});

test("IAP logout navigates after synchronous cleanup even when backend logout fails", async () => {
  const order: string[] = [];
  await assert.rejects(
    runIapLogoutLifecycle(
      { apiOrigin: IAP_API_ORIGIN },
      {
        cleanup: () => order.push("cleanup"),
        navigate: () => order.push("navigate"),
      },
      async () => {
        order.push("request");
        throw new Error("offline");
      },
    ),
  );
  assert.deepEqual(order, ["cleanup", "request", "navigate"]);
});

test("stale IAP stream-ticket attempts cannot create a socket", () => {
  assert.equal(isIapAttemptCurrent(1, 1, "s1", "s1"), true);
  assert.equal(isIapAttemptCurrent(1, 2, "s1", "s1"), false);
  assert.equal(isIapAttemptCurrent(1, 1, "s1", null), false);
  assert.equal(isIapAttemptCurrent(1, 1, "s1", "s2"), false);
});
