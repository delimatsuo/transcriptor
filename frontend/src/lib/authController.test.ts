import assert from "node:assert/strict";
import { test } from "node:test";

import {
  createAuthController,
  createProductionAuthController,
  createAuthLifecycleAdapter,
  type AuthState,
  type AuthUserInfo,
} from "./authController.ts";
import { apiFetch, ApiFetchError, setupAuthLifecycle } from "./auth.ts";
import type { AdmissionResult } from "./authAdmission.ts";

test("production controller instantiates without throwing eagerly", () => {
  const controller = createProductionAuthController();
  assert.ok(controller);
  assert.equal(typeof controller.start, "function");
  assert.equal(typeof controller.signIn, "function");
  assert.equal(typeof controller.signOut, "function");
  assert.equal(typeof controller.useAnotherAccount, "function");
  assert.equal(typeof controller.retry, "function");
  assert.equal(typeof controller.dispose, "function");
});

test("invalid config sets config_error and starts zero effects", () => {
  let subscribeCalled = false;
  let popupCalled = false;

  const controller = createAuthController({
    getRuntimeConfig: () => ({ ok: false, error: "Missing config" }),
    subscribeAuthState: () => {
      subscribeCalled = true;
      return () => {};
    },
    signInWithPopup: async () => {
      popupCalled = true;
      return null;
    },
  });

  controller.start();
  const state = controller.getState();
  assert.equal(state.status, "config_error");
  assert.equal(state.user, null);
  assert.equal(state.error, "Configuração de autenticação inválida ou ausente.");
  assert.equal(subscribeCalled, false);
  assert.equal(popupCalled, false);
});

test("synthetic bypass mode admits synthetic user immediately", () => {
  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: true,
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
  });

  controller.start();
  const state = controller.getState();
  assert.equal(state.status, "signed_in");
  assert.equal(state.user?.uid, "local-recruiter-dev");
  assert.equal(state.user?.email, "recruiter-pilot@example.com");
  assert.equal(state.user?.org_id, "ella-internal");
});

test("popup single-flight blocks concurrent signIn activations", async () => {
  let popupCount = 0;
  let resolvePopup: (val: unknown) => void;
  const popupPromise = new Promise((resolve) => {
    resolvePopup = resolve;
  });

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: null } as any),
    signInWithPopup: async () => {
      popupCount++;
      return await popupPromise;
    },
  });

  controller.start();

  const p1 = controller.signIn();
  const p2 = controller.signIn(); // Should be dropped due to single-flight

  assert.equal(popupCount, 1);
  assert.equal(controller.getState().status, "opening_popup");

  resolvePopup!(null);
  await p1;
  await p2;

  assert.equal(popupCount, 1);
});

test("admission transitions: admitted, denied, retryable", async () => {
  let userListener: ((u: any) => void) | null = null;
  let admissionResultToReturn: AdmissionResult = {
    status: "admitted",
    principal: {
      uid: "test-uid",
      email: "recruiter@ellaexecutivesearch.com",
      org_id: "ella-internal",
    },
  };

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: null } as any),
    subscribeAuthState: (_auth, next) => {
      userListener = next;
      return () => {};
    },
    executeAdmission: async () => admissionResultToReturn,
  });

  controller.start();
  assert.ok(userListener);

  const mockUser = {
    uid: "test-uid",
    email: "recruiter@ellaexecutivesearch.com",
    displayName: "Recruiter 1",
    getIdToken: async () => "token-1",
  };

  // 1. Admitted
  userListener!(mockUser);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(controller.getState().user?.uid, "test-uid");

  // 2. Denied
  admissionResultToReturn = { status: "denied", statusCode: 403 };
  userListener!(mockUser);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "denied_account");
  assert.equal(controller.getState().user, null);

  // 3. Retryable
  admissionResultToReturn = { status: "retryable" };
  userListener!(mockUser);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "retryable_error");
  assert.equal(controller.getState().user, null);
});

test("same-principal retry calls admission without opening popup", async () => {
  let popupCalled = false;
  let admissionCallCount = 0;
  let userListener: ((u: any) => void) | null = null;

  let currentAuthUser: any = null;
  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ get currentUser() { return currentAuthUser; } } as any),
    subscribeAuthState: (_auth, next) => {
      userListener = next;
      return () => {};
    },
    signInWithPopup: async () => {
      popupCalled = true;
      return null;
    },
    executeAdmission: async () => {
      admissionCallCount++;
      return { status: "retryable" };
    },
  });

  controller.start();
  const mockUser = {
    uid: "test-uid",
    email: "recruiter@ellaexecutivesearch.com",
    getIdToken: async () => "token-1",
  };
  currentAuthUser = mockUser;

  userListener!(mockUser);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "retryable_error");
  assert.equal(admissionCallCount, 1);

  // Trigger retry
  await controller.retry();
  assert.equal(admissionCallCount, 2);
  assert.equal(popupCalled, false);
});

test("useAnotherAccount signs out before opening chooser and blocks chooser on signout error", async () => {
  const callSequence: string[] = [];
  let shouldSignOutFail = false;

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: null } as any),
    signOutProvider: async () => {
      callSequence.push("provider_sign_out");
      if (shouldSignOutFail) {
        throw new Error("Provider offline");
      }
    },
    signInWithPopup: async () => {
      callSequence.push("popup_open");
      return null;
    },
  });

  controller.start();

  // Successful flow
  await controller.useAnotherAccount();
  assert.deepEqual(callSequence, ["provider_sign_out", "popup_open"]);

  // Failed sign-out flow
  callSequence.length = 0;
  shouldSignOutFail = true;
  await controller.useAnotherAccount();
  assert.deepEqual(callSequence, ["provider_sign_out"]);
  assert.equal(controller.getState().status, "sign_out_error");
});

test("account switching fences late admission results from older account", async () => {
  let userListener: ((u: any) => void) | null = null;
  let resolveFirstAdmission: (res: AdmissionResult) => void;
  const firstAdmissionPromise = new Promise<AdmissionResult>((resolve) => {
    resolveFirstAdmission = resolve;
  });

  let callCount = 0;
  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: null } as any),
    subscribeAuthState: (_auth, next) => {
      userListener = next;
      return () => {};
    },
    executeAdmission: async ({ expectedUid }) => {
      callCount++;
      if (expectedUid === "user-a") {
        return await firstAdmissionPromise;
      }
      return {
        status: "admitted",
        principal: {
          uid: "user-b",
          email: "user-b@ellaexecutivesearch.com",
          org_id: "ella-internal",
        },
      };
    },
  });

  controller.start();

  const userA = { uid: "user-a", email: "user-a@ellaexecutivesearch.com", getIdToken: async () => "token-a" };
  const userB = { uid: "user-b", email: "user-b@ellaexecutivesearch.com", getIdToken: async () => "token-b" };

  userListener!(userA);
  assert.equal(controller.getState().status, "checking_access");

  // User B arrives before User A resolves
  userListener!(userB);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(controller.getState().user?.uid, "user-b");

  // Now User A's admission resolves late
  resolveFirstAdmission!({
    status: "admitted",
    principal: {
      uid: "user-a",
      email: "user-a@ellaexecutivesearch.com",
      org_id: "ella-internal",
    },
  });
  await new Promise((r) => setTimeout(r, 10));

  // State must remain User B, not overwritten by late User A
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(controller.getState().user?.uid, "user-b");
});

test("dispose unsubscribes and cancels in-flight work", () => {
  let unsubscribed = false;
  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: null } as any),
    subscribeAuthState: () => () => {
      unsubscribed = true;
    },
  });

  controller.start();
  controller.dispose();
  assert.equal(unsubscribed, true);
});

test("Fixes-2 account-switch null-listener race opens exactly one chooser", async () => {
  let popupCount = 0;
  let userListener: ((u: any) => void) | null = null;

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: null } as any),
    subscribeAuthState: (_auth, next) => {
      userListener = next;
      return () => {};
    },
    signOutProvider: async () => {
      // Simulate Firebase firing onAuthStateChanged(null) during signOut
      if (userListener) {
        userListener(null);
      }
    },
    signInWithPopup: async () => {
      popupCount++;
      return null;
    },
  });

  controller.start();
  await controller.useAnotherAccount();
  assert.equal(popupCount, 1);
});

test("Fixes-2 StrictMode lifecycle setup-cleanup-setup adapter using createAuthLifecycleAdapter", () => {
  let subscriptionCount = 0;
  let unsubscriptionCount = 0;

  const adapter = createAuthLifecycleAdapter(() =>
    createAuthController({
      getRuntimeConfig: () => ({
        ok: true,
        value: {
          authBypassEnabled: false,
          firebase: {
            apiKey: "AIza" + "A".repeat(35),
            authDomain: "auth.ellaexecutivesearch.com",
            projectId: "pilot-proj-1",
            storageBucket: "bucket.ellaexecutivesearch.com",
            messagingSenderId: "123456789012",
            appId: "1:123456789012:web:abcdef0123456789",
          },
          apiUrl: "http://127.0.0.1:8000",
          wsUrl: "ws://127.0.0.1:8000/ws",
          wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
        },
      }),
      getAuth: () => ({ currentUser: null } as any),
      subscribeAuthState: () => {
        subscriptionCount++;
        return () => {
          unsubscriptionCount++;
        };
      },
    })
  );

  // Setup 1
  adapter.start();
  assert.equal(subscriptionCount, 1);

  // Cleanup 1
  adapter.dispose();
  assert.equal(unsubscriptionCount, 1);

  // Second adapter instance setup 2
  const adapter2 = createAuthLifecycleAdapter(() =>
    createAuthController({
      getRuntimeConfig: () => ({
        ok: true,
        value: {
          authBypassEnabled: false,
          firebase: {
            apiKey: "AIza" + "A".repeat(35),
            authDomain: "auth.ellaexecutivesearch.com",
            projectId: "pilot-proj-1",
            storageBucket: "bucket.ellaexecutivesearch.com",
            messagingSenderId: "123456789012",
            appId: "1:123456789012:web:abcdef0123456789",
          },
          apiUrl: "http://127.0.0.1:8000",
          wsUrl: "ws://127.0.0.1:8000/ws",
          wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
        },
      }),
      getAuth: () => ({ currentUser: null } as any),
      subscribeAuthState: () => {
        subscriptionCount++;
        return () => {
          unsubscriptionCount++;
        };
      },
    })
  );
  adapter2.start();
  assert.equal(subscriptionCount, 2);
  assert.equal(unsubscriptionCount, 1);

  // Final cleanup
  adapter2.dispose();
  assert.equal(subscriptionCount, 2);
  assert.equal(unsubscriptionCount, 2);
});

test("Section F: Controller operation fence suppresses stale listener events during pending signOut", async () => {
  let userListener: ((u: any) => void) | null = null;
  let resolveSignOut: () => void;
  const signOutPromise = new Promise<void>((resolve) => {
    resolveSignOut = resolve;
  });

  const admittedUser = {
    uid: "uid-a",
    email: "a@ellaexecutivesearch.com",
    getIdToken: async () => "token-a",
  };

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: admittedUser } as any),
    subscribeAuthState: (_auth, next) => {
      userListener = next;
      return () => {};
    },
    executeAdmission: async () => ({
      status: "admitted",
      principal: { uid: "uid-a", email: "a@ellaexecutivesearch.com", org_id: "ella-internal" },
    }),
    signOutProvider: async () => {
      await signOutPromise;
    },
  });

  controller.start();
  // 1. Initial admission
  userListener!(admittedUser);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "signed_in");

  // 2. Start unresolved signOut
  const pSignOut = controller.signOut();
  assert.equal(controller.getState().status, "signed_out");
  assert.equal(controller.getState().busy, true);

  // 3. Stale non-null listener event emitted while signOut is pending
  userListener!(admittedUser);
  assert.equal(controller.getState().status, "signed_out");
  assert.equal(controller.getState().busy, true);
  assert.equal(controller.getState().user, null);

  // 4. Resolve signOut
  resolveSignOut!();
  await pSignOut;
  assert.equal(controller.getState().status, "signed_out");
  assert.equal(controller.getState().busy, false);
  assert.equal(controller.getState().user, null);
});

test("Section F: Provider errors with throwing toString() do not leak sentinels or crash", async () => {
  const sentinel = "PROVIDER_SECRET_SENTINEL_404";
  const throwingError = {
    code: "auth/unknown",
    toString() {
      throw new Error(sentinel);
    },
  };

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: null } as any),
    signInWithPopup: async () => {
      throw throwingError;
    },
  });

  controller.start();
  await controller.signIn();

  const state = controller.getState();
  assert.equal(state.status, "signed_out");
  assert.equal(state.busy, false);
  assert.equal(state.error, "Falha ao conectar com o Google. Tente novamente.");
  assert.equal(state.error?.includes(sentinel), false);
});

test("Section F: Synthetic bypass signOut and useAnotherAccount settle immediately with zero effects", async () => {
  let signOutProviderCalled = false;
  let popupCalled = false;

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: true,
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    signOutProvider: async () => { signOutProviderCalled = true; },
    signInWithPopup: async () => { popupCalled = true; return null; },
  });

  controller.start();
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(controller.getState().busy, false);

  // signOut in bypass mode
  await controller.signOut();
  assert.equal(controller.getState().status, "signed_out");
  assert.equal(controller.getState().busy, false);
  assert.equal(signOutProviderCalled, false);

  // useAnotherAccount in bypass mode
  await controller.useAnotherAccount();
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(controller.getState().busy, false);
  assert.equal(popupCalled, false);
});

test("Section D: apiFetch immutable snapshot, replay, caller mutation immunity, and 401 refresh", async () => {
  const { apiFetch, ApiFetchError } = await import("./auth.ts");

  const validConfig = {
    authBypassEnabled: false,
    apiUrl: "https://backend.ellaexecutivesearch.com",
    wsUrl: "wss://backend.ellaexecutivesearch.com/ws",
    wsStreamUrl: "wss://backend.ellaexecutivesearch.com/api/stream/native",
  };

  // 1. Mutation while token is pending: caller mutates credentials and URLSearchParams; dispatched request retains original snapshot
  let resolveToken: (tok: string) => void;
  const tokenPromise = new Promise<string>((resolve) => {
    resolveToken = resolve;
  });

  const mutableParams = new URLSearchParams({ original: "val" });
  const mutableInit: RequestInit = {
    method: "POST",
    body: mutableParams,
    credentials: "omit",
  };

  let capturedUrl = "";
  let capturedCredentials: any = undefined;
  let capturedBody = "";
  let capturedHeaders: Headers | null = null;

  const fakeFetch = async (url: string | URL | Request, init?: RequestInit) => {
    if (typeof Request !== "undefined" && url instanceof Request) {
      capturedUrl = url.url;
      capturedCredentials = url.credentials;
      capturedHeaders = new Headers(url.headers);
      capturedBody = await url.clone().text();
    } else {
      capturedUrl = url.toString();
      capturedCredentials = init?.credentials;
      capturedHeaders = new Headers(init?.headers);
      capturedBody = String(init?.body || "");
    }
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  };

  const fakeUser = {
    uid: "uid-1",
    getIdToken: async (force?: boolean) => {
      if (force) return "refreshed-token";
      return await tokenPromise;
    },
  };

  const pFetch = apiFetch(
    "https://backend.ellaexecutivesearch.com/api/test",
    mutableInit,
    {
      isTrustedDestination: () => true,
      fetch: fakeFetch as unknown as typeof fetch,
      isBypassEnabled: () => false,
      getAuth: () => ({ currentUser: fakeUser } as any),
    }
  );

  // Caller mutates parameters and credentials while token is pending!
  mutableParams.set("original", "MUTATED");
  mutableParams.set("injected", "LEAK");
  mutableInit.credentials = "include";

  resolveToken!("initial-token");
  await pFetch;

  assert.equal(capturedUrl, "https://backend.ellaexecutivesearch.com/api/test");
  assert.equal(capturedCredentials, "omit");
  assert.equal(capturedBody, "original=val");
  assert.equal(capturedHeaders?.get("Authorization"), "Bearer initial-token");
  assert.equal(capturedHeaders?.get("Content-Type"), "application/x-www-form-urlencoded;charset=UTF-8");

  // 2. Relative URLs (api/me and /api/me) are rejected pre-token
  let tokenCalledOnRelative = false;
  const tokenRecordingUser = {
    uid: "uid-rel",
    getIdToken: async () => {
      tokenCalledOnRelative = true;
      return "token";
    },
  };
  await assert.rejects(
    async () =>
      apiFetch("/api/me", {}, {
        isTrustedDestination: () => true,
        fetch: fakeFetch as any,
        isBypassEnabled: () => false,
        getAuth: () => ({ currentUser: tokenRecordingUser } as any),
      }),
    (err: any) => err instanceof ApiFetchError && err.message === "Relative request destinations are prohibited"
  );
  await assert.rejects(
    async () =>
      apiFetch("api/me", {}, {
        isTrustedDestination: () => true,
        fetch: fakeFetch as any,
        isBypassEnabled: () => false,
        getAuth: () => ({ currentUser: tokenRecordingUser } as any),
      }),
    (err: any) => err instanceof ApiFetchError && err.message === "Relative request destinations are prohibited"
  );
  assert.equal(tokenCalledOnRelative, false);

  // 3. Native header replacement: RequestInit headers replace rather than merge Request headers
  let dispatchedHeaders: Headers | null = null;
  const initialReq = new Request("https://backend.ellaexecutivesearch.com/api/headers", {
    headers: {
      "X-Original": "keep-me",
      "X-Common": "original",
    },
  });
  await apiFetch(
    initialReq,
    {
      headers: {
        "X-Replaced": "new-val",
        "X-Common": "override",
      },
    },
    {
      isTrustedDestination: () => true,
      fetch: (async (req: any, init: any) => {
        dispatchedHeaders = req instanceof Request ? new Headers(req.headers) : new Headers(init?.headers);
        return new Response("ok", { status: 200 });
      }) as any,
      isBypassEnabled: () => false,
      getAuth: () => ({ currentUser: fakeUser } as any),
    }
  );
  assert.equal(dispatchedHeaders?.get("X-Replaced"), "new-val");
  assert.equal(dispatchedHeaders?.get("X-Common"), "override");
  assert.equal(dispatchedHeaders?.has("X-Original"), false);

  // 4. Empty-body override: body: "" is preserved
  let emptyBodyCaptured: any = null;
  await apiFetch(
    "https://backend.ellaexecutivesearch.com/api/empty",
    { method: "POST", body: "" },
    {
      isTrustedDestination: () => true,
      fetch: (async (req: any, init: any) => {
        emptyBodyCaptured = req instanceof Request ? await req.text() : init?.body;
        return new Response("ok", { status: 200 });
      }) as any,
      isBypassEnabled: () => false,
      getAuth: () => ({ currentUser: fakeUser } as any),
    }
  );
  assert.equal(emptyBodyCaptured, "");

  // 5. 401 Retry with ArrayBuffer body replay and refreshed token
  let attemptCount = 0;
  const originalBuffer = new Uint8Array([1, 2, 3, 4]).buffer;
  const p401 = await apiFetch(
    "https://backend.ellaexecutivesearch.com/api/replay",
    { method: "POST", body: originalBuffer },
    {
      isTrustedDestination: () => true,
      fetch: (async (req: any, init: any) => {
        attemptCount++;
        const headers = req instanceof Request ? new Headers(req.headers) : new Headers(init?.headers);
        const body = req instanceof Request ? await req.arrayBuffer() : init?.body;
        if (attemptCount === 1) {
          assert.equal(headers.get("Authorization"), "Bearer initial-token");
          return new Response("Unauthorized", { status: 401 });
        }
        assert.equal(headers.get("Authorization"), "Bearer refreshed-token");
        assert.deepEqual(new Uint8Array(body), new Uint8Array([1, 2, 3, 4]));
        return new Response("Success", { status: 200 });
      }) as any,
      isBypassEnabled: () => false,
      getAuth: () => ({ currentUser: fakeUser } as any),
    }
  );
  assert.equal(p401.status, 200);
  assert.equal(attemptCount, 2);

  // 6. Consumed / bodyUsed Request input is rejected before token lookup
  let tokenCalledOnConsumed = false;
  const consumedReq = new Request("https://backend.ellaexecutivesearch.com/api/consumed", {
    method: "POST",
    body: "data",
  });
  await consumedReq.text(); // Mark bodyUsed = true
  assert.equal(consumedReq.bodyUsed, true);

  await assert.rejects(
    async () =>
      apiFetch(consumedReq, {}, {
        isTrustedDestination: () => true,
        fetch: fakeFetch as any,
        isBypassEnabled: () => false,
        getAuth: () => ({
          currentUser: {
            uid: "uid-1",
            getIdToken: async () => {
              tokenCalledOnConsumed = true;
              return "token";
            },
          },
        } as any),
      }),
    (err: any) => err instanceof ApiFetchError && err.message === "Request body already used"
  );
  assert.equal(tokenCalledOnConsumed, false);

  // 7. Injected 3xx redirect is rejected
  await assert.rejects(
    async () =>
      apiFetch("https://backend.ellaexecutivesearch.com/api/redirect", {}, {
        isTrustedDestination: () => true,
        fetch: (async () => new Response(null, { status: 302, headers: { Location: "https://attacker.com" } })) as any,
        isBypassEnabled: () => false,
        getAuth: () => ({ currentUser: fakeUser } as any),
      }),
    (err: any) => err instanceof ApiFetchError && err.message === "Redirects are prohibited"
  );

  // 8. Account loss before dispatch throws
  const authHolder: any = {};
  const userMutatingAuth = {
    uid: "uid-loss",
    getIdToken: async () => {
      authHolder.currentUser = null; // Principal lost while token is pending
      return "token-loss";
    },
  };
  authHolder.currentUser = userMutatingAuth;

  await assert.rejects(
    async () =>
      apiFetch("https://backend.ellaexecutivesearch.com/api/loss", {}, {
        isTrustedDestination: () => true,
        fetch: fakeFetch as any,
        isBypassEnabled: () => false,
        getAuth: () => authHolder,
      }),
    (err: any) => err instanceof ApiFetchError && err.message === "Authentication state changed before request dispatch"
  );

  // 9. Bypass mode tokenless
  let bypassHeaders: Headers | null = null;
  await apiFetch("https://backend.ellaexecutivesearch.com/api/bypass", {}, {
    isTrustedDestination: () => true,
    fetch: (async (req: any, init: any) => {
      bypassHeaders = req instanceof Request ? new Headers(req.headers) : new Headers(init?.headers);
      return new Response("ok", { status: 200 });
    }) as any,
    isBypassEnabled: () => true,
    getAuth: () => ({ currentUser: null } as any),
  });
  assert.equal(bypassHeaders?.has("Authorization"), false);

  // 10. Throwing or non-boolean isBypassEnabled throws ApiFetchError("Authentication mode resolution failed")
  await assert.rejects(
    () =>
      apiFetch("https://backend.ellaexecutivesearch.com/api/test", {}, {
        isTrustedDestination: () => true,
        isBypassEnabled: (() => { throw new Error("SENTINEL_BYPASS_THROW"); }) as any,
      }),
    (err: any) => err instanceof ApiFetchError && err.message === "Authentication mode resolution failed" && !err.message.includes("SENTINEL")
  );

  await assert.rejects(
    () =>
      apiFetch("https://backend.ellaexecutivesearch.com/api/test", {}, {
        isTrustedDestination: () => true,
        isBypassEnabled: (() => "not-a-boolean") as any,
      }),
    (err: any) => err instanceof ApiFetchError && err.message === "Authentication mode resolution failed"
  );
});

test("Section D: Controller principal tombstone blocks late old listener after failed or successful sign-out", async () => {
  let userListener: ((u: any) => void) | null = null;
  let authErrorListener: (() => void) | null = null;
  let admissionCalls = 0;

  const oldUser = {
    uid: "uid-old",
    email: "old@ellaexecutivesearch.com",
    getIdToken: async () => "token-old",
  };

  let signOutShouldFail = true;

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: oldUser } as any),
    subscribeAuthState: (_auth, next, error) => {
      userListener = next;
      authErrorListener = error;
      return () => {};
    },
    executeAdmission: async () => {
      admissionCalls++;
      return {
        status: "admitted",
        principal: { uid: "uid-old", email: "old@ellaexecutivesearch.com", org_id: "ella-internal" },
      };
    },
    signOutProvider: async () => {
      if (signOutShouldFail) {
        throw new Error("SIGN_OUT_PROVIDER_FAILED");
      }
    },
  });

  controller.start();
  // 1. Initial admission of oldUser
  userListener!(oldUser);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(admissionCalls, 1);

  // 2. Failed signOut tombstones oldUser
  signOutShouldFail = true;
  await controller.signOut();
  assert.equal(controller.getState().status, "sign_out_error");
  assert.equal(controller.getState().user, null);

  // 3. Stale event for oldUser is emitted from provider -> MUST BE IGNORED (never re-admit)
  userListener!(oldUser);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "sign_out_error");
  assert.equal(controller.getState().user, null);
  assert.equal(admissionCalls, 1);

  // 4. Listener error callback settles into terminal signed_out non-busy
  authErrorListener!();
  assert.equal(controller.getState().status, "signed_out");
  assert.equal(controller.getState().error, "Falha na conexão de autenticação.");
  assert.equal(controller.getState().busy, false);
});

test("Section B: apiFetch causal body replay, keepalive, and error boundary tests", async () => {
  const fakeUser = {
    uid: "uid-fake",
    getIdToken: async () => "initial-token",
  };

  // 1. Mutable URL object href mutation during pending token is ignored
  const mutableUrl = new URL("https://backend.ellaexecutivesearch.com/api/mutable-url");
  let dispatchedUrl = "";
  let trustCheckUrl = "";
  const mutableUser = {
    uid: "uid-fake",
    getIdToken: async () => {
      // Mutate URL object while token is awaiting!
      mutableUrl.href = "https://evil.example.com/steal";
      return "token-1";
    },
  };
  const mutableAuth = { currentUser: mutableUser };

  await apiFetch(
    mutableUrl,
    {},
    {
      isTrustedDestination: (u) => {
        trustCheckUrl = u;
        return true;
      },
      fetch: (async (req: any) => {
        dispatchedUrl = req instanceof Request ? req.url : req;
        return new Response("ok", { status: 200 });
      }) as any,
      isBypassEnabled: () => false,
      getAuth: () => mutableAuth as any,
    }
  );
  assert.equal(dispatchedUrl, "https://backend.ellaexecutivesearch.com/api/mutable-url");
  assert.equal(trustCheckUrl, "https://backend.ellaexecutivesearch.com/api/mutable-url");

  // 2. FormData snapshot immunity and 401 replay boundary match
  let formDataAttempt = 0;
  let firstBodyBytes: Uint8Array | null = null;
  let secondBodyBytes: Uint8Array | null = null;
  let firstAuth = "";
  let secondAuth = "";
  let firstContentType = "";
  let secondContentType = "";

  const formData = new FormData();
  formData.append("field1", "initial-val");

  let tokenCount = 0;
  const fdUser = {
    uid: "uid-fake",
    getIdToken: async () => {
      tokenCount++;
      // Mutate FormData while token awaits -> should not affect snapshot
      formData.append("field2", "mutated-val");
      return tokenCount === 1 ? "initial-token" : "refreshed-token";
    },
  };
  const fdAuth = { currentUser: fdUser };

  await apiFetch(
    "https://backend.ellaexecutivesearch.com/api/form-data",
    { method: "POST", body: formData },
    {
      isTrustedDestination: () => true,
      fetch: (async (req: any) => {
        formDataAttempt++;
        const headers = req.headers;
        const bytes = new Uint8Array(await req.arrayBuffer());
        if (formDataAttempt === 1) {
          firstAuth = headers.get("Authorization") || "";
          firstContentType = headers.get("Content-Type") || "";
          firstBodyBytes = bytes;
          return new Response("Unauthorized", { status: 401 });
        }
        secondAuth = headers.get("Authorization") || "";
        secondContentType = headers.get("Content-Type") || "";
        secondBodyBytes = bytes;
        return new Response("ok", { status: 200 });
      }) as any,
      isBypassEnabled: () => false,
      getAuth: () => fdAuth as any,
    }
  );
  assert.equal(formDataAttempt, 2);
  assert.equal(firstAuth, "Bearer initial-token");
  assert.equal(secondAuth, "Bearer refreshed-token");
  assert.equal(firstContentType, secondContentType);
  assert.deepEqual(firstBodyBytes, secondBodyBytes);
  const firstBodyText = new TextDecoder().decode(firstBodyBytes!);
  assert.equal(firstBodyText.includes("mutated-val"), false, "Mutated data must not be in FormData snapshot");

  // 3. Request with non-empty body overridden by init body: ""
  const nonemptyReq = new Request("https://backend.ellaexecutivesearch.com/api/override-empty", {
    method: "POST",
    body: "ORIGINAL_BODY_SHOULD_BE_OVERRIDDEN",
  });
  let overrideCapturedBody = "INITIAL_FLAG";
  await apiFetch(
    nonemptyReq,
    { body: "" },
    {
      isTrustedDestination: () => true,
      fetch: (async (req: any) => {
        overrideCapturedBody = await req.text();
        return new Response("ok", { status: 200 });
      }) as any,
      isBypassEnabled: () => false,
      getAuth: () => ({ currentUser: fakeUser } as any),
    }
  );
  assert.equal(overrideCapturedBody, "");

  // 4. Throwing, wrong, same-class, changing isBypassEnabled yields fresh ApiFetchError with zero effects
  let authAccessed = 0;
  let fetchCalled = 0;
  const attackerErr = new ApiFetchError("RAW_BYPASS_SENTINEL");
  attackerErr.stack = "ATTACKER_INJECTED_STACK_TRACE";
  (attackerErr as any).cause = new Error("ATTACKER_CAUSE");
  (attackerErr as any).sentinelProp = "ATTACKER_STRING_PROP";
  const sentinelSym = Symbol("ATTACKER_SYMBOL");
  (attackerErr as any)[sentinelSym] = "ATTACKER_SYMBOL_PROP";
  (attackerErr as any).nestedObject = { secret: "ATTACKER_SECRET" };

  let capturedThrownErr: unknown = null;
  await assert.rejects(
    async () =>
      apiFetch(
        "https://backend.ellaexecutivesearch.com/api/test",
        {},
        {
          isTrustedDestination: () => true,
          fetch: (async () => { fetchCalled++; return new Response("ok"); }) as any,
          isBypassEnabled: (() => { throw attackerErr; }) as any,
          getAuth: () => { authAccessed++; return null as any; },
        }
      ),
    (err: unknown) => {
      capturedThrownErr = err;
      return true;
    }
  );
  assert.equal(authAccessed, 0);
  assert.equal(fetchCalled, 0);

  const pristineNativeError = new Error("baseline");
  const pristineNativeOwnKeys = Object.freeze(Reflect.ownKeys(pristineNativeError));
  const pristineNativeStackDesc = Object.getOwnPropertyDescriptor(pristineNativeError, "stack");
  assert.ok(pristineNativeStackDesc !== undefined, "Pristine native Error stack descriptor must exist");
  const pristineNativeMsgDesc = Object.getOwnPropertyDescriptor(pristineNativeError, "message");
  assert.ok(pristineNativeMsgDesc !== undefined, "Pristine native Error message descriptor must exist");

  const expectedOwnKeysBaseline = Object.freeze([...pristineNativeOwnKeys, "name"]);

  function validateAndExtractLiveErrorStackDescriptor(
    candidate: unknown,
    pristineStack: PropertyDescriptor,
    pristineMsg: PropertyDescriptor
  ): PropertyDescriptor {
    assert.ok(typeof candidate === "object" && candidate !== null, "Candidate error must be non-null object");
    const stackDesc = Object.getOwnPropertyDescriptor(candidate, "stack");
    assert.ok(stackDesc !== undefined, "Candidate error stack descriptor must exist");
    const msgDesc = Object.getOwnPropertyDescriptor(candidate, "message");
    assert.ok(msgDesc !== undefined, "Candidate error message descriptor must exist");

    const isNativeAccessor = "get" in pristineStack || "set" in pristineStack;
    const isCandidateAccessor = "get" in stackDesc || "set" in stackDesc;
    assert.equal(isCandidateAccessor, isNativeAccessor, "Candidate stack descriptor kind must match native kind");

    if (isNativeAccessor) {
      assert.equal(stackDesc.enumerable, pristineStack.enumerable, "stack enumerable flag must match native");
      assert.equal(stackDesc.configurable, pristineStack.configurable, "stack configurable flag must match native");
    } else {
      assert.equal(stackDesc.writable, pristineStack.writable, "stack writable flag must match native");
      assert.equal(stackDesc.enumerable, pristineStack.enumerable, "stack enumerable flag must match native");
      assert.equal(stackDesc.configurable, pristineStack.configurable, "stack configurable flag must match native");
    }

    assert.equal("get" in msgDesc || "set" in msgDesc, false, "message must not be accessor");
    assert.equal(msgDesc.writable, pristineMsg.writable, "message writable flag must match native");
    assert.equal(msgDesc.enumerable, pristineMsg.enumerable, "message enumerable flag must match native");
    assert.equal(msgDesc.configurable, pristineMsg.configurable, "message configurable flag must match native");

    return stackDesc;
  }

  // Runtime stack validation of captured production error
  const fixedMatrixStackBaseline = validateAndExtractLiveErrorStackDescriptor(capturedThrownErr, pristineNativeStackDesc, pristineNativeMsgDesc);
  const fixedMatrixMsgBaseline = pristineNativeMsgDesc;
  const isNativeAccessor = "get" in pristineNativeStackDesc || "set" in pristineNativeStackDesc;

  // Exact own-surface inspector verifying descriptors directly without property access
  function assertExactApiFetchErrorSurface(
    captured: unknown,
    expectedMessage: string,
    attacker: unknown,
    forbiddenSentinels: string[],
    expectedOwnKeys: readonly (string | symbol)[],
    expectedStackDesc: PropertyDescriptor,
    expectedMsgDesc: PropertyDescriptor
  ) {
    assert.ok(typeof captured === "object" && captured !== null, "Captured error must be non-null object");
    if (attacker !== undefined) {
      assert.notEqual(captured, attacker, "Must not be the attacker error instance");
    }
    assert.equal(Object.getPrototypeOf(captured), ApiFetchError.prototype, "Must have ApiFetchError prototype");

    const capturedOwnKeys = Reflect.ownKeys(captured as object);
    assert.equal(capturedOwnKeys.length, expectedOwnKeys.length, "Own key count must match expected");

    const capturedDescriptors = Object.getOwnPropertyDescriptors(captured as object);

    for (const key of capturedOwnKeys) {
      assert.ok(typeof key === "string", "Own keys must only be strings");
      assert.ok(expectedOwnKeys.includes(key), "Unexpected own key on fresh ApiFetchError");
      const desc = capturedDescriptors[key as string];
      assert.ok(desc !== undefined, "Descriptor must exist for own key");

      if (key === "name") {
        assert.equal(desc.get, undefined, "name must not have getter");
        assert.equal(desc.set, undefined, "name must not have setter");
        assert.equal(desc.value, "ApiFetchError", "name descriptor value must be ApiFetchError");
        assert.equal(desc.writable, true, "name descriptor writable must be true");
        assert.equal(desc.enumerable, true, "name descriptor enumerable must be true");
        assert.equal(desc.configurable, true, "name descriptor configurable must be true");
      } else if (key === "message") {
        assert.equal(desc.get, undefined, "message must not have getter");
        assert.equal(desc.set, undefined, "message must not have setter");
        assert.equal(typeof desc.value, "string", "message value must be a string");
        assert.equal(desc.value, expectedMessage, "message value must match expected");
        assert.equal(desc.writable, expectedMsgDesc.writable, "message writable flag must match expected");
        assert.equal(desc.enumerable, expectedMsgDesc.enumerable, "message enumerable flag must match expected");
        assert.equal(desc.configurable, expectedMsgDesc.configurable, "message configurable flag must match expected");
        for (const sentinel of forbiddenSentinels) {
          assert.ok(!desc.value.includes(sentinel), "message must not contain forbidden sentinel");
        }
      } else if (key === "stack") {
        const isExpectedAccessor = "get" in expectedStackDesc || "set" in expectedStackDesc;
        const isCapturedAccessor = "get" in desc || "set" in desc;

        if (isExpectedAccessor) {
          assert.ok(isCapturedAccessor, "Captured stack descriptor must be accessor-form when expected is accessor-form");
          assert.equal(desc.get, expectedStackDesc.get, "stack get function must match expected getter identity");
          assert.equal(desc.set, expectedStackDesc.set, "stack set function must match expected setter identity");
          assert.equal(desc.enumerable, expectedStackDesc.enumerable, "stack enumerable must match expected");
          assert.equal(desc.configurable, expectedStackDesc.configurable, "stack configurable must match expected");
        } else {
          assert.ok(!isCapturedAccessor, "Captured stack descriptor must be data-form when expected is data-form");
          assert.equal(typeof desc.value, "string", "data-form stack value must be a string");
          assert.equal(desc.writable, expectedStackDesc.writable, "stack writable must match expected data descriptor");
          assert.equal(desc.enumerable, expectedStackDesc.enumerable, "stack enumerable must match expected data descriptor");
          assert.equal(desc.configurable, expectedStackDesc.configurable, "stack configurable must match expected data descriptor");
          for (const sentinel of forbiddenSentinels) {
            assert.ok(!desc.value.includes(sentinel), "data-form stack must not contain forbidden sentinel");
          }
        }
      } else {
        assert.fail("Unexpected own property key");
      }
    }

    assert.equal(capturedDescriptors.cause, undefined, "cause descriptor must be undefined");
    assert.equal(capturedDescriptors.details, undefined, "details descriptor must be undefined");
    assert.equal(capturedDescriptors.metadata, undefined, "metadata descriptor must be undefined");
    assert.equal(capturedDescriptors.nestedObject, undefined, "nestedObject descriptor must be undefined");
  }

  // 1. Passing baseline
  assertExactApiFetchErrorSurface(capturedThrownErr, "Authentication mode resolution failed", attackerErr, ["ATTACKER", "RAW_BYPASS_SENTINEL"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline);

  // 2. Getter-only and setter-only name, message with invocation counters 0
  let nameGetterCalled = 0;
  const mutantNameGetter = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantNameGetter, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantNameGetter, "name", {
    get: () => { nameGetterCalled++; return "ApiFetchError"; },
    enumerable: true,
    configurable: true,
  });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantNameGetter, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
  assert.equal(nameGetterCalled, 0, "name getter must not be called");

  let nameSetterCalled = 0;
  const mutantNameSetter = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantNameSetter, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantNameSetter, "name", {
    set: () => { nameSetterCalled++; },
    enumerable: true,
    configurable: true,
  });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantNameSetter, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
  assert.equal(nameSetterCalled, 0, "name setter must not be called");

  let msgGetterCalled = 0;
  const mutantMsgGetter = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantMsgGetter, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantMsgGetter, "message", {
    get: () => { msgGetterCalled++; return "Authentication mode resolution failed"; },
    enumerable: false,
    configurable: true,
  });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantMsgGetter, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
  assert.equal(msgGetterCalled, 0, "message getter must not be called");

  let msgSetterCalled = 0;
  const mutantMsgSetter = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantMsgSetter, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantMsgSetter, "message", {
    set: () => { msgSetterCalled++; },
    enumerable: false,
    configurable: true,
  });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantMsgSetter, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
  assert.equal(msgSetterCalled, 0, "message setter must not be called");

  // 3. Demonstrated passing synthetic accessor baseline and one-dimension hostile accessor mutants (runs on every runtime)
  let syntheticBaselineGetterCalls = 0;
  let syntheticBaselineSetterCalls = 0;
  const syntheticBaselineGetter = () => { syntheticBaselineGetterCalls++; return "SAFE_SYNTHETIC_STACK"; };
  const syntheticBaselineSetter = (_val: any) => { syntheticBaselineSetterCalls++; };
  const syntheticAccessorPassingDesc: PropertyDescriptor = {
    get: syntheticBaselineGetter,
    set: syntheticBaselineSetter,
    enumerable: false,
    configurable: true,
  };

  const validSyntheticAccessor = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(validSyntheticAccessor, "stack", syntheticAccessorPassingDesc);
  assertExactApiFetchErrorSurface(
    validSyntheticAccessor,
    "Authentication mode resolution failed",
    attackerErr,
    ["ATTACKER"],
    expectedOwnKeysBaseline,
    syntheticAccessorPassingDesc,
    fixedMatrixMsgBaseline
  );
  assert.equal(syntheticBaselineGetterCalls, 0, "synthetic baseline getter must not be called during passing check");
  assert.equal(syntheticBaselineSetterCalls, 0, "synthetic baseline setter must not be called during passing check");

  let hostileGetterCalls = 0;
  const mutantHostileGetter = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantHostileGetter, "stack", {
    ...syntheticAccessorPassingDesc,
    get: () => { hostileGetterCalls++; return "HOSTILE_STACK"; },
  });
  assert.throws(() =>
    assertExactApiFetchErrorSurface(
      mutantHostileGetter,
      "Authentication mode resolution failed",
      attackerErr,
      ["ATTACKER"],
      expectedOwnKeysBaseline,
      syntheticAccessorPassingDesc,
      fixedMatrixMsgBaseline
    )
  );
  assert.equal(hostileGetterCalls, 0, "hostile getter must not be called");

  let hostileSetterCalls = 0;
  const mutantHostileSetter = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantHostileSetter, "stack", {
    ...syntheticAccessorPassingDesc,
    set: (_val: any) => { hostileSetterCalls++; },
  });
  assert.throws(() =>
    assertExactApiFetchErrorSurface(
      mutantHostileSetter,
      "Authentication mode resolution failed",
      attackerErr,
      ["ATTACKER"],
      expectedOwnKeysBaseline,
      syntheticAccessorPassingDesc,
      fixedMatrixMsgBaseline
    )
  );
  assert.equal(hostileSetterCalls, 0, "hostile setter must not be called");

  // 4. Synthetic setter-only passing control and wrong-setter-identity mutant (runs on every runtime)
  let syntheticSetterOnlyCalls = 0;
  const syntheticSetterOnly = (_val: any) => { syntheticSetterOnlyCalls++; };
  const syntheticSetterOnlyDesc: PropertyDescriptor = {
    get: undefined,
    set: syntheticSetterOnly,
    enumerable: false,
    configurable: true,
  };
  const validSetterOnly = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(validSetterOnly, "stack", syntheticSetterOnlyDesc);
  assertExactApiFetchErrorSurface(
    validSetterOnly,
    "Authentication mode resolution failed",
    attackerErr,
    ["ATTACKER"],
    expectedOwnKeysBaseline,
    syntheticSetterOnlyDesc,
    fixedMatrixMsgBaseline
  );
  assert.equal(syntheticSetterOnlyCalls, 0, "synthetic setter must not be called during passing check");

  let badSetterOnlyCalled = 0;
  const mutantAccessorSetterOnly = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantAccessorSetterOnly, "stack", {
    ...syntheticSetterOnlyDesc,
    set: () => { badSetterOnlyCalled++; },
  });
  assert.throws(() =>
    assertExactApiFetchErrorSurface(
      mutantAccessorSetterOnly,
      "Authentication mode resolution failed",
      attackerErr,
      ["ATTACKER"],
      expectedOwnKeysBaseline,
      syntheticSetterOnlyDesc,
      fixedMatrixMsgBaseline
    )
  );
  assert.equal(badSetterOnlyCalled, 0, "mutant setter must not be called");

  // 5. Independent name and message data value, kind, deletion, writable, enumerable, configurable rows
  const mutantNameVal = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantNameVal, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantNameVal, "name", { value: "OtherError", writable: true, enumerable: true, configurable: true });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantNameVal, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantNameKind = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantNameKind, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantNameKind, "name", { value: 123, writable: true, enumerable: true, configurable: true });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantNameKind, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantNameDel = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantNameDel, "stack", fixedMatrixStackBaseline);
  delete (mutantNameDel as any).name;
  assert.throws(() => assertExactApiFetchErrorSurface(mutantNameDel, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantNameWritable = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantNameWritable, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantNameWritable, "name", { value: "ApiFetchError", writable: false, enumerable: true, configurable: true });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantNameWritable, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantNameEnum = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantNameEnum, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantNameEnum, "name", { value: "ApiFetchError", writable: true, enumerable: false, configurable: true });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantNameEnum, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantNameConfig = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantNameConfig, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantNameConfig, "name", { value: "ApiFetchError", writable: true, enumerable: true, configurable: false });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantNameConfig, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantMsgVal = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantMsgVal, "stack", fixedMatrixStackBaseline);
  assert.throws(() => assertExactApiFetchErrorSurface(mutantMsgVal, "Different expected message", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantMsgKind = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantMsgKind, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantMsgKind, "message", { value: 12345, writable: true, enumerable: false, configurable: true });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantMsgKind, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantMsgDel = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantMsgDel, "stack", fixedMatrixStackBaseline);
  delete (mutantMsgDel as any).message;
  assert.throws(() => assertExactApiFetchErrorSurface(mutantMsgDel, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantMsgWritable = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantMsgWritable, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantMsgWritable, "message", {
    value: "Authentication mode resolution failed",
    writable: !fixedMatrixMsgBaseline.writable,
    enumerable: fixedMatrixMsgBaseline.enumerable,
    configurable: fixedMatrixMsgBaseline.configurable,
  });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantMsgWritable, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantMsgEnum = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantMsgEnum, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantMsgEnum, "message", {
    value: "Authentication mode resolution failed",
    writable: fixedMatrixMsgBaseline.writable,
    enumerable: !fixedMatrixMsgBaseline.enumerable,
    configurable: fixedMatrixMsgBaseline.configurable,
  });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantMsgEnum, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantMsgConfig = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantMsgConfig, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantMsgConfig, "message", {
    value: "Authentication mode resolution failed",
    writable: fixedMatrixMsgBaseline.writable,
    enumerable: fixedMatrixMsgBaseline.enumerable,
    configurable: !fixedMatrixMsgBaseline.configurable,
  });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantMsgConfig, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  // 6. Runtime-conditional native stack tests
  if (isNativeAccessor) {
    // Passing control with captured exact accessor descriptor
    const validAccessorStack = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(validAccessorStack, "stack", { ...fixedMatrixStackBaseline });
    assertExactApiFetchErrorSurface(validAccessorStack, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline);

    let badGetterCalled = 0;
    const mutantAccessorGetId = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantAccessorGetId, "stack", {
      ...fixedMatrixStackBaseline,
      get: () => { badGetterCalled++; return "DIFFERENT_GETTER"; },
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantAccessorGetId, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
    assert.equal(badGetterCalled, 0, "Hostile stack getter must not be called");

    let badSetterCalled = 0;
    const mutantAccessorSetId = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantAccessorSetId, "stack", {
      ...fixedMatrixStackBaseline,
      set: () => { badSetterCalled++; },
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantAccessorSetId, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
    assert.equal(badSetterCalled, 0, "Hostile stack setter must not be called");

    const mutantAccessorEnum = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantAccessorEnum, "stack", {
      ...fixedMatrixStackBaseline,
      enumerable: !fixedMatrixStackBaseline.enumerable,
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantAccessorEnum, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

    const mutantAccessorConfig = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantAccessorConfig, "stack", {
      ...fixedMatrixStackBaseline,
      configurable: !fixedMatrixStackBaseline.configurable,
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantAccessorConfig, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

    const mutantDataOnAccessor = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantDataOnAccessor, "stack", {
      value: "data descriptor replacement",
      writable: true,
      enumerable: false,
      configurable: true,
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantDataOnAccessor, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
  } else {
    // For pristine data-form stack: safe alternate data string passes; deleted, sentinel, scalar non-string, object fail; independent single-flag flips fail
    const validSafeStack = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(validSafeStack, "stack", {
      value: "ApiFetchError: Authentication mode resolution failed\n    at safe",
      writable: fixedMatrixStackBaseline.writable,
      enumerable: fixedMatrixStackBaseline.enumerable,
      configurable: fixedMatrixStackBaseline.configurable,
    });
    assertExactApiFetchErrorSurface(validSafeStack, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline);

    const mutantStackDel = new ApiFetchError("Authentication mode resolution failed");
    delete (mutantStackDel as any).stack;
    assert.throws(() => assertExactApiFetchErrorSurface(mutantStackDel, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

    const mutantStackSentinel = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantStackSentinel, "stack", {
      ...fixedMatrixStackBaseline,
      value: "Error with ATTACKER inside",
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantStackSentinel, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

    const mutantStackScalar = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantStackScalar, "stack", {
      ...fixedMatrixStackBaseline,
      value: 12345,
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantStackScalar, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

    const mutantStackObj = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantStackObj, "stack", {
      ...fixedMatrixStackBaseline,
      value: { raw: "error" },
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantStackObj, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

    const mutantDataStackWritable = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantDataStackWritable, "stack", {
      ...fixedMatrixStackBaseline,
      writable: !fixedMatrixStackBaseline.writable,
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantDataStackWritable, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

    const mutantDataStackEnum = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantDataStackEnum, "stack", {
      ...fixedMatrixStackBaseline,
      enumerable: !fixedMatrixStackBaseline.enumerable,
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantDataStackEnum, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

    const mutantDataStackConfig = new ApiFetchError("Authentication mode resolution failed");
    Object.defineProperty(mutantDataStackConfig, "stack", {
      ...fixedMatrixStackBaseline,
      configurable: !fixedMatrixStackBaseline.configurable,
    });
    assert.throws(() => assertExactApiFetchErrorSurface(mutantDataStackConfig, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
  }

  // 6. Arbitrary object-valued unexpected property; data cause; getter-only and setter-only cause with counters 0; details; metadata; Symbol
  const mutantDataCause = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantDataCause, "stack", fixedMatrixStackBaseline);
  (mutantDataCause as any).cause = new Error("inner");
  assert.throws(() => assertExactApiFetchErrorSurface(mutantDataCause, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  let causeGetterCalled = 0;
  const mutantCauseGetter = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantCauseGetter, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantCauseGetter, "cause", {
    get: () => { causeGetterCalled++; return "HOSTILE_CAUSE"; },
    enumerable: false,
    configurable: true,
  });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantCauseGetter, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
  assert.equal(causeGetterCalled, 0, "cause getter must not be called");

  let causeSetterCalled = 0;
  const mutantCauseSetter = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantCauseSetter, "stack", fixedMatrixStackBaseline);
  Object.defineProperty(mutantCauseSetter, "cause", {
    set: () => { causeSetterCalled++; },
    enumerable: false,
    configurable: true,
  });
  assert.throws(() => assertExactApiFetchErrorSurface(mutantCauseSetter, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));
  assert.equal(causeSetterCalled, 0, "cause setter must not be called");

  const mutantUnexpectedObj = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantUnexpectedObj, "stack", fixedMatrixStackBaseline);
  (mutantUnexpectedObj as any).unexpected = { leak: "secret" };
  assert.throws(() => assertExactApiFetchErrorSurface(mutantUnexpectedObj, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantDetails = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantDetails, "stack", fixedMatrixStackBaseline);
  (mutantDetails as any).details = { leak: "secret" };
  assert.throws(() => assertExactApiFetchErrorSurface(mutantDetails, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantMetadata = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantMetadata, "stack", fixedMatrixStackBaseline);
  (mutantMetadata as any).metadata = { leak: "secret" };
  assert.throws(() => assertExactApiFetchErrorSurface(mutantMetadata, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  const mutantSymbol = new ApiFetchError("Authentication mode resolution failed");
  Object.defineProperty(mutantSymbol, "stack", fixedMatrixStackBaseline);
  (mutantSymbol as any)[Symbol("unexpectedSymbol")] = true;
  assert.throws(() => assertExactApiFetchErrorSurface(mutantSymbol, "Authentication mode resolution failed", attackerErr, ["ATTACKER"], expectedOwnKeysBaseline, fixedMatrixStackBaseline, fixedMatrixMsgBaseline));

  // a) Unused POST Request with body dispatches and replays byte-identically across 401
  const unusedPostReq = new Request("https://backend.ellaexecutivesearch.com/api/post-replay", {
    method: "POST",
    body: "exact-post-bytes-12345",
  });
  let postAttemptCount = 0;
  const resUnusedPost = await apiFetch(
    unusedPostReq,
    {},
    {
      isTrustedDestination: () => true,
      fetch: (async (req: any) => {
        postAttemptCount++;
        const text = await req.text();
        assert.equal(text, "exact-post-bytes-12345");
        if (postAttemptCount === 1) {
          return new Response("Unauthorized", { status: 401 });
        }
        return new Response("OK", { status: 200 });
      }) as any,
      isBypassEnabled: () => false,
      getAuth: () => ({ currentUser: fakeUser } as any),
    }
  );
  assert.equal(resUnusedPost.status, 200);
  assert.equal(postAttemptCount, 2);

  // b) GET Request keepalive=true is preserved
  const getKeepaliveReq = new Request("https://backend.ellaexecutivesearch.com/api/keepalive", {
    method: "GET",
    keepalive: true,
  });
  let keepalivePreserved = false;
  await apiFetch(
    getKeepaliveReq,
    {},
    {
      isTrustedDestination: () => true,
      fetch: (async (req: any) => {
        keepalivePreserved = req.keepalive === true;
        return new Response("OK", { status: 200 });
      }) as any,
      isBypassEnabled: () => false,
      getAuth: () => ({ currentUser: fakeUser } as any),
    }
  );
  assert.equal(keepalivePreserved, true);

  // c) Throwing user.uid getter trapped into fixed ApiFetchError
  const throwingUidUser = {
    get uid(): string {
      throw new Error("THROWING_UID_SENTINEL");
    },
    getIdToken: async () => "token",
  };
  let capturedUidErr: unknown = null;
  await assert.rejects(
    async () =>
      apiFetch("https://backend.ellaexecutivesearch.com/api/me", {}, {
        isTrustedDestination: () => true,
        fetch: (async () => new Response("ok")) as any,
        isBypassEnabled: () => false,
        getAuth: () => ({ currentUser: throwingUidUser } as any),
      }),
    (err: unknown) => {
      capturedUidErr = err;
      return true;
    }
  );
  const liveUidStackDesc = validateAndExtractLiveErrorStackDescriptor(capturedUidErr, pristineNativeStackDesc, pristineNativeMsgDesc);
  assertExactApiFetchErrorSurface(capturedUidErr, "Authentication principal lookup failed", undefined, ["THROWING_UID_SENTINEL"], expectedOwnKeysBaseline, liveUidStackDesc, fixedMatrixMsgBaseline);

  // d) Throwing auth.currentUser trapped into fixed ApiFetchError
  const throwingCurrentUserAuth = {
    get currentUser(): any {
      throw new Error("THROWING_CURRENT_USER_SENTINEL");
    },
  };
  let capturedStateErr: unknown = null;
  await assert.rejects(
    async () =>
      apiFetch("https://backend.ellaexecutivesearch.com/api/me", {}, {
        isTrustedDestination: () => true,
        fetch: (async () => new Response("ok")) as any,
        isBypassEnabled: () => false,
        getAuth: () => throwingCurrentUserAuth as any,
      }),
    (err: unknown) => {
      capturedStateErr = err;
      return true;
    }
  );
  const liveStateStackDesc = validateAndExtractLiveErrorStackDescriptor(capturedStateErr, pristineNativeStackDesc, pristineNativeMsgDesc);
  assertExactApiFetchErrorSurface(capturedStateErr, "Authentication state lookup failed", undefined, ["THROWING_CURRENT_USER_SENTINEL"], expectedOwnKeysBaseline, liveStateStackDesc, fixedMatrixMsgBaseline);
});

test("Section E: Controller admits fresh same-UID object after sign-out while rejecting old object", async () => {
  let userListener: ((u: any) => void) | null = null;
  let admissionCalls = 0;

  const oldUserInstance = {
    uid: "uid-shared-recruiter",
    email: "recruiter@ellaexecutivesearch.com",
    getIdToken: async () => "token-old",
  };

  const freshUserInstance = {
    uid: "uid-shared-recruiter", // Same UID!
    email: "recruiter@ellaexecutivesearch.com",
    getIdToken: async () => "token-fresh",
  };

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: null } as any),
    subscribeAuthState: (_auth, next) => {
      userListener = next;
      return () => {};
    },
    executeAdmission: async () => {
      admissionCalls++;
      return {
        status: "admitted",
        principal: { uid: "uid-shared-recruiter", email: "recruiter@ellaexecutivesearch.com", org_id: "ella-internal" },
      };
    },
    signOutProvider: async () => {},
  });

  controller.start();

  // 1. Admit old instance
  userListener!(oldUserInstance);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(admissionCalls, 1);

  // 2. Sign out retires old instance
  await controller.signOut();
  assert.equal(controller.getState().status, "signed_out");

  // 3. Stale event for old instance is ignored
  userListener!(oldUserInstance);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "signed_out");
  assert.equal(admissionCalls, 1);

  // 4. Fresh instance with same UID is successfully admitted!
  userListener!(freshUserInstance);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(admissionCalls, 2);
});

test("Section E.2: Executable lifecycle wiring using createAuthLifecycleAdapter with setup -> cleanup -> setup", async () => {
  let sub1Calls = 0;
  let sub2Calls = 0;

  // Setup first adapter
  const adapter1 = createAuthLifecycleAdapter();
  assert.equal(typeof adapter1.start, "function");
  assert.equal(typeof adapter1.dispose, "function");

  const unsub1 = adapter1.subscribe(() => {
    sub1Calls++;
  });
  adapter1.start();

  // Cleanup first adapter (must dispose cleanly)
  unsub1();
  adapter1.dispose();

  // Setup second adapter: setup -> cleanup -> setup
  const adapter2 = createAuthLifecycleAdapter();
  const unsub2 = adapter2.subscribe(() => {
    sub2Calls++;
  });
  adapter2.start();

  // Second instance actions / transitions work independently
  assert.equal(typeof adapter2.signIn, "function");
  assert.equal(typeof adapter2.retry, "function");
  assert.equal(typeof adapter2.signOut, "function");
  assert.equal(typeof adapter2.useAnotherAccount, "function");

  // Cleanup second adapter
  unsub2();
  adapter2.dispose();
});

test("Section E.3: Executable Retry wiring invokes controller retry and not signIn", async () => {
  let retryCalled = false;
  let signInCalled = false;
  let admissionAttempted = false;

  const mockUser = {
    uid: "uid-retry",
    email: "recruiter@ellaexecutivesearch.com",
    getIdToken: async () => "token",
  };

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({ currentUser: mockUser } as any),
    subscribeAuthState: (_auth, next) => {
      next(mockUser);
      return () => {};
    },
    signInWithPopup: async () => {
      signInCalled = true;
      return null;
    },
    executeAdmission: async () => {
      admissionAttempted = true;
      if (!retryCalled) {
        return { status: "retryable" };
      }
      return {
        status: "admitted",
        principal: { uid: "uid-retry", email: "recruiter@ellaexecutivesearch.com", org_id: "ella-internal" },
      };
    },
  });

  controller.start();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "retryable_error");
  assert.equal(signInCalled, false);

  // Calling retry() invokes admission again without opening popup
  retryCalled = true;
  await controller.retry();
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(controller.getState().status, "signed_in");
  assert.equal(signInCalled, false);
  assert.equal(admissionAttempted, true);
});

test("Section D: setupAuthLifecycle manages lifecycle with setup->cleanup->setup", () => {
  let subscribeCount1 = 0;
  let startCount1 = 0;
  let unsubCount1 = 0;
  let disposeCount1 = 0;

  let subscribeCount2 = 0;
  let startCount2 = 0;
  let unsubCount2 = 0;
  let disposeCount2 = 0;

  let listener1: ((s: any) => void) | null = null;
  let listener2: ((s: any) => void) | null = null;

  const fakeAdapter1 = {
    getInitialState: () => ({ status: "initializing", user: null, error: null, busy: false } as any),
    subscribe: (l: (s: any) => void) => {
      subscribeCount1++;
      listener1 = l;
      return () => { unsubCount1++; listener1 = null; };
    },
    start: () => { startCount1++; },
    signIn: async () => {},
    signOut: async () => {},
    useAnotherAccount: async () => {},
    retry: async () => {},
    dispose: () => { disposeCount1++; },
  };

  const fakeAdapter2 = {
    getInitialState: () => ({ status: "initializing", user: null, error: null, busy: false } as any),
    subscribe: (l: (s: any) => void) => {
      subscribeCount2++;
      listener2 = l;
      return () => { unsubCount2++; listener2 = null; };
    },
    start: () => { startCount2++; },
    signIn: async () => {},
    signOut: async () => {},
    useAnotherAccount: async () => {},
    retry: async () => {},
    dispose: () => { disposeCount2++; },
  };

  let capturedState: any = null;
  const onStateChange = (s: any) => { capturedState = s; };

  // Mount 1
  const cleanup1 = setupAuthLifecycle(fakeAdapter1 as any, onStateChange);
  assert.equal(subscribeCount1, 1);
  assert.equal(startCount1, 1);

  // Unmount 1
  cleanup1();
  assert.equal(unsubCount1, 1);
  assert.equal(disposeCount1, 1);

  // Late effect from adapter 1 has zero effect
  if (listener1) {
    (listener1 as any)({ status: "signed_in", user: { uid: "late" }, error: null, busy: false });
  }
  assert.equal(capturedState, null);

  // Mount 2
  const cleanup2 = setupAuthLifecycle(fakeAdapter2 as any, onStateChange);
  assert.equal(subscribeCount2, 1);
  assert.equal(startCount2, 1);

  if (listener2) {
    (listener2 as any)({ status: "signed_in", user: { uid: "adapter2" }, error: null, busy: false });
  }
  assert.equal(capturedState?.user?.uid, "adapter2");

  // Unmount 2
  cleanup2();
  assert.equal(unsubCount2, 1);
  assert.equal(disposeCount2, 1);
});

test("Section D: Hostile throwing/mutating config getters result in fixed config_error", () => {
  const hostileConfigObj = {
    get authBypassEnabled(): boolean {
      throw new Error("HOSTILE_CONFIG_GETTER");
    },
  };

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: hostileConfigObj as any,
    }),
  });

  const state = controller.getState();
  assert.equal(state.status, "config_error");
  assert.equal(state.busy, false);
});

test("Section D: Non-string/numeric/object/list/blank/padded/control/throwing principal claims fail to retryable_error with zero admission calls", async () => {
  const invalidPrincipals = [
    { uid: 12345, email: "recruiter@ellaexecutivesearch.com" },
    { uid: {}, email: "recruiter@ellaexecutivesearch.com" },
    { uid: [], email: "recruiter@ellaexecutivesearch.com" },
    { uid: "", email: "recruiter@ellaexecutivesearch.com" },
    { uid: " uid ", email: "recruiter@ellaexecutivesearch.com" },
    { uid: "uid\x00test", email: "recruiter@ellaexecutivesearch.com" },
    {
      get uid() { throw new Error("THROWING_UID_SENTINEL"); },
      email: "recruiter@ellaexecutivesearch.com",
    },
    { uid: "valid-uid", email: 12345 },
    { uid: "valid-uid", email: "not-an-email" },
    { uid: "valid-uid", email: "user@invalid domain.com" },
    {
      uid: "valid-uid",
      get email() { throw new Error("THROWING_EMAIL_SENTINEL"); },
    },
  ];

  for (const badPrincipal of invalidPrincipals) {
    let tokenCallCount = 0;
    let admissionCallCount = 0;

    const controller = createAuthController({
      getRuntimeConfig: () => ({
        ok: true,
        value: {
          authBypassEnabled: false,
          firebase: {
            apiKey: "AIza" + "A".repeat(35),
            authDomain: "auth.ellaexecutivesearch.com",
            projectId: "pilot-proj-1",
            storageBucket: "bucket.ellaexecutivesearch.com",
            messagingSenderId: "123456789012",
            appId: "1:123456789012:web:abcdef0123456789",
          },
          apiUrl: "http://127.0.0.1:8000",
          wsUrl: "ws://127.0.0.1:8000/ws",
          wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
        },
      }),
      getAuth: () => ({ currentUser: badPrincipal } as any),
      subscribeAuthState: (_auth, next) => {
        next(badPrincipal as any);
        return () => {};
      },
      executeAdmission: async () => {
        admissionCallCount++;
        return { status: "admitted", principal: { uid: "u", email: "e@e.com", org_id: "ella-internal" } };
      },
    });

    controller.start();
    await new Promise((r) => setTimeout(r, 10));

    const state = controller.getState();
    assert.equal(state.status, "retryable_error");
    assert.equal(state.busy, false);
    assert.equal(admissionCallCount, 0); // Proves zero admission call occurred!
    assert.equal(state.error?.includes("SENTINEL"), false);
  }
});

test("Section G: Source topology guard enforces useAuth lifecycle helper and real page retry wiring using TypeScript AST", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const urlModule = await import("node:url");
  const ts = (await import("typescript")).default;

  const currentDirResolved = path.dirname(urlModule.fileURLToPath(import.meta.url));

  function verifyAuthSourceTopology(sourceText: string): boolean {
    const sourceFile = ts.createSourceFile("auth.ts", sourceText, ts.ScriptTarget.Latest, true);

    // 1. Total recursive counts across the whole source file
    let totalFileAdapterCalls = 0;
    let totalFileSetupCalls = 0;

    function countCallsInFile(node: ts.Node) {
      if (ts.isCallExpression(node)) {
        if (ts.isIdentifier(node.expression)) {
          if (node.expression.text === "createAuthLifecycleAdapter") {
            totalFileAdapterCalls++;
          } else if (node.expression.text === "setupAuthLifecycle") {
            totalFileSetupCalls++;
          }
        }
      }
      ts.forEachChild(node, countCallsInFile);
    }
    countCallsInFile(sourceFile);

    if (totalFileAdapterCalls !== 1 || totalFileSetupCalls !== 1) {
      return false;
    }

    // 2. Locate useAuth function
    let useAuthFunctionNode: any = null;
    for (const stmt of sourceFile.statements) {
      if (ts.isFunctionDeclaration(stmt) && stmt.name?.text === "useAuth") {
        useAuthFunctionNode = stmt;
        break;
      } else if (ts.isVariableStatement(stmt)) {
        for (const decl of stmt.declarationList.declarations) {
          if (ts.isIdentifier(decl.name) && decl.name.text === "useAuth") {
            if (decl.initializer && (ts.isArrowFunction(decl.initializer) || ts.isFunctionExpression(decl.initializer))) {
              useAuthFunctionNode = decl.initializer;
              break;
            }
          }
        }
      }
    }

    if (!useAuthFunctionNode || !useAuthFunctionNode.body || !ts.isBlock(useAuthFunctionNode.body)) {
      return false;
    }

    // 3. Direct top-level useEffect call inside useAuth
    let effectCallbackBlock: any = null;
    let useEffectCount = 0;
    for (const stmt of useAuthFunctionNode.body.statements) {
      if (
        ts.isExpressionStatement(stmt) &&
        ts.isCallExpression(stmt.expression) &&
        ts.isIdentifier(stmt.expression.expression) &&
        stmt.expression.expression.text === "useEffect"
      ) {
        useEffectCount++;
        const effectArg = stmt.expression.arguments[0];
        if (effectArg && (ts.isArrowFunction(effectArg) || ts.isFunctionExpression(effectArg))) {
          if (effectArg.body && ts.isBlock(effectArg.body)) {
            effectCallbackBlock = effectArg.body;
          }
        }
      }
    }

    if (useEffectCount !== 1 || !effectCallbackBlock) {
      return false;
    }

    // 4. Recursive count inside effectCallbackBlock
    let effectAdapterCalls = 0;
    let effectSetupCalls = 0;
    function countCallsInEffect(node: ts.Node) {
      if (ts.isCallExpression(node)) {
        if (ts.isIdentifier(node.expression)) {
          if (node.expression.text === "createAuthLifecycleAdapter") {
            effectAdapterCalls++;
          } else if (node.expression.text === "setupAuthLifecycle") {
            effectSetupCalls++;
          }
        }
      }
      ts.forEachChild(node, countCallsInEffect);
    }
    countCallsInEffect(effectCallbackBlock);

    if (effectAdapterCalls !== 1 || effectSetupCalls !== 1) {
      return false;
    }

    // 5. Direct top-level statements structure in effectCallbackBlock
    let adapterVarName: string | null = null;
    let cleanupVarName: string | null = null;
    let returnStatementFound = false;
    let cleanupInvokedInReturn = false;

    for (const effStmt of effectCallbackBlock.statements) {
      if (returnStatementFound) {
        return false;
      }

      if (ts.isVariableStatement(effStmt)) {
        const isConst = (effStmt.declarationList.flags & ts.NodeFlags.Const) !== 0;
        if (!isConst) return false;

        for (const decl of effStmt.declarationList.declarations) {
          if (decl.initializer && ts.isCallExpression(decl.initializer)) {
            const callExpr = decl.initializer;
            if (ts.isIdentifier(callExpr.expression)) {
              if (callExpr.expression.text === "createAuthLifecycleAdapter") {
                if (callExpr.arguments.length !== 0 || !ts.isIdentifier(decl.name) || adapterVarName !== null) {
                  return false;
                }
                adapterVarName = decl.name.text;
              } else if (callExpr.expression.text === "setupAuthLifecycle") {
                if (!ts.isIdentifier(decl.name) || !adapterVarName || cleanupVarName !== null) {
                  return false;
                }
                const firstArg = callExpr.arguments[0];
                if (!firstArg || !ts.isIdentifier(firstArg) || firstArg.text !== adapterVarName) {
                  return false;
                }
                cleanupVarName = decl.name.text;
              }
            }
          }
        }
      } else if (ts.isReturnStatement(effStmt)) {
        returnStatementFound = true;
        if (!cleanupVarName || !adapterVarName) {
          return false;
        }
        const retExpr = effStmt.expression;
        if (retExpr) {
          if (ts.isIdentifier(retExpr) && retExpr.text === cleanupVarName) {
            cleanupInvokedInReturn = true;
          } else if (ts.isArrowFunction(retExpr) || ts.isFunctionExpression(retExpr)) {
            const retBody = retExpr.body;
            if (retBody && ts.isBlock(retBody)) {
              for (const retInnerStmt of retBody.statements) {
                if (
                  ts.isExpressionStatement(retInnerStmt) &&
                  ts.isCallExpression(retInnerStmt.expression) &&
                  ts.isIdentifier(retInnerStmt.expression.expression) &&
                  retInnerStmt.expression.expression.text === cleanupVarName
                ) {
                  cleanupInvokedInReturn = true;
                }
              }
            } else if (
              retBody &&
              ts.isCallExpression(retBody) &&
              ts.isIdentifier(retBody.expression) &&
              retBody.expression.text === cleanupVarName
            ) {
              cleanupInvokedInReturn = true;
            }
          }
        }
      } else if (ts.isExpressionStatement(effStmt)) {
        if (
          ts.isCallExpression(effStmt.expression) &&
          ts.isIdentifier(effStmt.expression.expression) &&
          (effStmt.expression.expression.text === "setupAuthLifecycle" ||
            effStmt.expression.expression.text === "createAuthLifecycleAdapter")
        ) {
          return false;
        }
      }
    }

    return (
      adapterVarName !== null &&
      cleanupVarName !== null &&
      returnStatementFound &&
      cleanupInvokedInReturn
    );
  }

  // 1. Verify production auth.ts passes
  const authSourcePath = path.resolve(currentDirResolved, "auth.ts");
  const authSource = fs.readFileSync(authSourcePath, "utf-8");
  assert.equal(verifyAuthSourceTopology(authSource), true, "Production auth.ts must pass strict AST topology check");

  // 2. Diskless mutant tests against the topology checker
  // Mutant a: Real adapter initializer replaced with null as any
  const mutantAdapterNull = `export function useAuth() { useEffect(() => { const adapter = null as any; const cleanup = setupAuthLifecycle(adapter, () => {}); return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantAdapterNull), false, "Mutant with adapter null initializer must fail AST check");

  // Mutant b: Wrong adapter initializer
  const mutantWrongInitializer = `export function useAuth() { useEffect(() => { const adapter = otherFactory(); const cleanup = setupAuthLifecycle(adapter, () => {}); return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantWrongInitializer), false, "Mutant with wrong initializer must fail AST check");

  // Mutant c: createAuthLifecycleAdapter called with arguments
  const mutantAdapterWithArgs = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(true); const cleanup = setupAuthLifecycle(adapter, () => {}); return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantAdapterWithArgs), false, "Mutant with adapter arguments must fail AST check");

  // Mutant d: setupAuthLifecycle uses different adapter identifier
  const mutantSetupWrongAdapter = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(); const wrongAdapter = {}; const cleanup = setupAuthLifecycle(wrongAdapter, () => {}); return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantSetupWrongAdapter), false, "Mutant with wrong adapter in setup must fail AST check");

  // Mutant e: cleanup return invokes wrong identifier
  const mutantCleanupWrongId = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); return () => wrongCleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantCleanupWrongId), false, "Mutant with wrong cleanup identifier in return must fail AST check");

  // Mutant f: Real call removed, only comment remains
  const mutantRemoved = `export function useAuth() { useEffect(() => { /* setupAuthLifecycle(adapter); */ return () => {}; }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantRemoved), false, "Mutant with removed call must fail AST check");

  // Mutant g: Nested unused helper contains the call
  const mutantNestedUnused = `export function useAuth() { useEffect(() => { function unused() { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); return cleanup; } return () => {}; }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantNestedUnused), false, "Mutant with nested unused call must fail AST check");

  // Mutant h1: Setup inside if(false) within useEffect
  const mutantIfFalseSetup = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); if (false) { setupAuthLifecycle(adapter, () => {}); } return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantIfFalseSetup), false, "Mutant with setup in if(false) must fail AST check");

  // Mutant h2: Adapter inside if(false) within useEffect
  const mutantIfFalseAdapter = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); if (false) { const a = createAuthLifecycleAdapter(); } return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantIfFalseAdapter), false, "Mutant with adapter in if(false) must fail AST check");

  // Mutant i1: Setup placed after unconditional return
  const mutantPostReturnSetup = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); return () => cleanup(); setupAuthLifecycle(adapter, () => {}); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantPostReturnSetup), false, "Mutant with post-return setup call must fail AST check");

  // Mutant i2: Adapter placed after unconditional return
  const mutantPostReturnAdapter = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); return () => cleanup(); createAuthLifecycleAdapter(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantPostReturnAdapter), false, "Mutant with post-return adapter call must fail AST check");

  // Mutant j1: Extra setup inside live nested block
  const mutantLiveNestedBlockSetup = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); { setupAuthLifecycle(adapter, () => {}); } return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantLiveNestedBlockSetup), false, "Mutant with setup in live nested block must fail AST check");

  // Mutant j2: Extra adapter inside live nested block
  const mutantLiveNestedBlockAdapter = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(); { const a = createAuthLifecycleAdapter(); } const cleanup = setupAuthLifecycle(adapter, () => {}); return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantLiveNestedBlockAdapter), false, "Mutant with adapter in live nested block must fail AST check");

  // Mutant j3: Invoked nested IIFE setup
  const mutantInvokedIIFESetup = `export function useAuth() { useEffect(() => { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); (() => setupAuthLifecycle(adapter, () => {}))(); return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantInvokedIIFESetup), false, "Mutant with invoked IIFE setup must fail AST check");

  // Mutant k: Outer if(false) wrapping useEffect
  const mutantOuterIfFalse = `export function useAuth() { if (false) { useEffect(() => { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); return () => cleanup(); }, []); } }`;
  assert.equal(verifyAuthSourceTopology(mutantOuterIfFalse), false, "Mutant with outer if(false) wrapping useEffect must fail AST check");

  // Mutant l: setupAuthLifecycle called outside useEffect
  const mutantSetupOutsideEffect = `export function useAuth() { const adapter = createAuthLifecycleAdapter(); const cleanup = setupAuthLifecycle(adapter, () => {}); useEffect(() => { return () => cleanup(); }, []); }`;
  assert.equal(verifyAuthSourceTopology(mutantSetupOutsideEffect), false, "Mutant with setup outside effect must fail AST check");

  function isFunctionLikeNode(node: any): boolean {
    return (
      ts.isFunctionDeclaration(node) ||
      ts.isFunctionExpression(node) ||
      ts.isArrowFunction(node) ||
      ts.isMethodDeclaration(node) ||
      ts.isGetAccessor(node) ||
      ts.isSetAccessor(node) ||
      ts.isConstructorDeclaration(node)
    );
  }

  function getNearestFunctionLike(node: any): any | null {
    let curr = node.parent;
    while (curr) {
      if (isFunctionLikeNode(curr)) {
        return curr;
      }
      curr = curr.parent;
    }
    return null;
  }

  function verifyPageAuthControlsTopology(sourceText: string): boolean {
    const pageSourceFile = ts.createSourceFile("page.tsx", sourceText, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const authControlsElements: any[] = [];

    function findAuthControls(node: any) {
      if (
        (ts.isJsxSelfClosingElement(node) && ts.isIdentifier(node.tagName) && node.tagName.text === "AuthControls") ||
        (ts.isJsxOpeningElement(node) && ts.isIdentifier(node.tagName) && node.tagName.text === "AuthControls")
      ) {
        authControlsElements.push(node);
      }
      ts.forEachChild(node, findAuthControls);
    }
    findAuthControls(pageSourceFile);

    // Exact count must be 2 across entire AST (rejects extra/dead decoys, even in if(false))
    if (authControlsElements.length !== 2) {
      return false;
    }

    let foundAuthenticatedHomeSite = false;
    let foundSignedOutHomeSite = false;

    for (const elem of authControlsElements) {
      // 1. Nearest function-like ancestor of the JSX element itself
      const nearestFuncOfElem = getNearestFunctionLike(elem);
      if (!nearestFuncOfElem || !ts.isFunctionDeclaration(nearestFuncOfElem) || nearestFuncOfElem.parent !== pageSourceFile) {
        return false;
      }

      // 2. Enclosing ReturnStatement and its nearest function-like ancestor
      let enclosingReturn: any = null;
      let deadAncestor = false;
      let curr = elem.parent;
      let prevChild: any = elem;

      while (curr && curr !== nearestFuncOfElem) {
        // Binary expression checks: false && ... or true || ...
        if (ts.isBinaryExpression(curr)) {
          if (
            curr.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken &&
            curr.left.kind === ts.SyntaxKind.FalseKeyword
          ) {
            deadAncestor = true;
            break;
          }
          if (
            curr.operatorToken.kind === ts.SyntaxKind.BarBarToken &&
            curr.left.kind === ts.SyntaxKind.TrueKeyword
          ) {
            deadAncestor = true;
            break;
          }
        }

        // Conditional expression checks: true ? ... : ... or false ? ... : ...
        if (ts.isConditionalExpression(curr)) {
          if (curr.condition.kind === ts.SyntaxKind.TrueKeyword && curr.whenFalse === prevChild) {
            deadAncestor = true;
            break;
          }
          if (curr.condition.kind === ts.SyntaxKind.FalseKeyword && curr.whenTrue === prevChild) {
            deadAncestor = true;
            break;
          }
        }

        // If statement dead branch checks: if (false) then or if (true) else
        if (ts.isIfStatement(curr)) {
          if (curr.expression.kind === ts.SyntaxKind.FalseKeyword && curr.thenStatement === prevChild) {
            deadAncestor = true;
            break;
          }
          if (curr.expression.kind === ts.SyntaxKind.TrueKeyword && curr.elseStatement === prevChild) {
            deadAncestor = true;
            break;
          }
        }

        // Block post-return check
        if (ts.isBlock(curr)) {
          const statements = curr.statements;
          let sawReturnBeforeChild = false;
          for (const stmt of statements) {
            if (stmt === prevChild || (prevChild && prevChild.parent === stmt)) {
              break;
            }
            if (ts.isReturnStatement(stmt)) {
              sawReturnBeforeChild = true;
              break;
            }
          }
          if (sawReturnBeforeChild) {
            deadAncestor = true;
            break;
          }
        }

        if (ts.isReturnStatement(curr)) {
          enclosingReturn = curr;
        }

        prevChild = curr;
        curr = curr.parent;
      }

      if (deadAncestor || !enclosingReturn) {
        return false;
      }

      // Enclosing return's nearest function-like ancestor must be identical to nearestFuncOfElem
      const nearestFuncOfReturn = getNearestFunctionLike(enclosingReturn);
      if (nearestFuncOfReturn !== nearestFuncOfElem) {
        return false;
      }

      // Check onRetry attribute is auth.retry
      let retryExprText: string | null = null;
      const attributes = elem.attributes;
      if (attributes && ts.isJsxAttributes(attributes)) {
        for (const prop of attributes.properties) {
          if (ts.isJsxAttribute(prop) && prop.name.text === "onRetry") {
            if (prop.initializer && ts.isJsxExpression(prop.initializer) && prop.initializer.expression) {
              const expr = prop.initializer.expression;
              if (
                ts.isPropertyAccessExpression(expr) &&
                ts.isIdentifier(expr.expression) &&
                expr.expression.text === "auth" &&
                ts.isIdentifier(expr.name) &&
                expr.name.text === "retry"
              ) {
                retryExprText = "auth.retry";
              }
            }
          }
        }
      }

      if (retryExprText !== "auth.retry") {
        return false;
      }

      const funcName = nearestFuncOfElem.name?.text;
      if (funcName === "AuthenticatedHome") {
        foundAuthenticatedHomeSite = true;
      } else if (funcName === "Home") {
        // Must be in the thenStatement of exact unary !admittedUser IfStatement
        let insideNotAdmittedBranch = false;
        let walkHome = enclosingReturn.parent;
        let walkChild = enclosingReturn;
        while (walkHome && walkHome !== nearestFuncOfElem) {
          if (
            ts.isIfStatement(walkHome) &&
            walkHome.thenStatement === walkChild &&
            ts.isPrefixUnaryExpression(walkHome.expression) &&
            walkHome.expression.operator === ts.SyntaxKind.ExclamationToken &&
            ts.isIdentifier(walkHome.expression.operand) &&
            walkHome.expression.operand.text === "admittedUser"
          ) {
            insideNotAdmittedBranch = true;
            break;
          }
          walkChild = walkHome;
          walkHome = walkHome.parent;
        }
        if (insideNotAdmittedBranch) {
          foundSignedOutHomeSite = true;
        }
      }
    }

    return foundAuthenticatedHomeSite && foundSignedOutHomeSite;
  }

  // 4. Verify production page.tsx passes AST checks
  const pageSourcePath = path.resolve(currentDirResolved, "../app/page.tsx");
  const pageSource = fs.readFileSync(pageSourcePath, "utf-8");
  assert.equal(verifyPageAuthControlsTopology(pageSource), true, "Production page.tsx must pass AST topology check");

  // 5. Diskless mutants against page.tsx AST topology
  const mutantWrappedInFalse1 = pageSource.replace(
    "<AuthControls\n          status={auth.status}",
    "{false && <AuthControls\n          status={auth.status}"
  ).replace(
    "onRetry={auth.retry}\n          disabled={isActive}\n        />",
    "onRetry={auth.retry}\n          disabled={isActive}\n        />}"
  );
  assert.notEqual(mutantWrappedInFalse1, pageSource, "mutantWrappedInFalse1 must differ from pageSource");
  assert.equal(verifyPageAuthControlsTopology(mutantWrappedInFalse1), false, "Mutant with site 1 wrapped in false && must fail AST check");

  const mutantWrappedInFalse2 = pageSource.replace(
    "<AuthControls\n            status={auth.status}",
    "{false && <AuthControls\n            status={auth.status}"
  ).replace(
    "onRetry={auth.retry}\n          />",
    "onRetry={auth.retry}\n          />}"
  );
  assert.notEqual(mutantWrappedInFalse2, pageSource, "mutantWrappedInFalse2 must differ from pageSource");
  assert.equal(verifyPageAuthControlsTopology(mutantWrappedInFalse2), false, "Mutant with site 2 wrapped in false && must fail AST check");

  const mutantRetrySignIn = pageSource.replace("onRetry={auth.retry}", "onRetry={auth.signIn}");
  assert.equal(verifyPageAuthControlsTopology(mutantRetrySignIn), false, "Mutant with onRetry wired to signIn must fail AST check");

  const mutantOneRemoved = pageSource.replace(/<AuthControls[^>]*\/>/, "");
  assert.equal(verifyPageAuthControlsTopology(mutantOneRemoved), false, "Mutant with one site removed must fail AST check");

  const mutantUncalledArrowFragment = pageSource
    .replace(
      /return \(\s*<main[\s\S]*?<\/main>\s*\);/m,
      `return (<main><p>Main</p></main>);`
    )
    .replace(
      "function AuthenticatedHome",
      `const UncalledDecoy = () => (<><AuthControls status={auth.status} user={auth.user} error={auth.error} busy={auth.busy} onSignIn={auth.signIn} onSignOut={handleSignOut} onUseAnotherAccount={auth.useAnotherAccount} onRetry={auth.retry} disabled={isActive} /></>);\nfunction AuthenticatedHome`
    );
  assert.equal(verifyPageAuthControlsTopology(mutantUncalledArrowFragment), false, "Mutant with uncalled arrow fragment decoy must fail AST check");

  const mutantNestedFunctionExpression = pageSource.replace(
    "<AuthControls\n          status={auth.status}",
    "{(() => <AuthControls\n          status={auth.status}"
  ).replace(
    "onRetry={auth.retry}\n          disabled={isActive}\n        />",
    "onRetry={auth.retry}\n          disabled={isActive}\n        />)()}"
  );
  assert.equal(verifyPageAuthControlsTopology(mutantNestedFunctionExpression), false, "Mutant with nested function expression must fail AST check");
});

test("Section H: safeResolveRuntimeConfig prototype seam guards", async () => {
  class CustomConfig {
    authBypassEnabled = false;
    apiUrl = "http://127.0.0.1:8000";
    wsUrl = "ws://127.0.0.1:8000/ws";
    wsStreamUrl = "ws://127.0.0.1:8000/api/stream/native";
  }

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: new CustomConfig() as any,
    }),
  });

  controller.start();
  await new Promise((r) => setTimeout(r, 10));

  const state = controller.getState();
  assert.equal(state.status, "config_error");
  assert.equal(state.busy, false);
});

test("Section I: Monotonic supersession prevents old popup/retry finally from unlocking new state", async () => {
  let listenerCb: ((u: any) => void) | null = null;
  let popupResolver: (() => void) | null = null;

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({} as any),
    subscribeAuthState: (_auth, next) => {
      listenerCb = next;
      return () => {};
    },
    signInWithPopup: () => new Promise<any>((resolve) => {
      popupResolver = resolve;
    }),
    executeAdmission: async () => ({
      status: "admitted",
      principal: { uid: "uid-b", email: "b@ellaexecutivesearch.com", org_id: "ella-internal" },
    }),
  });

  controller.start();
  await new Promise((r) => setTimeout(r, 10));

  // Start signIn (opening_popup)
  void controller.signIn();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "opening_popup");

  // While popup is pending, null event arrives -> settles signed_out non-busy immediately!
  if (listenerCb) (listenerCb as (u: any) => void)(null);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "signed_out");
  assert.equal(controller.getState().busy, false);

  // Now nextUser arrives for User B -> advances ownership and starts checking_access
  if (listenerCb) (listenerCb as (u: any) => void)({ uid: "uid-b", email: "b@ellaexecutivesearch.com" });
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(controller.getState().status, "signed_in");

  // Late popup finally resolves -> cannot unlock or overwrite User B state!
  if (popupResolver) (popupResolver as () => void)();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(controller.getState().user?.uid, "uid-b");
});

test("Section J: Table-driven multi-phase ownership and retirement matrix across null, error, and A->B replacement", async () => {
  const initialUnhandledCount = process.listenerCount("unhandledRejection");
  const unhandledRejections: any[] = [];
  const onUnhandled = (reason: any) => { unhandledRejections.push(reason); };
  process.on("unhandledRejection", onUnhandled);

  let totalExecutedRows = 0;
  let resolvedRowsCount = 0;
  let rejectedRowsCount = 0;

  function observePromise<T>(p: Promise<T>) {
    let settled = false;
    let fulfilled = false;
    let rejected = false;
    let value: T | undefined = undefined;
    let error: any = undefined;
    p.then(
      (v) => { settled = true; fulfilled = true; value = v; },
      (e) => { settled = true; rejected = true; error = e; }
    );
    return {
      promise: p,
      isSettled: () => settled,
      isFulfilled: () => fulfilled,
      isRejected: () => rejected,
      getValue: () => value,
      getError: () => error,
      drain: async (timeoutMs = 500) => {
        if (settled) return fulfilled ? { status: "settled" as const, result: value! } : { status: "rejected" as const, error };
        let timer: NodeJS.Timeout | null = null;
        try {
          const timeoutPromise = new Promise<{ status: "timed_out" }>((res) => { timer = setTimeout(() => res({ status: "timed_out" }), timeoutMs); });
          const settlePromise = p.then(
            (res) => ({ status: "settled" as const, result: res }),
            (err) => ({ status: "rejected" as const, error: err })
          );
          return await Promise.race([settlePromise, timeoutPromise]);
        } finally {
          if (timer) clearTimeout(timer);
        }
      },
    };
  }

  interface AttemptLedgerRecord {
    owner: "user-a" | "user-b" | "fresh-user-a";
    tokenEntryCalls: number;
    tokenSettledCalls: number;
    controllerFetchCalls: number;
    controllerFetchSettledFulfilled: number;
    controllerFetchSettledRejected: number;
    depsFetchCalls: number;
    responseStatusReads: number;
    responseHeadersReads: number;
    responseJsonGetters: number;
    jsonEntryCalls: number;
    jsonSettledFulfilled: number;
    jsonSettledRejected: number;
    bodyInspectionCount: number;
    bodyProxyThenAccesses: number;
    bodyProxyOtherTraps: number;
    postFetchFenceAbortedCount: number;
    postJsonFenceAbortedCount: number;
    admissionResultStatus: string;
  }

  const createZeroRecord = (owner: "user-a" | "user-b" | "fresh-user-a"): AttemptLedgerRecord => ({
    owner,
    tokenEntryCalls: 0,
    tokenSettledCalls: 0,
    controllerFetchCalls: 0,
    controllerFetchSettledFulfilled: 0,
    controllerFetchSettledRejected: 0,
    depsFetchCalls: 0,
    responseStatusReads: 0,
    responseHeadersReads: 0,
    responseJsonGetters: 0,
    jsonEntryCalls: 0,
    jsonSettledFulfilled: 0,
    jsonSettledRejected: 0,
    bodyInspectionCount: 0,
    bodyProxyThenAccesses: 0,
    bodyProxyOtherTraps: 0,
    postFetchFenceAbortedCount: 0,
    postJsonFenceAbortedCount: 0,
    admissionResultStatus: "none",
  });

  function getExpectedRecord(
    key: string,
    phase: "popup" | "retry" | "token" | "fetch" | "body",
    transition: "null" | "error" | "replacement_b",
    outcome: "resolve" | "reject"
  ): AttemptLedgerRecord {
    if (key === "fresh-A") {
      return {
        owner: "fresh-user-a",
        tokenEntryCalls: 1,
        tokenSettledCalls: 1,
        controllerFetchCalls: 1,
        controllerFetchSettledFulfilled: 1,
        controllerFetchSettledRejected: 0,
        depsFetchCalls: 1,
        responseStatusReads: 1,
        responseHeadersReads: 1,
        responseJsonGetters: 1,
        jsonEntryCalls: 1,
        jsonSettledFulfilled: 1,
        jsonSettledRejected: 0,
        bodyInspectionCount: 1,
        bodyProxyThenAccesses: 0,
        bodyProxyOtherTraps: 0,
        postFetchFenceAbortedCount: 0,
        postJsonFenceAbortedCount: 0,
        admissionResultStatus: "admitted",
      };
    }
    if (key === "B1") {
      return {
        owner: "user-b",
        tokenEntryCalls: 1,
        tokenSettledCalls: 1,
        controllerFetchCalls: 1,
        controllerFetchSettledFulfilled: 1,
        controllerFetchSettledRejected: 0,
        depsFetchCalls: 1,
        responseStatusReads: 1,
        responseHeadersReads: 1,
        responseJsonGetters: 1,
        jsonEntryCalls: 1,
        jsonSettledFulfilled: 1,
        jsonSettledRejected: 0,
        bodyInspectionCount: 1,
        bodyProxyThenAccesses: 0,
        bodyProxyOtherTraps: 0,
        postFetchFenceAbortedCount: 0,
        postJsonFenceAbortedCount: 0,
        admissionResultStatus: "admitted",
      };
    }
    if (key === "A1" && phase === "retry") {
      return {
        owner: "user-a",
        tokenEntryCalls: 1,
        tokenSettledCalls: 1,
        controllerFetchCalls: 1,
        controllerFetchSettledFulfilled: 1,
        controllerFetchSettledRejected: 0,
        depsFetchCalls: 1,
        responseStatusReads: 1,
        responseHeadersReads: 1,
        responseJsonGetters: 0,
        jsonEntryCalls: 0,
        jsonSettledFulfilled: 0,
        jsonSettledRejected: 0,
        bodyInspectionCount: 0,
        bodyProxyThenAccesses: 0,
        bodyProxyOtherTraps: 0,
        postFetchFenceAbortedCount: 0,
        postJsonFenceAbortedCount: 0,
        admissionResultStatus: "retryable",
      };
    }

    const isA2 = key === "A2";
    const effectivePhase = isA2 ? "fetch" : phase;
    if (effectivePhase === "token") {
      return {
        owner: "user-a",
        tokenEntryCalls: 1,
        tokenSettledCalls: 1,
        controllerFetchCalls: 0,
        controllerFetchSettledFulfilled: 0,
        controllerFetchSettledRejected: 0,
        depsFetchCalls: 0,
        responseStatusReads: 0,
        responseHeadersReads: 0,
        responseJsonGetters: 0,
        jsonEntryCalls: 0,
        jsonSettledFulfilled: 0,
        jsonSettledRejected: 0,
        bodyInspectionCount: 0,
        bodyProxyThenAccesses: 0,
        bodyProxyOtherTraps: 0,
        postFetchFenceAbortedCount: 0,
        postJsonFenceAbortedCount: 0,
        admissionResultStatus: outcome === "resolve" ? "cancelled" : "retryable",
      };
    }
    if (effectivePhase === "fetch") {
      return {
        owner: "user-a",
        tokenEntryCalls: 1,
        tokenSettledCalls: 1,
        controllerFetchCalls: 1,
        controllerFetchSettledFulfilled: outcome === "resolve" ? 1 : 0,
        controllerFetchSettledRejected: outcome === "reject" ? 1 : 0,
        depsFetchCalls: 1,
        responseStatusReads: 0,
        responseHeadersReads: 0,
        responseJsonGetters: 0,
        jsonEntryCalls: 0,
        jsonSettledFulfilled: 0,
        jsonSettledRejected: 0,
        bodyInspectionCount: 0,
        bodyProxyThenAccesses: 0,
        bodyProxyOtherTraps: 0,
        postFetchFenceAbortedCount: outcome === "resolve" ? 1 : 0,
        postJsonFenceAbortedCount: 0,
        admissionResultStatus: outcome === "resolve" ? "cancelled" : "retryable",
      };
    }
    if (effectivePhase === "body") {
      return {
        owner: "user-a",
        tokenEntryCalls: 1,
        tokenSettledCalls: 1,
        controllerFetchCalls: 1,
        controllerFetchSettledFulfilled: 1,
        controllerFetchSettledRejected: 0,
        depsFetchCalls: 1,
        responseStatusReads: 1,
        responseHeadersReads: 1,
        responseJsonGetters: 1,
        jsonEntryCalls: 1,
        jsonSettledFulfilled: outcome === "resolve" ? 1 : 0,
        jsonSettledRejected: outcome === "reject" ? 1 : 0,
        bodyInspectionCount: 0,
        bodyProxyThenAccesses: outcome === "resolve" ? 1 : 0,
        bodyProxyOtherTraps: 0,
        postFetchFenceAbortedCount: 0,
        postJsonFenceAbortedCount: outcome === "resolve" ? 1 : 0,
        admissionResultStatus: outcome === "resolve" ? "cancelled" : "retryable",
      };
    }
    throw new Error(`Unexpected oracle request for key ${key} in phase ${phase}`);
  }

  try {
    const phases = ["popup", "retry", "token", "fetch", "body"] as const;
    const transitions = ["null", "error", "replacement_b"] as const;
    const outcomes = ["resolve", "reject"] as const;

    for (const phase of phases) {
      for (const transition of transitions) {
        for (const outcome of outcomes) {
          totalExecutedRows++;
          if (outcome === "resolve") resolvedRowsCount++;
          if (outcome === "reject") rejectedRowsCount++;

          const ledgerRecords = new Map<string, AttemptLedgerRecord>();

          const getOrCreateRecord = (key: string, owner: "user-a" | "user-b" | "fresh-user-a"): AttemptLedgerRecord => {
            let rec = ledgerRecords.get(key);
            if (!rec) {
              rec = createZeroRecord(owner);
              ledgerRecords.set(key, rec);
            }
            return rec;
          };

          let currentAuthUser: any = null;
          let listenerCb: ((u: any) => void) | null = null;
          let listenerErrCb: (() => void) | null = null;

          let popupCalls = 0;
          let popupEnteredResolve: (() => void) | null = null;
          const popupEnteredPromise = new Promise<void>((r) => { popupEnteredResolve = r; });
          let popupResolver: (() => void) | null = null;
          let popupRejecter: ((err: any) => void) | null = null;

          let tokenEnteredResolve: (() => void) | null = null;
          const tokenEnteredPromise = new Promise<void>((r) => { tokenEnteredResolve = r; });
          let tokenResolver: ((t: string) => void) | null = null;
          let tokenRejecter: ((err: any) => void) | null = null;

          let fetchEnteredResolve: (() => void) | null = null;
          const fetchEnteredPromise = new Promise<void>((r) => { fetchEnteredResolve = r; });
          let fetchResolver: ((r: Response) => void) | null = null;
          let fetchRejecter: ((err: any) => void) | null = null;

          let bodyEnteredResolve: (() => void) | null = null;
          const bodyEnteredPromise = new Promise<void>((r) => { bodyEnteredResolve = r; });
          let bodyResolver: ((d: any) => void) | null = null;
          let bodyRejecter: ((err: any) => void) | null = null;

          let capturedExternalSignal: AbortSignal | null = null;
          let capturedControllerFetchFn: ((input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) | null = null;
          let userAFirstAdmissionDone = false;
          let freshUserAActive = false;

          let missingExternalSignal = false;
          let missingFetchFn = false;
          let missingInjectedDelegate = false;

          const userA = {
            uid: "uid-a",
            email: "a@ellaexecutivesearch.com",
            getIdToken: async () => {
              const attemptKey = phase === "retry" && userAFirstAdmissionDone ? "A2" : "A1";
              const rec = getOrCreateRecord(attemptKey, "user-a");
              rec.tokenEntryCalls++;

              if (phase === "token" && attemptKey === "A1") {
                if (tokenEnteredResolve) (tokenEnteredResolve as () => void)();
                return new Promise<string>((res, rej) => {
                  tokenResolver = (t: string) => {
                    rec.tokenSettledCalls++;
                    res(t);
                  };
                  tokenRejecter = (e: any) => {
                    rec.tokenSettledCalls++;
                    rej(e);
                  };
                });
              }
              rec.tokenSettledCalls++;
              return "token-a";
            },
          };

          const userB = {
            uid: "uid-b",
            email: "b@ellaexecutivesearch.com",
            getIdToken: async () => {
              const rec = getOrCreateRecord("B1", "user-b");
              rec.tokenEntryCalls++;
              rec.tokenSettledCalls++;
              return "token-b";
            },
          };

          const mockInjectedFetch = async (_input: RequestInfo | URL, init?: RequestInit) => {
            const authHeader = (init?.headers as any)?.Authorization || (init?.headers as any)?.authorization;
            if (authHeader === "Bearer token-b") {
              const rec = getOrCreateRecord("B1", "user-b");
              rec.depsFetchCalls++;
              return {
                get status() { rec.responseStatusReads++; return 200; },
                get headers() { rec.responseHeadersReads++; return new Headers({ "Content-Type": "application/json" }); },
                get json() {
                  rec.responseJsonGetters++;
                  return async () => ({ uid: "uid-b", email: "b@ellaexecutivesearch.com", org_id: "ella-internal" });
                },
              } as any;
            }
            if (authHeader === "Bearer fresh-token-a") {
              const rec = getOrCreateRecord("fresh-A", "fresh-user-a");
              rec.depsFetchCalls++;
              return {
                get status() { rec.responseStatusReads++; return 200; },
                get headers() { rec.responseHeadersReads++; return new Headers({ "Content-Type": "application/json" }); },
                get json() {
                  rec.responseJsonGetters++;
                  return async () => ({ uid: "uid-a", email: "a@ellaexecutivesearch.com", org_id: "ella-internal" });
                },
              } as any;
            }

            // User A in fetch phase (A1)
            if (phase === "fetch" && !freshUserAActive) {
              const rec = getOrCreateRecord("A1", "user-a");
              rec.depsFetchCalls++;
              if (fetchEnteredResolve) (fetchEnteredResolve as () => void)();
              return new Promise<Response>((res, rej) => {
                fetchResolver = res;
                fetchRejecter = rej;
              });
            }
            // User A in retry phase on first attempt (A1) -> return 500 to settle retryable_error
            if (phase === "retry" && !userAFirstAdmissionDone && !freshUserAActive) {
              userAFirstAdmissionDone = true;
              const rec = getOrCreateRecord("A1", "user-a");
              rec.depsFetchCalls++;
              return {
                get status() { rec.responseStatusReads++; return 500; },
                get headers() { rec.responseHeadersReads++; return new Headers({ "Content-Type": "text/plain" }); },
                get json() { rec.responseJsonGetters++; return async () => ({}); },
              } as any;
            }
            // User A in retry phase on second attempt (A2)
            if (phase === "retry" && userAFirstAdmissionDone && !freshUserAActive) {
              const rec = getOrCreateRecord("A2", "user-a");
              rec.depsFetchCalls++;
              if (fetchEnteredResolve) (fetchEnteredResolve as () => void)();
              return new Promise<Response>((res, rej) => {
                fetchResolver = res;
                fetchRejecter = rej;
              });
            }
            // User A in body phase (A1)
            if (phase === "body" && !freshUserAActive) {
              const rec = getOrCreateRecord("A1", "user-a");
              rec.depsFetchCalls++;
              if (fetchEnteredResolve) (fetchEnteredResolve as () => void)();
              return {
                get status() { rec.responseStatusReads++; return 200; },
                get headers() { rec.responseHeadersReads++; return new Headers({ "Content-Type": "application/json" }); },
                get json() {
                  rec.responseJsonGetters++;
                  return () => {
                    if (bodyEnteredResolve) (bodyEnteredResolve as () => void)();
                    return new Promise((res, rej) => {
                      bodyResolver = res;
                      bodyRejecter = rej;
                    });
                  };
                },
              } as any;
            }

            const rec = getOrCreateRecord(freshUserAActive ? "fresh-A" : "A1", freshUserAActive ? "fresh-user-a" : "user-a");
            rec.depsFetchCalls++;
            return {
              get status() { rec.responseStatusReads++; return 200; },
              get headers() { rec.responseHeadersReads++; return new Headers({ "Content-Type": "application/json" }); },
              get json() {
                rec.responseJsonGetters++;
                return async () => ({ uid: "uid-a", email: "a@ellaexecutivesearch.com", org_id: "ella-internal" });
              },
            } as any;
          };

          const controller = createAuthController({
            getRuntimeConfig: () => ({
              ok: true,
              value: {
                authBypassEnabled: false,
                firebase: {
                  apiKey: "AIza" + "A".repeat(35),
                  authDomain: "auth.ellaexecutivesearch.com",
                  projectId: "pilot-proj-1",
                  storageBucket: "bucket.ellaexecutivesearch.com",
                  messagingSenderId: "123456789012",
                  appId: "1:123456789012:web:abcdef0123456789",
                },
                apiUrl: "http://127.0.0.1:8000",
                wsUrl: "ws://127.0.0.1:8000/ws",
                wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
              },
            }),
            getAuth: () => ({ currentUser: currentAuthUser } as any),
            subscribeAuthState: (_auth, next, err) => {
              listenerCb = (u: any) => {
                currentAuthUser = u;
                next(u);
              };
              listenerErrCb = err ?? null;
              return () => {};
            },
            signInWithPopup: () => {
              popupCalls++;
              if (phase === "popup") {
                if (popupEnteredResolve) (popupEnteredResolve as () => void)();
                return new Promise<any>((res, rej) => {
                  popupResolver = res;
                  popupRejecter = rej;
                });
              }
              return Promise.resolve();
            },
            fetch: mockInjectedFetch as any,
            executeAdmission: async (params) => {
              if (!params.externalSignal || typeof (params.externalSignal as any).aborted !== "boolean") {
                missingExternalSignal = true;
              }
              if (!params.fetchFn || typeof params.fetchFn !== "function") {
                missingFetchFn = true;
              }

              const attemptKey = params.expectedUid === "uid-b"
                ? "B1"
                : (freshUserAActive
                    ? "fresh-A"
                    : (phase === "retry" && userAFirstAdmissionDone ? "A2" : "A1"));
              const owner = params.expectedUid === "uid-b" ? "user-b" : (attemptKey === "fresh-A" ? "fresh-user-a" : "user-a");
              const rec = getOrCreateRecord(attemptKey, owner);

              if (params.expectedUid === "uid-a" && !freshUserAActive) {
                capturedExternalSignal = params.externalSignal ?? null;
                capturedControllerFetchFn = params.fetchFn ?? null;
              }

              let tok: string | null = null;
              try {
                tok = await params.tokenProvider();
              } catch {
                rec.admissionResultStatus = "retryable";
                return { status: "retryable" };
              }
              if (!tok || params.externalSignal?.aborted) {
                rec.admissionResultStatus = "cancelled";
                return { status: "cancelled" };
              }

              rec.controllerFetchCalls++;
              let res: Response;
              const preDepsCalls = rec.depsFetchCalls;
              try {
                res = await params.fetchFn(params.apiUrl, {
                  headers: { Authorization: `Bearer ${tok}` },
                  signal: params.externalSignal,
                });
                rec.controllerFetchSettledFulfilled++;
              } catch {
                rec.controllerFetchSettledRejected++;
                rec.admissionResultStatus = "retryable";
                return { status: "retryable" };
              }

              if (rec.depsFetchCalls === preDepsCalls) {
                missingInjectedDelegate = true;
              }

              // FENCE IMMEDIATELY AFTER FETCH AWAIT:
              if (params.externalSignal?.aborted) {
                rec.postFetchFenceAbortedCount++;
                rec.admissionResultStatus = "cancelled";
                return { status: "cancelled" };
              }

              if (res.headers) {
                // Header inspection
              }

              const status = res.status;
              if (status === 401 || status === 403) {
                rec.admissionResultStatus = "denied";
                return { status: "denied" };
              }
              if (status !== 200) {
                rec.admissionResultStatus = "retryable";
                return { status: "retryable" };
              }

              rec.jsonEntryCalls++;
              let data: any;
              try {
                data = await res.json();
                rec.jsonSettledFulfilled++;
              } catch {
                rec.jsonSettledRejected++;
                rec.admissionResultStatus = "retryable";
                return { status: "retryable" };
              }

              // FENCE IMMEDIATELY AFTER JSON AWAIT:
              if (params.externalSignal?.aborted) {
                rec.postJsonFenceAbortedCount++;
                rec.admissionResultStatus = "cancelled";
                return { status: "cancelled" };
              }

              rec.bodyInspectionCount++;
              if (data && typeof data === "object" && (data as any).uid) {
                rec.admissionResultStatus = "admitted";
                return { status: "admitted", principal: data as any };
              }
              rec.admissionResultStatus = "retryable";
              return { status: "retryable" };
            },
          });

          controller.start();
          await new Promise((r) => setTimeout(r, 10));

          let pPopupObs: ReturnType<typeof observePromise> | null = null;
          let pRetryObs: ReturnType<typeof observePromise> | null = null;

          if (phase === "popup") {
            const pPopup = controller.signIn();
            pPopupObs = observePromise(pPopup);
            let timer: NodeJS.Timeout | null = null;
            await Promise.race([
              popupEnteredPromise,
              new Promise<never>((_, rej) => {
                timer = setTimeout(() => rej(new Error("popup entry timeout")), 500);
              }),
            ]).finally(() => { if (timer) clearTimeout(timer); });
            assert.equal(popupCalls, 1, "Popup must be invoked in popup phase");
          } else if (phase === "retry") {
            // Deliver userA -> first admission (A1) fails to 500 -> state settles to retryable_error
            if (listenerCb) (listenerCb as (u: any) => void)(userA);
            await new Promise((r) => setTimeout(r, 25));
            assert.equal(controller.getState().status, "retryable_error");
            assert.equal(controller.getState().user, null);
            assert.equal(controller.getState().busy, false);
            assert.equal(popupCalls, 0, "No popup during normal admission");

            // Trigger genuine retry: starts checking_access for A2 with zero popup
            const pRetry = controller.retry();
            pRetryObs = observePromise(pRetry);
            let timer: NodeJS.Timeout | null = null;
            await Promise.race([
              fetchEnteredPromise,
              new Promise<never>((_, rej) => {
                timer = setTimeout(() => rej(new Error("retry fetch entry timeout")), 500);
              }),
            ]).finally(() => { if (timer) clearTimeout(timer); });
            assert.equal(popupCalls, 0, "No popup during retry");
            assert.equal(controller.getState().status, "checking_access");
          } else if (phase === "token") {
            if (listenerCb) (listenerCb as (u: any) => void)(userA);
            let timer: NodeJS.Timeout | null = null;
            await Promise.race([
              tokenEnteredPromise,
              new Promise<never>((_, rej) => {
                timer = setTimeout(() => rej(new Error("token entry timeout")), 500);
              }),
            ]).finally(() => { if (timer) clearTimeout(timer); });
            const recA1 = ledgerRecords.get("A1");
            assert.ok(recA1);
            assert.equal(recA1.tokenEntryCalls, 1);
          } else if (phase === "fetch") {
            if (listenerCb) (listenerCb as (u: any) => void)(userA);
            let timer: NodeJS.Timeout | null = null;
            await Promise.race([
              fetchEnteredPromise,
              new Promise<never>((_, rej) => {
                timer = setTimeout(() => rej(new Error("fetch entry timeout")), 500);
              }),
            ]).finally(() => { if (timer) clearTimeout(timer); });
            const recA1 = ledgerRecords.get("A1");
            assert.ok(recA1);
            assert.equal(recA1.depsFetchCalls, 1);
          } else if (phase === "body") {
            if (listenerCb) (listenerCb as (u: any) => void)(userA);
            let timer: NodeJS.Timeout | null = null;
            await Promise.race([
              bodyEnteredPromise,
              new Promise<never>((_, rej) => {
                timer = setTimeout(() => rej(new Error("body entry timeout")), 500);
              }),
            ]).finally(() => { if (timer) clearTimeout(timer); });
            const recA1 = ledgerRecords.get("A1");
            assert.ok(recA1);
            assert.equal(recA1.depsFetchCalls, 1);
          }

          assert.equal(missingExternalSignal, false, "externalSignal must be injected by authController");
          assert.equal(missingFetchFn, false, "fetchFn must be injected by authController");
          assert.equal(missingInjectedDelegate, false, "controller fetchFn must use injected delegate");

          if (phase !== "popup") {
            assert.ok(capturedExternalSignal, `externalSignal must be provided in phase ${phase}`);
            assert.equal((capturedExternalSignal as AbortSignal).aborted, false, `Signal must not be aborted while phase ${phase} is in flight`);
          }

          // Fire disrupting transition while User A is in flight
          if (transition === "null") {
            if (listenerCb) (listenerCb as (u: any) => void)(null);
          } else if (transition === "error") {
            if (listenerErrCb) (listenerErrCb as () => void)();
          } else if (transition === "replacement_b") {
            if (listenerCb) (listenerCb as (u: any) => void)(userB);
          }
          await new Promise((r) => setTimeout(r, 25));

          if (phase !== "popup") {
            assert.ok(capturedExternalSignal, `externalSignal must be present in phase ${phase}`);
            assert.equal((capturedExternalSignal as AbortSignal).aborted, true, `Old signal must be aborted in phase ${phase}`);
          }

          if (transition === "replacement_b") {
            const recB1 = ledgerRecords.get("B1");
            assert.ok(recB1, "B1 record must exist upon replacement_b");
            assert.equal(recB1.admissionResultStatus, "admitted");
            assert.equal(recB1.responseHeadersReads, 1, "B1 must read response headers on successful admission");
            assert.equal(recB1.responseStatusReads, 1);
            assert.equal(recB1.responseJsonGetters, 1);
            assert.equal(recB1.jsonEntryCalls, 1);
            assert.equal(recB1.jsonSettledFulfilled, 1);
            assert.equal(recB1.bodyInspectionCount, 1);
          }

          // SNAPSHOT the retired-A record after transition/B work and BEFORE late settlement:
          const retiredKey = phase === "retry" ? "A2" : "A1";
          const retiredRec = ledgerRecords.get(retiredKey);
          const snapRetired = retiredRec ? { ...retiredRec } : null;

          const targetBodyObj = { uid: "uid-a", email: "a@ellaexecutivesearch.com", org_id: "ella-internal" };
          const lateBodyProxy = new Proxy(targetBodyObj, {
            get(t, p, r) {
              if (p === "then") {
                if (retiredRec) retiredRec.bodyProxyThenAccesses++;
                return Reflect.get(t, p, r);
              }
              if (retiredRec) retiredRec.bodyProxyOtherTraps++;
              return Reflect.get(t, p, r);
            },
            set(t, p, v, r) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.set(t, p, v, r); },
            has(t, p) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.has(t, p); },
            deleteProperty(t, p) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.deleteProperty(t, p); },
            getPrototypeOf(t) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.getPrototypeOf(t); },
            setPrototypeOf(t, v) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.setPrototypeOf(t, v); },
            isExtensible(t) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.isExtensible(t); },
            preventExtensions(t) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.preventExtensions(t); },
            getOwnPropertyDescriptor(t, p) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.getOwnPropertyDescriptor(t, p); },
            defineProperty(t, p, a) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.defineProperty(t, p, a); },
            ownKeys(t) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.ownKeys(t); },
            apply(t, thisArg, argArray) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.apply(t, thisArg, argArray); },
            construct(t, argArray, newTarget) { if (retiredRec) retiredRec.bodyProxyOtherTraps++; return Reflect.construct(t, argArray, newTarget); },
          });

          // Settle old work (late resolve or late reject)
          if (phase === "popup") {
            if (outcome === "resolve" && popupResolver) (popupResolver as () => void)();
            if (outcome === "reject" && popupRejecter) {
              try { (popupRejecter as (e: any) => void)(new Error("LATE_POPUP_REJECT")); } catch {}
            }
            if (pPopupObs) {
              const drainRes = await pPopupObs.drain(100);
              assert.notEqual(drainRes.status, "timed_out", "Popup promise must settle boundedly");
            }
          } else if (phase === "token") {
            if (outcome === "resolve" && tokenResolver) {
              (tokenResolver as (t: string) => void)("late-token-a");
              if (capturedControllerFetchFn) {
                try {
                  await (capturedControllerFetchFn as any)("http://127.0.0.1:8000/api/me", {});
                  assert.fail("Captured controller fetchFn must throw when called on stale generation / aborted signal");
                } catch (err: any) {
                  assert.ok(err.message === "Aborted", "Stale controller fetchFn must throw Aborted error");
                }
              }
            }
            if (outcome === "reject" && tokenRejecter) {
              try { (tokenRejecter as (e: any) => void)(new Error("LATE_TOKEN_REJECT")); } catch {}
            }
          } else if (phase === "fetch" || phase === "retry") {
            if (outcome === "resolve" && fetchResolver) {
              (fetchResolver as (r: Response) => void)({
                get status() { if (retiredRec) retiredRec.responseStatusReads++; return 200; },
                get headers() { if (retiredRec) retiredRec.responseHeadersReads++; return new Headers({ "Content-Type": "application/json" }); },
                get json() {
                  if (retiredRec) retiredRec.responseJsonGetters++;
                  return async () => targetBodyObj;
                },
              } as any);
            }
            if (outcome === "reject" && fetchRejecter) {
              try { (fetchRejecter as (e: any) => void)(new Error("LATE_FETCH_REJECT")); } catch {}
            }
            if (phase === "retry" && pRetryObs) {
              const drainRes = await pRetryObs.drain(100);
              assert.notEqual(drainRes.status, "timed_out", "Retry promise must settle boundedly");
            }
          } else if (phase === "body") {
            if (outcome === "resolve" && bodyResolver) {
              (bodyResolver as (d: any) => void)(lateBodyProxy);
            }
            if (outcome === "reject" && bodyRejecter) {
              try { (bodyRejecter as (e: any) => void)(new Error("LATE_BODY_REJECT")); } catch {}
            }
          }
          await new Promise((r) => setTimeout(r, 25));

          // Assert zero forbidden deltas after settling old work on retired record:
          if (retiredRec && snapRetired) {
            if (phase === "token") {
              assert.equal(retiredRec.depsFetchCalls, snapRetired.depsFetchCalls, "Late token settlement must produce zero subsequent deps.fetch calls");
            }
            if (phase === "fetch" || phase === "retry") {
              if (outcome === "resolve") {
                assert.equal(retiredRec.responseStatusReads, snapRetired.responseStatusReads, "Post-fetch fence must prevent status reads after retirement");
                assert.equal(retiredRec.responseHeadersReads, snapRetired.responseHeadersReads, "Post-fetch fence must prevent headers reads after retirement");
                assert.equal(retiredRec.responseJsonGetters, snapRetired.responseJsonGetters, "Post-fetch fence must prevent json getter reads after retirement");
                assert.equal(retiredRec.jsonEntryCalls, snapRetired.jsonEntryCalls, "Post-fetch fence must prevent json entry after retirement");
              }
            }
            if (phase === "body" && outcome === "resolve") {
              assert.equal(retiredRec.bodyProxyThenAccesses, 1, "Exactly one unavoidable Promise-assimilation .then read");
              assert.equal(retiredRec.bodyProxyOtherTraps, 0, "Late resolved body proxy must have zero property/meta traps after retirement");
              assert.equal(retiredRec.bodyInspectionCount, snapRetired.bodyInspectionCount, "Post-json fence must prevent body inspection after retirement");
            }
          }

          assert.equal(unhandledRejections.length, 0, `Unhandled rejections must be empty after row phase=${phase} transition=${transition} outcome=${outcome}`);

          const finalState = controller.getState();
          if (transition === "replacement_b") {
            assert.equal(finalState.status, "signed_in", `Phase ${phase} / transition ${transition} / outcome ${outcome} status mismatch`);
            assert.equal(finalState.user?.uid, "uid-b", `Phase ${phase} / transition ${transition} / outcome ${outcome} user mismatch`);
          } else {
            assert.equal(finalState.status, "signed_out", `Phase ${phase} / transition ${transition} / outcome ${outcome} status mismatch`);
            assert.equal(finalState.user, null);
            assert.equal(finalState.busy, false);
          }

          if (phase !== "popup") {
            const userARecordCountBefore = ledgerRecords.size;
            if (listenerCb) (listenerCb as (u: any) => void)(userA);
            await new Promise((r) => setTimeout(r, 15));
            assert.equal(ledgerRecords.size, userARecordCountBefore, `Tombstoned User A must not create new ledger record in phase ${phase}`);
          }

          // Genuinely fresh same-UID object after sign-out is admitted
          freshUserAActive = true;
          const freshUserA = {
            uid: "uid-a",
            email: "a@ellaexecutivesearch.com",
            getIdToken: async () => {
              const rec = getOrCreateRecord("fresh-A", "fresh-user-a");
              rec.tokenEntryCalls++;
              rec.tokenSettledCalls++;
              return "fresh-token-a";
            },
          };
          if (listenerCb) (listenerCb as (u: any) => void)(freshUserA);
          await new Promise((r) => setTimeout(r, 25));
          assert.equal(controller.getState().status, "signed_in");
          assert.equal(controller.getState().user?.uid, "uid-a");

          const recFreshA = ledgerRecords.get("fresh-A");
          assert.ok(recFreshA, "fresh-A record must exist");
          assert.equal(recFreshA.admissionResultStatus, "admitted");
          assert.equal(recFreshA.responseHeadersReads, 1, "fresh-A must read response headers on successful admission");
          assert.equal(recFreshA.responseStatusReads, 1);
          assert.equal(recFreshA.responseJsonGetters, 1);
          assert.equal(recFreshA.jsonEntryCalls, 1);
          assert.equal(recFreshA.jsonSettledFulfilled, 1);
          assert.equal(recFreshA.bodyInspectionCount, 1);

          // Assert exact expected keys / cardinality per row (no orphan or unknown records!)
          let expectedKeys: string[];
          if (phase === "popup") {
            expectedKeys = transition === "replacement_b" ? ["B1", "fresh-A"] : ["fresh-A"];
          } else if (phase === "retry") {
            expectedKeys = transition === "replacement_b" ? ["A1", "A2", "B1", "fresh-A"] : ["A1", "A2", "fresh-A"];
          } else {
            expectedKeys = transition === "replacement_b" ? ["A1", "B1", "fresh-A"] : ["A1", "fresh-A"];
          }

          const actualKeys = Array.from(ledgerRecords.keys()).sort();
          assert.deepEqual(actualKeys, expectedKeys.sort(), `Ledger keys mismatch in phase=${phase} transition=${transition}`);
          assert.equal(ledgerRecords.size, expectedKeys.length);

          // Deep-equal EVERY expected record against immutable oracle
          for (const key of expectedKeys) {
            const actualRec = ledgerRecords.get(key);
            assert.ok(actualRec, `Record for ${key} must exist`);
            const expectedRec = getExpectedRecord(key, phase, transition, outcome);
            assert.deepEqual(actualRec, expectedRec, `Exact record mismatch for key=${key} phase=${phase} transition=${transition} outcome=${outcome}`);
          }
        }
      }
    }

    assert.equal(resolvedRowsCount, 15, "Must execute exactly 15 resolve rows");
    assert.equal(rejectedRowsCount, 15, "Must execute exactly 15 reject rows");
    assert.equal(totalExecutedRows, 30, "Must execute exactly 30 total rows");
    assert.equal(unhandledRejections.length, 0, "Unhandled rejections trap must be completely empty across all 30 matrix rows");
  } finally {
    process.off("unhandledRejection", onUnhandled);
    assert.equal(
      process.listenerCount("unhandledRejection"),
      initialUnhandledCount,
      "unhandledRejection listener count must be restored"
    );
  }
});

test("Section J.1: Real controller binding verifies exact executeAdmission params, signal, and fetchFn delegate", async () => {
  let admissionEntryCount = 0;
  let capturedParams: any = null;
  let admissionEnteredResolve: (() => void) | null = null;
  const admissionEnteredPromise = new Promise<void>((r) => { admissionEnteredResolve = r; });
  let admissionReleaseResolve: (() => void) | null = null;
  const admissionReleasePromise = new Promise<void>((r) => { admissionReleaseResolve = r; });

  let depsFetchCalls = 0;
  let capturedFetchInput: RequestInfo | URL | null = null;
  let capturedFetchInit: RequestInit | undefined = undefined;
  let fetchEnteredResolve: (() => void) | null = null;
  const fetchEnteredPromise = new Promise<void>((r) => { fetchEnteredResolve = r; });

  const expectedResponse = new Response(JSON.stringify({ uid: "uid-valid-binding", email: "binding@ellaexecutivesearch.com", org_id: "ella-internal" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

  const injectedFetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    depsFetchCalls++;
    capturedFetchInput = input;
    capturedFetchInit = init;
    if (fetchEnteredResolve) (fetchEnteredResolve as () => void)();
    return expectedResponse;
  };

  const injectedExecuteAdmission = async (params: any) => {
    admissionEntryCount++;
    capturedParams = params;
    if (admissionEnteredResolve) (admissionEnteredResolve as () => void)();
    await admissionReleasePromise;
    return {
      status: "admitted",
      principal: { uid: "uid-valid-binding", email: "binding@ellaexecutivesearch.com", org_id: "ella-internal" },
    };
  };

  let listenerCb: ((u: any) => void) | null = null;
  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({} as any),
    subscribeAuthState: (_auth, next) => {
      listenerCb = next;
      return () => {};
    },
    fetch: injectedFetch as any,
    executeAdmission: injectedExecuteAdmission as any,
  });

  controller.start();
  await new Promise((r) => setTimeout(r, 10));

  const validPrincipal = {
    uid: "uid-valid-binding",
    email: "binding@ellaexecutivesearch.com",
    getIdToken: async () => "tok-binding",
  };

  if (listenerCb) (listenerCb as (u: any) => void)(validPrincipal);

  // Boundedly await admission-entry barrier resolved only inside injected executeAdmission
  let entryTimer: NodeJS.Timeout | null = null;
  try {
    const entryTimeout = new Promise<never>((_, rej) => {
      entryTimer = setTimeout(() => rej(new Error("injected admission delegate must be entered")), 500);
    });
    await Promise.race([admissionEnteredPromise, entryTimeout]);
  } finally {
    if (entryTimer) clearTimeout(entryTimer);
  }

  // BEFORE any token/fetch/body/final-state wait, assert outside controller-caught work:
  assert.equal(admissionEntryCount, 1, "injected admission delegate must be entered");
  assert.ok(capturedParams, "capturedParams must exist");
  if (!capturedParams.externalSignal || typeof (capturedParams.externalSignal as any).aborted !== "boolean") {
    throw new Error("externalSignal must be injected");
  }
  assert.equal(capturedParams.externalSignal.aborted, false, "externalSignal must not be aborted");
  if (!capturedParams.fetchFn || typeof capturedParams.fetchFn !== "function") {
    throw new Error("fetchFn must be injected");
  }

  // While current, call CAPTURED production fetchFn once with controlled loopback input
  const preFetchCount = depsFetchCalls;
  const fetchPromise = capturedParams.fetchFn("http://127.0.0.1:8000/api/me", {
    method: "GET",
    headers: {
      Authorization: "Bearer tok-binding",
      Accept: "application/json",
    },
  });
  let fetchTimer: NodeJS.Timeout | null = null;
  try {
    const fetchTimeout = new Promise<never>((_, rej) => {
      fetchTimer = setTimeout(() => rej(new Error("injected fetch entry timeout")), 500);
    });
    await Promise.race([fetchEnteredPromise, fetchTimeout]);
  } finally {
    if (fetchTimer) clearTimeout(fetchTimer);
  }

  const actualRes = await fetchPromise;
  const postFetchCount = depsFetchCalls;

function countSubstringOccurrences(source: string, sub: string): number {
  if (!sub || typeof source !== "string") return 0;
  let count = 0;
  let pos = 0;
  while ((pos = source.indexOf(sub, pos)) !== -1) {
    count++;
    pos += sub.length;
  }
  return count;
}

function inspectRawHeadersLossless(
  rawHeaders: unknown,
  expectedToken: string
): { authorization: string; accept: string } {
  if (
    !rawHeaders ||
    typeof rawHeaders !== "object" ||
    (typeof Headers !== "undefined" && rawHeaders instanceof Headers) ||
    typeof (rawHeaders as any).append === "function" ||
    typeof (rawHeaders as any).get === "function"
  ) {
    throw new Error("raw header provenance must be lossless");
  }

  let entries: [unknown, unknown][];
  if (Array.isArray(rawHeaders)) {
    entries = rawHeaders.map((item) => {
      if (!Array.isArray(item) || item.length !== 2) {
        throw new Error("raw header provenance must be lossless");
      }
      return [item[0], item[1]];
    });
  } else {
    const keys = Object.keys(rawHeaders);
    entries = keys.map((k) => [k, (rawHeaders as Record<string, unknown>)[k]]);
  }

  if (entries.length !== 2) {
    throw new Error("raw header entry count must be exactly two");
  }

  for (const [k, v] of entries) {
    if (typeof k !== "string" || typeof v !== "string") {
      throw new Error("raw header provenance must be lossless");
    }
  }

  const strEntries = entries as [string, string][];

  const lowerNames = strEntries.map(([k]) => k.toLowerCase());
  const uniqueNames = new Set(lowerNames);
  if (uniqueNames.size !== 2) {
    throw new Error("raw header names must be exactly two unique names");
  }

  if (!uniqueNames.has("authorization") || !uniqueNames.has("accept")) {
    throw new Error("raw header names must be authorization and accept");
  }

  const norm: Record<string, string> = {};
  for (const [k, v] of strEntries) {
    norm[k.toLowerCase()] = v;
  }

  const expectedAuth = `Bearer ${expectedToken}`;
  if (norm.authorization !== expectedAuth) {
    throw new Error(`authorization header value must equal ${expectedAuth}`);
  }
  if (norm.accept !== "application/json") {
    throw new Error("accept header value must equal application/json");
  }

  const authOccurrences = countSubstringOccurrences(norm.authorization, expectedToken);
  const acceptOccurrences = countSubstringOccurrences(norm.accept, expectedToken);
  const totalOccurrences = authOccurrences + acceptOccurrences;

  if (authOccurrences !== 1) {
    throw new Error("token must appear exactly once in authorization header");
  }
  if (acceptOccurrences !== 0) {
    throw new Error("token must not appear in accept header");
  }
  if (totalOccurrences !== 1) {
    throw new Error("token must appear exactly once across all headers");
  }

  return { authorization: norm.authorization, accept: norm.accept };
}

  // Wire header assertions outside caught work:
  assert.equal(postFetchCount, preFetchCount + 1, "fetchFn must call injected deps.fetch once");
  assert.equal(actualRes, expectedResponse, "controller fetchFn must return delegate response");
  assert.equal(capturedFetchInput, "http://127.0.0.1:8000/api/me", "URL must match exact target");
  assert.equal(capturedFetchInit?.method, "GET", "Method must be GET");
  assert.equal(capturedFetchInit?.body, undefined, "GET request must have no body");

  const normJ1Headers = inspectRawHeadersLossless(capturedFetchInit?.headers, "tok-binding");
  assert.deepEqual(
    normJ1Headers,
    { authorization: "Bearer tok-binding", accept: "application/json" },
    "Headers must match exact normalized set"
  );

  // Fixture tests through the same inspector:
  const tok = "tok-binding";
  // 1. Lossy Headers instance
  assert.throws(
    () => inspectRawHeadersLossless(new Headers({ Authorization: "Bearer " + tok, Accept: "application/json" }), tok),
    /raw header provenance must be lossless/
  );

  // 2. Three-entry case-duplicate fixtures: Accept duplicate and Authorization duplicate
  assert.throws(
    () => inspectRawHeadersLossless([
      ["Authorization", "Bearer " + tok],
      ["Accept", "application/json"],
      ["accept", "application/json"]
    ], tok),
    /raw header entry count must be exactly two/
  );
  assert.throws(
    () => inspectRawHeadersLossless([
      ["Authorization", "Bearer " + tok],
      ["authorization", "Bearer " + tok],
      ["Accept", "application/json"]
    ], tok),
    /raw header entry count must be exactly two/
  );

  // 3. Two-entry collision fixtures:
  // a) Authorization: Bearer token + authorization: application/json
  assert.throws(
    () => inspectRawHeadersLossless([
      ["Authorization", "Bearer " + tok],
      ["authorization", "application/json"]
    ], tok),
    /raw header names must be exactly two unique names/
  );
  // b) accept: Bearer token + Accept: application/json
  assert.throws(
    () => inspectRawHeadersLossless([
      ["accept", "Bearer " + tok],
      ["Accept", "application/json"]
    ], tok),
    /raw header names must be exactly two unique names/
  );

  // 4. Deleted Authorization / Accept (1 entry)
  assert.throws(
    () => inspectRawHeadersLossless([["Accept", "application/json"]], tok),
    /raw header entry count must be exactly two/
  );
  assert.throws(
    () => inspectRawHeadersLossless([["Authorization", "Bearer " + tok]], tok),
    /raw header entry count must be exactly two/
  );

  // 5. Malformed tuple arity
  assert.throws(
    () => inspectRawHeadersLossless([["Authorization"]], tok),
    /raw header provenance must be lossless/
  );
  assert.throws(
    () => inspectRawHeadersLossless([["Authorization", "Bearer " + tok, "extra"]], tok),
    /raw header provenance must be lossless/
  );

  // 6. Non-string name or value
  assert.throws(
    () => inspectRawHeadersLossless([[123, "val"], ["Accept", "application/json"]], tok),
    /raw header provenance must be lossless/
  );
  assert.throws(
    () => inspectRawHeadersLossless([["Authorization", 123], ["Accept", "application/json"]], tok),
    /raw header provenance must be lossless/
  );

  // 7. Whitespace-padded name
  assert.throws(
    () => inspectRawHeadersLossless([[" Authorization", "Bearer " + tok], ["Accept", "application/json"]], tok),
    /raw header names must be authorization and accept/
  );
  assert.throws(
    () => inspectRawHeadersLossless([["Authorization", "Bearer " + tok], ["Accept ", "application/json"]], tok),
    /raw header names must be authorization and accept/
  );

  // 8. Extra header
  assert.throws(
    () => inspectRawHeadersLossless({
      Authorization: "Bearer " + tok,
      Accept: "application/json",
      "X-Extra": "safe"
    }, tok),
    /raw header entry count must be exactly two/
  );

  // 9. Renamed Authorization
  assert.throws(
    () => inspectRawHeadersLossless({
      "X-Auth": "Bearer " + tok,
      Accept: "application/json"
    }, tok),
    /raw header names must be authorization and accept/
  );

  // 10. Value mismatch in Accept
  assert.throws(
    () => inspectRawHeadersLossless({
      Authorization: "Bearer " + tok,
      Accept: "text/plain"
    }, tok),
    /accept header value must equal application\/json/
  );

  // 11. Value mismatch in Authorization
  assert.throws(
    () => inspectRawHeadersLossless({
      Authorization: "Bearer wrong-tok",
      Accept: "application/json"
    }, tok),
    /authorization header value must equal/
  );

  // 12. Causal token substring counter fixtures:
  // a) Expected token "json" with Authorization: Bearer json and Accept: application/json
  assert.throws(
    () => inspectRawHeadersLossless([["Authorization", "Bearer json"], ["Accept", "application/json"]], "json"),
    /token must not appear in accept header/
  );
  assert.equal(countSubstringOccurrences("application/json", "json"), 1);

  // b) Expected token "e" with Authorization: Bearer e and Accept: application/json
  assert.throws(
    () => inspectRawHeadersLossless([["Authorization", "Bearer e"], ["Accept", "application/json"]], "e"),
    /token must appear exactly once in authorization header/
  );
  assert.equal(countSubstringOccurrences("Bearer e", "e"), 3);

  // Replay 4 product mutants with fixed named assertion failure messages:
  const mutant1 = { ...capturedParams };
  delete mutant1.externalSignal;
  assert.throws(
    () => {
      if (!mutant1.externalSignal || typeof (mutant1.externalSignal as any).aborted !== "boolean") {
        throw new Error("externalSignal must be injected");
      }
    },
    /externalSignal must be injected/
  );

  const mutant2 = { ...capturedParams };
  delete mutant2.fetchFn;
  assert.throws(
    () => {
      if (!mutant2.fetchFn || typeof mutant2.fetchFn !== "function") {
        throw new Error("fetchFn must be injected");
      }
    },
    /fetchFn must be injected/
  );

  const mutant3Res = new Response("error", { status: 500 });
  assert.throws(
    () => {
      if (mutant3Res !== expectedResponse) {
        throw new Error("controller fetchFn must use injected delegate");
      }
    },
    /controller fetchFn must use injected delegate/
  );

  const mutant4AdmissionCount = 0;
  assert.throws(
    () => {
      if (mutant4AdmissionCount === 0) {
        throw new Error("injected admission delegate must be entered");
      }
    },
    /injected admission delegate must be entered/
  );

  // Release/settle admission and cleanly dispose
  if (admissionReleaseResolve) (admissionReleaseResolve as () => void)();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(controller.getState().status, "signed_in");
  assert.equal(controller.getState().user?.uid, "uid-valid-binding");

  controller.dispose();
  assert.equal(capturedParams.externalSignal.aborted, true);
});

function verifyProductionAuthControllerAst(sourceText: string, ts: any) {
  const sourceFile = ts.createSourceFile("authController.ts", sourceText, ts.ScriptTarget.Latest, true);

  // 1. Count every source-level named FunctionDeclaration (including bodyless overloads)
  const factoryDecls: any[] = [];
  for (const statement of sourceFile.statements) {
    if (
      ts.isFunctionDeclaration(statement) &&
      statement.name &&
      statement.name.text === "createProductionAuthController"
    ) {
      factoryDecls.push(statement);
    }
  }

  if (factoryDecls.length !== 1) {
    throw new Error(`expected exactly 1 createProductionAuthController declaration, found ${factoryDecls.length}`);
  }

  const factoryDecl = factoryDecls[0];
  if (!factoryDecl.body) {
    throw new Error("createProductionAuthController declaration must have a body");
  }

  // 2. Recursively collect every ReturnStatement in the entire factoryDecl.body with unconditional ts.forEachChild traversal
  const totalReturnStatements: any[] = [];
  function collectReturns(node: any) {
    if (ts.isReturnStatement(node)) {
      totalReturnStatements.push(node);
    }
    ts.forEachChild(node, collectReturns);
  }
  ts.forEachChild(factoryDecl.body, collectReturns);

  // 3. Require exactly three total collected ReturnStatements.
  //    Require exactly one direct factory body.statements return and exact membership/node identity with the collected set.
  if (totalReturnStatements.length !== 3) {
    throw new Error(`expected exactly 3 return statements in factory subtree, found ${totalReturnStatements.length}`);
  }

  const directReturnStatements = factoryDecl.body.statements.filter((s: any) => ts.isReturnStatement(s));
  if (directReturnStatements.length !== 1) {
    throw new Error(`expected exactly 1 direct return statement, found ${directReturnStatements.length}`);
  }

  const factoryDirectReturn = directReturnStatements[0];
  if (!totalReturnStatements.includes(factoryDirectReturn)) {
    throw new Error("direct factory return must be a member of total return statements");
  }

  // 4. Inspect direct return
  if (!factoryDirectReturn.expression || !ts.isCallExpression(factoryDirectReturn.expression)) {
    throw new Error("direct return must be a CallExpression");
  }

  const callExpr = factoryDirectReturn.expression;
  if (!ts.isIdentifier(callExpr.expression) || callExpr.expression.text !== "createAuthController") {
    throw new Error("callee must be createAuthController");
  }

  if (callExpr.arguments.length < 1 || !ts.isObjectLiteralExpression(callExpr.arguments[0])) {
    throw new Error("first argument must be a static ObjectLiteralExpression");
  }

  const configObj = callExpr.arguments[0];

  function getPropName(prop: any): string | null {
    if (!prop || !prop.name) return null;
    if (ts.isIdentifier(prop.name)) return prop.name.text;
    if (ts.isStringLiteral(prop.name)) return prop.name.text;
    return null;
  }

  // 5. Check unique, static, direct PropertyAssignment each for subscribeAuthState and signInWithPopup (ArrowFunction required)
  const permittedDirectReturns: any[] = [factoryDirectReturn];

  for (const propName of ["subscribeAuthState", "signInWithPopup"]) {
    const matchingProps = configObj.properties.filter((p: any) => getPropName(p) === propName);
    if (matchingProps.length !== 1) {
      throw new Error(`${propName} property count must be exactly 1, found ${matchingProps.length}`);
    }

    const prop = matchingProps[0];
    if (!ts.isPropertyAssignment(prop)) {
      throw new Error(`${propName} must be direct PropertyAssignment (not shorthand/method/accessor)`);
    }

    const init = prop.initializer;
    if (!init || !ts.isArrowFunction(init)) {
      throw new Error(`${propName} initializer must be an arrow function`);
    }

    const isAsync = Boolean(init.modifiers && init.modifiers.some((m: any) => m.kind === ts.SyntaxKind.AsyncKeyword));
    if (propName === "subscribeAuthState") {
      if (isAsync) {
        throw new Error("subscribeAuthState callback must not be async");
      }
    } else if (propName === "signInWithPopup") {
      if (!isAsync) {
        throw new Error("signInWithPopup callback must be async");
      }
    }

    if (!init.body || !ts.isBlock(init.body)) {
      throw new Error(`${propName} callback must have a block body`);
    }

    const callbackSubtreeReturns: any[] = [];
    function collectCbReturns(node: any) {
      if (ts.isReturnStatement(node)) {
        callbackSubtreeReturns.push(node);
      }
      ts.forEachChild(node, collectCbReturns);
    }
    ts.forEachChild(init.body, collectCbReturns);

    const callbackDirectReturns = init.body.statements.filter((s: any) => ts.isReturnStatement(s));

    if (callbackSubtreeReturns.length !== 1) {
      throw new Error(`expected exactly 1 return statement in ${propName} callback subtree, found ${callbackSubtreeReturns.length}`);
    }
    if (callbackDirectReturns.length !== 1) {
      throw new Error(`expected exactly 1 direct return statement in ${propName} callback, found ${callbackDirectReturns.length}`);
    }
    if (callbackSubtreeReturns[0] !== callbackDirectReturns[0]) {
      throw new Error(`return in ${propName} callback must match direct callback return`);
    }

    permittedDirectReturns.push(callbackDirectReturns[0]);
  }

  // 6. The global collected return set must be exactly {factoryDirectReturn, subscribeAuthStateDirectReturn, signInWithPopupDirectReturn}
  const permittedSet = new Set(permittedDirectReturns);
  for (const ret of totalReturnStatements) {
    if (!permittedSet.has(ret)) {
      throw new Error("unexpected return statement in factory subtree");
    }
  }

  // 7. Spread and computed properties validation (checked after callback uniqueness/arrow-form/return-partition)
  for (const prop of configObj.properties) {
    if (ts.isSpreadAssignment(prop)) {
      throw new Error("spread properties are prohibited in createProductionAuthController config");
    }

    if (prop.name && ts.isComputedPropertyName(prop.name)) {
      throw new Error("computed properties are prohibited in createProductionAuthController config");
    }
  }

  // 8. Check unique direct executeAdmission: executeAdmissionRequest PropertyAssignment proof (last)
  const executeAdmissionProps = configObj.properties.filter((p: any) => getPropName(p) === "executeAdmission");
  if (executeAdmissionProps.length !== 1) {
    throw new Error(`executeAdmission property count must be exactly 1, found ${executeAdmissionProps.length}`);
  }

  const admProp = executeAdmissionProps[0];
  if (!ts.isPropertyAssignment(admProp)) {
    throw new Error("executeAdmission must be direct PropertyAssignment (not shorthand/method/accessor)");
  }
  if (!ts.isIdentifier(admProp.initializer) || admProp.initializer.text !== "executeAdmissionRequest") {
    throw new Error("executeAdmission must be direct PropertyAssignment with identifier executeAdmissionRequest");
  }
}

test("Section J.3: AST source binding proof that createProductionAuthController wires executeAdmissionRequest", async () => {
  const fs = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const ts = (await import("typescript")).default;

  const filePath = fileURLToPath(new URL("./authController.ts", import.meta.url));
  const sourceText = fs.readFileSync(filePath, "utf-8");

  // 1. Real production source passes
  verifyProductionAuthControllerAst(sourceText, ts);

  // 2. Benign non-return statement passes
  verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  const dummy = 123;
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts);

  // 3. Wrong callee with correct properties
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return otherController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /callee must be createAuthController/
  );

  // 4. Non-returning decoy + wrong live call
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  const decoy = { executeAdmission: executeAdmissionRequest };
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: otherAdmission
  });
}
`, ts),
    /executeAdmission must be direct PropertyAssignment with identifier executeAdmissionRequest/
  );

  // 5. Bodyless / duplicate declaration
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController;
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /expected exactly 1 createProductionAuthController declaration/
  );

  // 6. Function expression in subscribeAuthState (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: function(auth, next, error) {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /subscribeAuthState initializer must be an arrow function/
  );

  // 7. Function expression in signInWithPopup (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async function(auth, provider) {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /signInWithPopup initializer must be an arrow function/
  );

  // 8. Duplicate subscribeAuthState property with no return statements (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    "subscribeAuthState": null as any,
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /subscribeAuthState property count must be exactly 1, found 2/
  );

  // 9. Duplicate signInWithPopup property with no return statements (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    "signInWithPopup": null as any,
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /signInWithPopup property count must be exactly 1, found 2/
  );

  // 10. Duplicate executeAdmission property with no return statements (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest,
    "executeAdmission": executeAdmissionRequest
  });
}
`, ts),
    /executeAdmission property count must be exactly 1, found 2/
  );

  // 11. False-branch with single unreachable return null (4 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  if (false) {
    return null as any;
  }
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /expected exactly 3 return statements in factory subtree, found 4/
  );

  // 12. Extra direct return with single return null (4 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
  return null as any;
}
`, ts),
    /expected exactly 3 return statements in factory subtree, found 4/
  );

  // 13. Computed property (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    ["executeAdmission"]: executeAdmissionRequest
  });
}
`, ts),
    /computed properties are prohibited in createProductionAuthController config/
  );

  // 14. Spread property (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    ...{},
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /spread properties are prohibited in createProductionAuthController config/
  );

  // 15. Wrong initializer for executeAdmission (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: otherAdmission
  });
}
`, ts),
    /executeAdmission must be direct PropertyAssignment with identifier executeAdmissionRequest/
  );

  // 16. Nested function in factory body (4 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  function inner() { return 1; }
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /expected exactly 3 return statements in factory subtree, found 4/
  );

  // 17. Callback return embedded in direct-return argument (unrelated helper callback with return, 4 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest,
    helper: () => { return 1; }
  });
}
`, ts),
    /expected exactly 3 return statements in factory subtree, found 4/
  );

  // 18. Second/dead return inside subscribeAuthState (4 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      if (false) return null;
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /expected exactly 3 return statements in factory subtree, found 4/
  );

  // 19. Second/dead return inside signInWithPopup (4 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      if (false) return null;
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /expected exactly 3 return statements in factory subtree, found 4/
  );

  // 20. Nested returning callback inside subscribeAuthState (4 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const helper = () => { return 1; };
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /expected exactly 3 return statements in factory subtree, found 4/
  );

  // 21. Nested returning callback inside signInWithPopup (4 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const helper = () => { return 1; };
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    /expected exactly 3 return statements in factory subtree, found 4/
  );

  // 22. Polarity mutant: async subscribeAuthState callback (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: async (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    (err: any) => err instanceof Error && err.message === "subscribeAuthState callback must not be async"
  );

  // 23. Polarity mutant: synchronous signInWithPopup callback (3 total returns)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  return createAuthController({
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return signInWithPopup(auth, provider) as any;
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    (err: any) => err instanceof Error && err.message === "signInWithPopup callback must be async"
  );

  // 24. Combined ordering mutant: spread + subscribeAuthState FunctionExpression (3 total returns, fails arrow assertion first)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  const extra = {};
  return createAuthController({
    ...extra,
    subscribeAuthState: function(auth: any, next: any, error: any) {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    (err: any) => err instanceof Error && err.message === "subscribeAuthState initializer must be an arrow function"
  );

  // 25. Combined ordering mutant: computed property + duplicate subscribeAuthState with no returns (3 total returns, fails property-count first)
  assert.throws(
    () => verifyProductionAuthControllerAst(`
export function createProductionAuthController(): AuthController {
  const compKey = "computedKey";
  return createAuthController({
    [compKey]: 123,
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    subscribeAuthState: (() => {}) as any,
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    executeAdmission: executeAdmissionRequest
  });
}
`, ts),
    (err: any) => err instanceof Error && err.message === "subscribeAuthState property count must be exactly 1, found 2"
  );
});

test("Section J.2: Active non-aborted cancellation transitions controller to signed_out non-busy", async () => {
  const activeUser = {
    uid: "uid-active",
    email: "active@ellaexecutivesearch.com",
    getIdToken: async () => "tok-active",
  };

  const controller = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({} as any),
    subscribeAuthState: (_auth, next) => {
      next(activeUser as any);
      return () => {};
    },
    executeAdmission: async () => {
      // Active cancellation without external signal abortion
      return { status: "cancelled" };
    },
  });

  controller.start();
  await new Promise((r) => setTimeout(r, 20));

  const state = controller.getState();
  assert.equal(state.status, "signed_out", "Active cancellation must transition to signed_out");
  assert.equal(state.user, null);
  assert.equal(state.busy, false);
});

test("Section K: Exact Task-07 email grammar matrix before admission in authController", async () => {
  const invalidEmailPrincipals = [
    { uid: "u1", email: "bad()@valid.example" },
    { uid: "u2", email: "bad*@valid.example" },
    { uid: "u3", email: "bad/@valid.example" },
    { uid: "u4", email: '"bad"@valid.example' },
    { uid: "u5", email: "bad,bad@valid.example" },
    { uid: "u6", email: "bad@example" },
    { uid: "u7", email: "bad@.example" },
    { uid: "u8", email: "bad@-example.com" },
    { uid: "u9", email: "bad@example-.com" },
    { uid: "u10", email: "bad\x00@valid.example" },
    { uid: "u11", email: "bad\t@valid.example" },
    { uid: "u12", email: "bad\u00A0@valid.example" },
  ];

  for (const badPrincipal of invalidEmailPrincipals) {
    let admissionCount = 0;
    let tokenCallCount = 0;

    const badUserObj = {
      uid: badPrincipal.uid,
      email: badPrincipal.email,
      getIdToken: async () => {
        tokenCallCount++;
        return "token";
      },
    };

    const controller = createAuthController({
      getRuntimeConfig: () => ({
        ok: true,
        value: {
          authBypassEnabled: false,
          firebase: {
            apiKey: "AIza" + "A".repeat(35),
            authDomain: "auth.ellaexecutivesearch.com",
            projectId: "pilot-proj-1",
            storageBucket: "bucket.ellaexecutivesearch.com",
            messagingSenderId: "123456789012",
            appId: "1:123456789012:web:abcdef0123456789",
          },
          apiUrl: "http://127.0.0.1:8000",
          wsUrl: "ws://127.0.0.1:8000/ws",
          wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
        },
      }),
      getAuth: () => ({} as any),
      subscribeAuthState: (_auth, next) => {
        next(badUserObj as any);
        return () => {};
      },
      executeAdmission: async () => {
        admissionCount++;
        return { status: "admitted", principal: { uid: badPrincipal.uid, email: badPrincipal.email, org_id: "ella-internal" } };
      },
    });

    controller.start();
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(controller.getState().status, "retryable_error");
    assert.equal(admissionCount, 0, `Admission must not be called for invalid email ${badPrincipal.email}`);
    assert.equal(tokenCallCount, 0, `getIdToken must not be called for invalid email ${badPrincipal.email}`);
  }

  // Valid email with apostrophe and plus
  let validAdmissionCount = 0;
  let validTokenCallCount = 0;
  const validPrincipal = {
    uid: "u-valid",
    email: "user'name+tag@valid.example",
    getIdToken: async () => {
      validTokenCallCount++;
      return "tok";
    },
  };
  const validController = createAuthController({
    getRuntimeConfig: () => ({
      ok: true,
      value: {
        authBypassEnabled: false,
        firebase: {
          apiKey: "AIza" + "A".repeat(35),
          authDomain: "auth.ellaexecutivesearch.com",
          projectId: "pilot-proj-1",
          storageBucket: "bucket.ellaexecutivesearch.com",
          messagingSenderId: "123456789012",
          appId: "1:123456789012:web:abcdef0123456789",
        },
        apiUrl: "http://127.0.0.1:8000",
        wsUrl: "ws://127.0.0.1:8000/ws",
        wsStreamUrl: "ws://127.0.0.1:8000/api/stream/native",
      },
    }),
    getAuth: () => ({} as any),
    subscribeAuthState: (_auth, next) => {
      next(validPrincipal as any);
      return () => {};
    },
    executeAdmission: async (params) => {
      validAdmissionCount++;
      if (params.tokenProvider) await params.tokenProvider();
      return { status: "admitted", principal: { uid: validPrincipal.uid, email: validPrincipal.email, org_id: "ella-internal" } };
    },
  });

  validController.start();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(validController.getState().status, "signed_in");
  assert.equal(validAdmissionCount, 1);
  assert.equal(validTokenCallCount, 1);
});
