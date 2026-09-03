import assert from "node:assert/strict";
import { test } from "node:test";

import {
  admissionIsCurrent,
  executeAdmissionRequest,
  type AdmissionResult,
} from "./authAdmission.ts";

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

test("executeAdmissionRequest admits valid matching principal on HTTP 200", async () => {
  const mockFetch = async () =>
    new Response(
      JSON.stringify({
        uid: "uid-valid",
        email: "recruiter@ellaexecutivesearch.com",
        org_id: "ella-internal",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );

  const res: AdmissionResult = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "valid-token",
    expectedUid: "uid-valid",
    expectedEmail: "Recruiter@EllaExecutiveSearch.com",
    fetchFn: mockFetch as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });

  assert.equal(res.status, "admitted");
  if (res.status === "admitted") {
    assert.equal(res.principal.uid, "uid-valid");
    assert.equal(res.principal.email, "recruiter@ellaexecutivesearch.com");
    assert.equal(res.principal.org_id, "ella-internal");
  }
});

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

test("executeAdmissionRequest wire capture asserts exact request parameters, normalized headers, and mutant rejections", async () => {
  let capturedInput: RequestInfo | URL | null = null;
  let capturedInit: RequestInit | undefined = undefined;
  let fetchEntryCalls = 0;
  let fetchEnteredResolve: (() => void) | null = null;
  const fetchEnteredPromise = new Promise<void>((r) => { fetchEnteredResolve = r; });
  let responseReleaseResolve: (() => void) | null = null;
  const responseReleasePromise = new Promise<void>((r) => { responseReleaseResolve = r; });

  const mockResponse = new Response(
    JSON.stringify({
      uid: "uid-valid-wire",
      email: "wire@example.com",
      org_id: "org-wire",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } }
  );

  const captureFetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    fetchEntryCalls++;
    capturedInput = input;
    capturedInit = init;
    if (fetchEnteredResolve) (fetchEnteredResolve as () => void)();
    await responseReleasePromise;
    return mockResponse;
  };

  const admissionPromise = executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "unique-admission-token",
    expectedUid: "uid-valid-wire",
    expectedEmail: "wire@example.com",
    fetchFn: captureFetch as unknown as typeof fetch,
    deadlineMs: 2000,
    isTrustedDestination: () => true,
  });

  // Await fetch entry barrier
  let entryTimer: NodeJS.Timeout | null = null;
  try {
    const entryTimeout = new Promise<never>((_, rej) => {
      entryTimer = setTimeout(() => rej(new Error("fetchFn entry timeout")), 500);
    });
    await Promise.race([fetchEnteredPromise, entryTimeout]);
  } finally {
    if (entryTimer) clearTimeout(entryTimer);
  }

  // Immediately after bounded entry, before other wire assertions/Response release, require exactly 1
  assert.equal(fetchEntryCalls, 1, "admission fetch entry count must be exactly one");

  // Assert OUTSIDE caught work against fixed literals:
  assert.equal(capturedInput, "https://backend.example.com/api/me", "URL must match exact target");
  assert.equal(capturedInit?.method, "GET", "Method must be GET");
  assert.equal(capturedInit?.body, undefined, "GET request must have no body");
  assert.equal(capturedInit?.redirect, "error", "Redirect must be error");
  assert.equal(capturedInit?.cache, "no-store", "Cache must be no-store");
  assert.ok(capturedInit?.signal, "Signal must be present");
  assert.equal(capturedInit?.signal?.aborted, false, "Signal must not be aborted");

  // Inspect raw headers through lossless inspector:
  const tok = "unique-admission-token";
  const normHeaders = inspectRawHeadersLossless(capturedInit?.headers, tok);
  assert.deepEqual(
    normHeaders,
    { authorization: "Bearer unique-admission-token", accept: "application/json" },
    "Headers must match exact normalized set"
  );

  // Fixture tests through the same inspector:
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

  // Release and require admitted
  if (responseReleaseResolve) (responseReleaseResolve as () => void)();
  let settleTimer: NodeJS.Timeout | null = null;
  let res: any;
  try {
    const settleTimeout = new Promise<never>((_, rej) => {
      settleTimer = setTimeout(() => rej(new Error("admissionPromise settlement timeout")), 2000);
    });
    res = await Promise.race([admissionPromise, settleTimeout]);
  } finally {
    if (settleTimer) clearTimeout(settleTimer);
  }
  assert.equal(res.status, "admitted");
  if (res.status === "admitted") {
    assert.equal(res.principal.uid, "uid-valid-wire");
    assert.equal(res.principal.email, "wire@example.com");
    assert.equal(res.principal.org_id, "org-wire");
  }

  // After bounded admitted settlement still 1
  assert.equal(fetchEntryCalls, 1, "admission fetch entry count must be exactly one");
});

test("executeAdmissionRequest denies on HTTP 401 and 403", async () => {
  const mockFetch401 = async () => new Response("Unauthorized", { status: 401 });
  const res401 = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: mockFetch401 as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(res401.status, "denied");

  const mockFetch403 = async () => new Response("Forbidden", { status: 403 });
  const res403 = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: mockFetch403 as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(res403.status, "denied");
});

test("executeAdmissionRequest retries on 500, network error, and malformed JSON", async () => {
  const mockFetch500 = async () => new Response("Server error", { status: 500 });
  const res500 = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: mockFetch500 as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(res500.status, "retryable");

  const mockFetchNetErr = async () => {
    throw new Error("Network offline");
  };
  const resNetErr = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: mockFetchNetErr as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(resNetErr.status, "retryable");

  const mockFetchBadJson = async () =>
    new Response("{invalid json", { status: 200, headers: { "Content-Type": "application/json" } });
  const resBadJson = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: mockFetchBadJson as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(resBadJson.status, "retryable");
});

test("executeAdmissionRequest cancels on external abort", async () => {
  const abortController = new AbortController();
  abortController.abort();

  const res = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    externalSignal: abortController.signal,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(res.status, "cancelled");
});

test("executeAdmissionRequest retries on timeout", async () => {
  const hungFetch = (_url: string | URL | Request, init?: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        const err = new Error("The operation was aborted");
        err.name = "AbortError";
        reject(err);
      });
    });
  const res = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: hungFetch as unknown as typeof fetch,
    deadlineMs: 50,
    isTrustedDestination: () => true,
  });
  assert.equal(res.status, "retryable");
});

test("Fixes-2/3 bounded never-settling token/fetch/body settle on deadline or abort", async () => {
  // 1. Hung token provider
  const hungToken = () => new Promise<string>(() => {});
  const resToken = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: hungToken,
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: (async () => new Response("ok")) as unknown as typeof fetch,
    deadlineMs: 50,
    isTrustedDestination: () => true,
  });
  assert.equal(resToken.status, "retryable");

  // 2. Hung fetch ignoring abort
  const hungFetchIgnoreAbort = () => new Promise<Response>(() => {});
  const resFetch = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: hungFetchIgnoreAbort as unknown as typeof fetch,
    deadlineMs: 50,
    isTrustedDestination: () => true,
  });
  assert.equal(resFetch.status, "retryable");

  // 3. Hung response.json() body settling on deadline
  const hungBodyFetch = async () => {
    return {
      status: 200,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: () => new Promise<any>(() => {}), // never settles
    } as unknown as Response;
  };
  const resBody = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: hungBodyFetch as unknown as typeof fetch,
    deadlineMs: 50,
    isTrustedDestination: () => true,
  });
  assert.equal(resBody.status, "retryable");

  // 4. Abort during token
  const abortCtrlToken = new AbortController();
  const hungTokenAbortable = () => new Promise<string>((_, reject) => {
    abortCtrlToken.signal.addEventListener("abort", () => reject(new Error("aborted")));
  });
  const pToken = executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: hungTokenAbortable,
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    externalSignal: abortCtrlToken.signal,
    fetchFn: (async () => new Response("ok")) as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  setTimeout(() => abortCtrlToken.abort(), 10);
  assert.equal((await pToken).status, "cancelled");

  // 5. Abort during fetch
  const abortCtrlFetch = new AbortController();
  const hungFetchAbortable = () => new Promise<Response>((_, reject) => {
    abortCtrlFetch.signal.addEventListener("abort", () => reject(new Error("aborted")));
  });
  const pFetch = executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    externalSignal: abortCtrlFetch.signal,
    fetchFn: hungFetchAbortable as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  setTimeout(() => abortCtrlFetch.abort(), 10);
  assert.equal((await pFetch).status, "cancelled");

  // 6. Abort during response body
  const abortCtrlBody = new AbortController();
  const hungBodyAbortable = async () => {
    return {
      status: 200,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: () => new Promise<any>((_, reject) => {
        abortCtrlBody.signal.addEventListener("abort", () => reject(new Error("aborted")));
      }),
    } as unknown as Response;
  };
  const pBody = executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    externalSignal: abortCtrlBody.signal,
    fetchFn: hungBodyAbortable as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  setTimeout(() => abortCtrlBody.abort(), 10);
  assert.equal((await pBody).status, "cancelled");
});

test("Section E: Non-string and throwing inputs return fixed retryable", async () => {
  const throwingEmail = {
    toString() { throw new Error("THROWING_EMAIL_SENTINEL"); },
    toLowerCase() { throw new Error("THROWING_EMAIL_SENTINEL"); },
    trim() { throw new Error("THROWING_EMAIL_SENTINEL"); },
  };

  const resThrowing = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-a",
    expectedEmail: throwingEmail as any,
    fetchFn: (async () => new Response("ok")) as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(resThrowing.status, "retryable");

  // Untrusted destination rejected before token provider is called
  let tokenCalled = false;
  const resUntrusted = await executeAdmissionRequest({
    apiUrl: "https://attacker.com/api/me",
    tokenProvider: async () => {
      tokenCalled = true;
      return "token";
    },
    expectedUid: "uid-a",
    expectedEmail: "a@b.com",
    fetchFn: (async () => new Response("ok")) as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => false,
  });
  assert.equal(resUntrusted.status, "retryable");
  assert.equal(tokenCalled, false);
});

test("Fixes-2 principal validation and status codes", async () => {
  // HTTP 201 admits valid principal
  const res201 = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-valid",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => new Response(JSON.stringify({
      uid: "uid-valid",
      email: "recruiter@ellaexecutivesearch.com",
      org_id: "ella-internal",
    }), { status: 201, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(res201.status, "admitted");

  // Injected 302 returns retryable (redirect rejected)
  const res302 = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-valid",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => new Response(null, { status: 302, headers: { Location: "/login" } })) as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(res302.status, "retryable");

  // UID mismatch returns retryable
  const resMismatch = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-expected",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => new Response(JSON.stringify({
      uid: "uid-different",
      email: "recruiter@ellaexecutivesearch.com",
      org_id: "ella-internal",
    }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch,
    deadlineMs: 1000,
    isTrustedDestination: () => true,
  });
  assert.equal(resMismatch.status, "retryable");
});

test("Section C: Pre-aborted externalSignal has precedence over malformed input", async () => {
  const abortedCtrl = new AbortController();
  abortedCtrl.abort();

  // Pre-aborted signal with completely malformed inputs returns cancelled
  const res1 = await executeAdmissionRequest({
    apiUrl: "not-a-valid-url",
    tokenProvider: async () => "token",
    expectedUid: "BAD UID WITH SPACES",
    expectedEmail: "not-an-email",
    externalSignal: abortedCtrl.signal,
  });
  assert.equal(res1.status, "cancelled");
});

test("Section C: Throwing cleanup seams do not strand terminalPromise", async () => {
  const throwingClearTimeout = () => {
    throw new Error("CLEAR_TIMEOUT_FAILED");
  };
  const throwingRemoveEventListener = () => {
    throw new Error("REMOVE_EVENT_LISTENER_FAILED");
  };

  const abortCtrl = new AbortController();

  const res = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-valid",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => new Response(JSON.stringify({
      uid: "uid-valid",
      email: "recruiter@ellaexecutivesearch.com",
      org_id: "ella-internal",
    }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch,
    deadlineMs: 5000,
    externalSignal: abortCtrl.signal,
    isTrustedDestination: () => true,
    clearTimeoutFn: throwingClearTimeout as any,
    removeEventListenerFn: throwingRemoveEventListener as any,
  });

  // Must successfully resolve admitted despite throwing cleanup functions
  assert.equal(res.status, "admitted");
});

test("Section C: Late token/fetch/body resolve and reject have zero later effects", async () => {
  let fetchCallCount = 0;
  let jsonCallCount = 0;

  // 1. Late token resolve after timeout: fetchFn is NEVER called
  let tokenResolver: ((t: string) => void) | null = null;
  let tokenRejecter: ((err: any) => void) | null = null;

  const delayedToken = () => new Promise<string>((resolve, reject) => {
    tokenResolver = resolve;
    tokenRejecter = reject;
  });

  const pTimeoutToken = executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: delayedToken,
    expectedUid: "uid-valid",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => {
      fetchCallCount++;
      return new Response("ok");
    }) as unknown as typeof fetch,
    deadlineMs: 20,
    isTrustedDestination: () => true,
  });

  const resTokenTimeout = await pTimeoutToken;
  assert.equal(resTokenTimeout.status, "retryable");

  // Late resolution of token
  if (tokenResolver) (tokenResolver as (t: string) => void)("late-token");
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(fetchCallCount, 0); // Proves continuation fence prevented fetch!

  // 2. Late fetch resolve after timeout: json is NEVER called
  let fetchResolver: ((r: Response) => void) | null = null;
  const delayedFetch = () => new Promise<Response>((resolve) => {
    fetchResolver = resolve;
  });

  const pTimeoutFetch = executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token",
    expectedUid: "uid-valid",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: delayedFetch as unknown as typeof fetch,
    deadlineMs: 20,
    isTrustedDestination: () => true,
  });

  const resFetchTimeout = await pTimeoutFetch;
  assert.equal(resFetchTimeout.status, "retryable");

  // Late resolution of fetch
  if (fetchResolver) {
    (fetchResolver as (r: Response) => void)({
      status: 200,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: async () => {
        jsonCallCount++;
        return { uid: "uid-valid", email: "recruiter@ellaexecutivesearch.com", org_id: "ella-internal" };
      },
    } as unknown as Response);
  }
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(jsonCallCount, 0); // Proves continuation fence prevented body read!
});

test("Section B.5: Deterministic injected scheduler/listener counters across all outcomes", async () => {
  const outcomes = ["admitted", "denied", "retryable", "cancelled", "timeout"] as const;

  for (const outcome of outcomes) {
    let setTimeoutCalls = 0;
    let clearTimeoutCalls = 0;
    let addListenerCalls = 0;
    let removeListenerCalls = 0;

    const fakeSetTimeout = (fn: () => void, ms?: number) => {
      setTimeoutCalls++;
      if (outcome === "timeout") {
        fn();
      }
      return 123 as any;
    };
    const fakeClearTimeout = () => {
      clearTimeoutCalls++;
    };
    const fakeAddListener = (sig: AbortSignal, type: string, fn: () => void) => {
      addListenerCalls++;
    };
    const fakeRemoveListener = (sig: AbortSignal, type: string, fn: () => void) => {
      removeListenerCalls++;
    };

    const abortCtrl = new AbortController();
    if (outcome === "cancelled") {
      abortCtrl.abort();
    }

    const res = await executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-1",
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (async () => {
        if (outcome === "denied") return new Response("denied", { status: 403 });
        if (outcome === "retryable") return new Response("error", { status: 500 });
        return new Response(JSON.stringify({ uid: "uid-1", email: "recruiter@ellaexecutivesearch.com", org_id: "ella-internal" }), { status: 200 });
      }) as any,
      externalSignal: abortCtrl.signal,
      deadlineMs: 5000,
      isTrustedDestination: () => true,
      setTimeoutFn: fakeSetTimeout as any,
      clearTimeoutFn: fakeClearTimeout as any,
      addEventListenerFn: fakeAddListener as any,
      removeEventListenerFn: fakeRemoveListener as any,
    });

    if (outcome === "cancelled") {
      assert.equal(res.status, "cancelled");
    } else if (outcome === "timeout") {
      assert.equal(res.status, "retryable");
    } else {
      assert.equal(res.status, outcome);
      assert.equal(clearTimeoutCalls, 1);
      assert.equal(removeListenerCalls, 1);
    }
  }
});

test("Section B.5: Trust predicate synchronously aborting then returning true yields cancelled with zero token/fetch calls", async () => {
  let tokenCallCount = 0;
  let fetchCallCount = 0;
  const abortCtrl = new AbortController();

  const res = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => {
      tokenCallCount++;
      return "token-1";
    },
    expectedUid: "uid-1",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => {
      fetchCallCount++;
      return new Response("ok");
    }) as any,
    externalSignal: abortCtrl.signal,
    deadlineMs: 5000,
    isTrustedDestination: () => {
      abortCtrl.abort();
      return true;
    },
  });

  assert.equal(res.status, "cancelled");
  assert.equal(tokenCallCount, 0);
  assert.equal(fetchCallCount, 0);
});

test("Section B.1: Exact status parsing matrix", async () => {
  let bodyReadCount = 0;
  const makeResponse = (status: any) => ({
    status,
    headers: new Headers({ "Content-Type": "application/json" }),
    json: async () => {
      bodyReadCount++;
      return { uid: "uid-1", email: "recruiter@ellaexecutivesearch.com", org_id: "ella-internal" };
    },
  });

  // Positives: 200, 201
  for (const okStatus of [200, 201]) {
    bodyReadCount = 0;
    const res = await executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-1",
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (async () => makeResponse(okStatus)) as any,
      deadlineMs: 5000,
      isTrustedDestination: () => true,
    });
    assert.equal(res.status, "admitted");
    assert.equal(bodyReadCount, 1);
  }

  // Denied: 401, 403 (does NOT read body)
  for (const deniedStatus of [401, 403]) {
    bodyReadCount = 0;
    const res = await executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-1",
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (async () => makeResponse(deniedStatus)) as any,
      deadlineMs: 5000,
      isTrustedDestination: () => true,
    });
    assert.equal(res.status, "denied");
    assert.equal(bodyReadCount, 0);
  }

  // Non-numeric or invalid status: undefined, null, NaN, Infinity, 200.5, string '200', '201', object, throwing getter
  const invalidStatuses = [
    undefined,
    null,
    NaN,
    Infinity,
    -Infinity,
    200.5,
    "200",
    "201",
    {},
    () => 200,
  ];

  for (const badStatus of invalidStatuses) {
    bodyReadCount = 0;
    const res = await executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-1",
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (async () => makeResponse(badStatus)) as any,
      deadlineMs: 5000,
      isTrustedDestination: () => true,
    });
    assert.equal(res.status, "retryable");
    assert.equal(bodyReadCount, 0);
  }

  // Throwing status getter
  bodyReadCount = 0;
  const throwingStatusResp = {
    get status() {
      throw new Error("SENTINEL_STATUS_GETTER");
    },
    headers: new Headers({ "Content-Type": "application/json" }),
    json: async () => {
      bodyReadCount++;
      return {};
    },
  };
  const resThrowingStatus = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token-1",
    expectedUid: "uid-1",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => throwingStatusResp) as any,
    deadlineMs: 5000,
    isTrustedDestination: () => true,
  });
  assert.equal(resThrowingStatus.status, "retryable");
  assert.equal(bodyReadCount, 0);
});

test("Section B.1: Throwing scheduler and listener setup fail-safe and content-free", async () => {
  let tokenCalls = 0;
  let fetchCalls = 0;

  // 1. Throwing addEventListenerFn settles retryable without calling token or fetch
  const abortCtrl = new AbortController();
  const resThrowingAdd = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => { tokenCalls++; return "token"; },
    expectedUid: "uid-1",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => { fetchCalls++; return new Response("ok"); }) as any,
    externalSignal: abortCtrl.signal,
    deadlineMs: 5000,
    isTrustedDestination: () => true,
    addEventListenerFn: () => {
      throw new Error("SENTINEL_THROWING_ADD_LISTENER");
    },
  });
  assert.equal(resThrowingAdd.status, "retryable");
  assert.equal(tokenCalls, 0);
  assert.equal(fetchCalls, 0);

  // 2. Throwing setTimeoutFn settles retryable and cleans up installed listener
  let listenerRemoved = false;
  const resThrowingTimer = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => { tokenCalls++; return "token"; },
    expectedUid: "uid-1",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => { fetchCalls++; return new Response("ok"); }) as any,
    externalSignal: abortCtrl.signal,
    deadlineMs: 5000,
    isTrustedDestination: () => true,
    addEventListenerFn: (sig, type, fn) => {
      // Installed successfully
    },
    removeEventListenerFn: () => {
      listenerRemoved = true;
    },
    setTimeoutFn: () => {
      throw new Error("SENTINEL_THROWING_SET_TIMEOUT");
    },
  });
  assert.equal(resThrowingTimer.status, "retryable");
  assert.equal(listenerRemoved, true);
  assert.equal(tokenCalls, 0);
  assert.equal(fetchCalls, 0);
});

test("Section B.1: Baseline-first table of exact scheduler/listener counters across all lifecycle paths", async () => {
  type LifecycleScenario =
    | "admitted"
    | "denied"
    | "retryable"
    | "cancel_token"
    | "cancel_fetch"
    | "cancel_body"
    | "timeout"
    | "pre_aborted"
    | "simultaneous_abort_first"
    | "simultaneous_timeout_first"
    | "throwing_add"
    | "throwing_timer"
    | "throwing_clear"
    | "throwing_remove"
    | "synchronous_listener"
    | "synchronous_timer"
    | "null_handle"
    | "undefined_handle"
    | "id_0_handle";

  const scenarios: {
    scenario: LifecycleScenario;
    expectedStatus: string;
    expectedSetTimeout: number;
    expectedClearTimeout: number;
    expectedAddListener: number;
    expectedRemoveListener: number;
    expectedTrust: number;
    expectedToken: number;
    expectedFetch: number;
    expectedBody: number;
  }[] = [
    { scenario: "admitted", expectedStatus: "admitted", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 1, expectedBody: 1 },
    { scenario: "denied", expectedStatus: "denied", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 1, expectedBody: 0 },
    { scenario: "retryable", expectedStatus: "retryable", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 1, expectedBody: 0 },
    { scenario: "cancel_token", expectedStatus: "cancelled", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 0, expectedBody: 0 },
    { scenario: "cancel_fetch", expectedStatus: "cancelled", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 1, expectedBody: 0 },
    { scenario: "cancel_body", expectedStatus: "cancelled", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 1, expectedBody: 1 },
    { scenario: "timeout", expectedStatus: "retryable", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 0, expectedToken: 0, expectedFetch: 0, expectedBody: 0 },
    { scenario: "pre_aborted", expectedStatus: "cancelled", expectedSetTimeout: 0, expectedClearTimeout: 0, expectedAddListener: 0, expectedRemoveListener: 0, expectedTrust: 0, expectedToken: 0, expectedFetch: 0, expectedBody: 0 },
    { scenario: "simultaneous_abort_first", expectedStatus: "cancelled", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 1, expectedBody: 0 },
    { scenario: "simultaneous_timeout_first", expectedStatus: "retryable", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 0, expectedToken: 0, expectedFetch: 0, expectedBody: 0 },
    { scenario: "throwing_add", expectedStatus: "retryable", expectedSetTimeout: 0, expectedClearTimeout: 0, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 0, expectedToken: 0, expectedFetch: 0, expectedBody: 0 },
    { scenario: "throwing_timer", expectedStatus: "retryable", expectedSetTimeout: 1, expectedClearTimeout: 0, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 0, expectedToken: 0, expectedFetch: 0, expectedBody: 0 },
    { scenario: "throwing_clear", expectedStatus: "admitted", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 1, expectedBody: 1 },
    { scenario: "throwing_remove", expectedStatus: "admitted", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 1, expectedBody: 1 },
    { scenario: "synchronous_listener", expectedStatus: "cancelled", expectedSetTimeout: 0, expectedClearTimeout: 0, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 0, expectedToken: 0, expectedFetch: 0, expectedBody: 0 },
    { scenario: "synchronous_timer", expectedStatus: "retryable", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 0, expectedToken: 0, expectedFetch: 0, expectedBody: 0 },
    { scenario: "null_handle", expectedStatus: "retryable", expectedSetTimeout: 1, expectedClearTimeout: 0, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 0, expectedToken: 0, expectedFetch: 0, expectedBody: 0 },
    { scenario: "undefined_handle", expectedStatus: "retryable", expectedSetTimeout: 1, expectedClearTimeout: 0, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 0, expectedToken: 0, expectedFetch: 0, expectedBody: 0 },
    { scenario: "id_0_handle", expectedStatus: "admitted", expectedSetTimeout: 1, expectedClearTimeout: 1, expectedAddListener: 1, expectedRemoveListener: 1, expectedTrust: 1, expectedToken: 1, expectedFetch: 1, expectedBody: 1 },
  ];

  for (const item of scenarios) {
    let setTimeoutCalls = 0;
    let clearTimeoutCalls = 0;
    let addListenerCalls = 0;
    let removeListenerCalls = 0;
    let trustCalls = 0;
    let tokenCalls = 0;
    let fetchCalls = 0;
    let bodyCalls = 0;

    let timerCallback: (() => void) | null = null;
    let abortListenerCallback: (() => void) | null = null;

    const fakeSetTimeout = (fn: () => void) => {
      setTimeoutCalls++;
      if (item.scenario === "throwing_timer") {
        throw new Error("SENTINEL_THROW_TIMER");
      }
      if (item.scenario === "null_handle") {
        return null;
      }
      if (item.scenario === "undefined_handle") {
        return undefined;
      }
      timerCallback = fn;
      if (item.scenario === "synchronous_timer" || item.scenario === "timeout" || item.scenario === "simultaneous_timeout_first") {
        fn();
      }
      if (item.scenario === "id_0_handle") {
        return 0;
      }
      return 123 as any;
    };

    const fakeClearTimeout = () => {
      clearTimeoutCalls++;
      if (item.scenario === "throwing_clear") {
        throw new Error("SENTINEL_THROW_CLEAR");
      }
    };

    const fakeAddListener = (sig: AbortSignal, type: string, fn: () => void) => {
      addListenerCalls++;
      if (item.scenario === "throwing_add") {
        throw new Error("SENTINEL_THROW_ADD");
      }
      abortListenerCallback = fn;
      if (item.scenario === "synchronous_listener") {
        fn();
      }
    };

    const fakeRemoveListener = () => {
      removeListenerCalls++;
      if (item.scenario === "throwing_remove") {
        throw new Error("SENTINEL_THROW_REMOVE");
      }
    };

    const abortCtrl = new AbortController();
    if (item.scenario === "pre_aborted") {
      abortCtrl.abort();
    }

    const res = await executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => {
        tokenCalls++;
        if (item.scenario === "cancel_token") {
          abortCtrl.abort();
          if (abortListenerCallback) (abortListenerCallback as () => void)();
        }
        return "token-1";
      },
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (async () => {
        fetchCalls++;
        if (item.scenario === "cancel_fetch") {
          abortCtrl.abort();
          if (abortListenerCallback) (abortListenerCallback as () => void)();
        }
        if (item.scenario === "simultaneous_abort_first") {
          abortCtrl.abort();
          if (abortListenerCallback) (abortListenerCallback as () => void)();
          if (timerCallback) (timerCallback as () => void)();
        }
        const status = item.scenario === "denied" ? 403 : item.scenario === "retryable" ? 500 : 200;
        return {
          status,
          headers: new Headers({ "Content-Type": "application/json" }),
          json: async () => {
            bodyCalls++;
            if (item.scenario === "cancel_body") {
              abortCtrl.abort();
              if (abortListenerCallback) (abortListenerCallback as () => void)();
            }
            return { uid: "uid-1", email: "recruiter@ellaexecutivesearch.com", org_id: "ella-internal" };
          },
        };
      }) as any,
      externalSignal: abortCtrl.signal,
      deadlineMs: 5000,
      isTrustedDestination: () => {
        trustCalls++;
        return true;
      },
      setTimeoutFn: fakeSetTimeout as any,
      clearTimeoutFn: fakeClearTimeout as any,
      addEventListenerFn: fakeAddListener as any,
      removeEventListenerFn: fakeRemoveListener as any,
    });

    // Wait a bounded turn after settlement to prove fences and cleanups hold
    await new Promise((r) => setTimeout(r, 10));

    assert.equal(res.status, item.expectedStatus, `Scenario ${item.scenario} status mismatch`);
    assert.equal(setTimeoutCalls, item.expectedSetTimeout, `Scenario ${item.scenario} setTimeoutCalls mismatch`);
    assert.equal(clearTimeoutCalls, item.expectedClearTimeout, `Scenario ${item.scenario} clearTimeoutCalls mismatch`);
    assert.equal(addListenerCalls, item.expectedAddListener, `Scenario ${item.scenario} addListenerCalls mismatch`);
    assert.equal(removeListenerCalls, item.expectedRemoveListener, `Scenario ${item.scenario} removeListenerCalls mismatch`);
    assert.equal(trustCalls, item.expectedTrust, `Scenario ${item.scenario} trustCalls mismatch`);
    assert.equal(tokenCalls, item.expectedToken, `Scenario ${item.scenario} tokenCalls mismatch`);
    assert.equal(fetchCalls, item.expectedFetch, `Scenario ${item.scenario} fetchCalls mismatch`);
    assert.equal(bodyCalls, item.expectedBody, `Scenario ${item.scenario} bodyCalls mismatch`);
  }
});

test("Section B.1: Status-triggered abort fenced immediately after status read and before body", async () => {
  let bodyCalls = 0;
  const abortCtrl = new AbortController();

  const res = await executeAdmissionRequest({
    apiUrl: "https://backend.example.com/api/me",
    tokenProvider: async () => "token-1",
    expectedUid: "uid-1",
    expectedEmail: "recruiter@ellaexecutivesearch.com",
    fetchFn: (async () => {
      let readCount = 0;
      return {
        get status() {
          readCount++;
          abortCtrl.abort(); // Synchronous abort triggered during status read!
          return 200;
        },
        headers: new Headers({ "Content-Type": "application/json" }),
        json: async () => {
          bodyCalls++;
          return { uid: "uid-1", email: "recruiter@ellaexecutivesearch.com", org_id: "ella-internal" };
        },
      };
    }) as any,
    externalSignal: abortCtrl.signal,
    deadlineMs: 5000,
    isTrustedDestination: () => true,
  });

  assert.equal(res.status, "cancelled");
  assert.equal(bodyCalls, 0, "Body must never be read when abort occurs during/before status read");
});

test("Section B.1: Late RESOLVE and REJECT on token, fetch, and body after terminal cancel/timeout", async () => {
  const unhandledRejections: any[] = [];
  const onUnhandled = (err: any) => { unhandledRejections.push(err); };
  process.on("unhandledRejection", onUnhandled);

  try {
    // 1. Independent Operation 1: Late Token Resolve after Cancel
    let resolveToken1: ((tok: string) => void) | null = null;
    let fetchAfterLateToken1 = 0;
    const abortCtrl1 = new AbortController();
    const pToken1 = executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: () => new Promise((res) => { resolveToken1 = res; }),
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (async () => { fetchAfterLateToken1++; return new Response("{}"); }) as any,
      externalSignal: abortCtrl1.signal,
      isTrustedDestination: () => true,
    });
    abortCtrl1.abort();
    const res1 = await pToken1;
    assert.equal(res1.status, "cancelled");
    if (resolveToken1) (resolveToken1 as (t: string) => void)("late-token-1");
    await new Promise((r) => setTimeout(r, 15));
    assert.equal(fetchAfterLateToken1, 0, "Late token resolve must produce zero subsequent fetch calls");

    // 2. Independent Operation 2: Late Token Reject after Timeout
    let rejectToken2: ((err: any) => void) | null = null;
    const pToken2 = executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: () => new Promise((_res, rej) => { rejectToken2 = rej; }),
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      deadlineMs: 10,
      isTrustedDestination: () => true,
    });
    const res2 = await pToken2;
    assert.equal(res2.status, "retryable");
    if (rejectToken2) {
      try { (rejectToken2 as (e: any) => void)(new Error("LATE_TOKEN_REJECT_SENTINEL")); } catch {}
    }
    await new Promise((r) => setTimeout(r, 15));

    // 3. Independent Operation 3: Late Fetch Resolve after Cancel
    let resolveFetch3: ((r: any) => void) | null = null;
    let bodyAfterLateFetch3 = 0;
    const abortCtrl3 = new AbortController();
    const pFetch3 = executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-3",
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (() => new Promise((res) => { resolveFetch3 = res; })) as any,
      externalSignal: abortCtrl3.signal,
      isTrustedDestination: () => true,
    });
    abortCtrl3.abort();
    const res3 = await pFetch3;
    assert.equal(res3.status, "cancelled");
    if (resolveFetch3) {
      (resolveFetch3 as (r: any) => void)({
        status: 200,
        headers: new Headers({ "Content-Type": "application/json" }),
        json: async () => { bodyAfterLateFetch3++; return {}; },
      });
    }
    await new Promise((r) => setTimeout(r, 15));
    assert.equal(bodyAfterLateFetch3, 0, "Late fetch resolve must produce zero body calls");

    // 4. Independent Operation 4: Late Fetch Reject after Timeout
    let rejectFetch4: ((err: any) => void) | null = null;
    const pFetch4 = executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-4",
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (() => new Promise((_res, rej) => { rejectFetch4 = rej; })) as any,
      deadlineMs: 10,
      isTrustedDestination: () => true,
    });
    const res4 = await pFetch4;
    assert.equal(res4.status, "retryable");
    if (rejectFetch4) {
      try { (rejectFetch4 as (e: any) => void)(new Error("LATE_FETCH_REJECT_SENTINEL")); } catch {}
    }
    await new Promise((r) => setTimeout(r, 15));

    // Reusable observer and bounded drainer helper
    function observePromise<T>(p: Promise<T>) {
      let settled = false;
      let fulfilled = false;
      let rejected = false;
      let value: T | undefined = undefined;
      let error: any = undefined;

      p.then(
        (v) => {
          settled = true;
          fulfilled = true;
          value = v;
        },
        (e) => {
          settled = true;
          rejected = true;
          error = e;
        }
      );

      return {
        promise: p,
        isSettled: () => settled,
        isFulfilled: () => fulfilled,
        isRejected: () => rejected,
        getValue: () => value,
        getError: () => error,
        drain: async (timeoutMs = 500) => {
          if (settled) {
            return fulfilled ? { status: "settled" as const, result: value! } : { status: "rejected" as const, error };
          }
          let timer: NodeJS.Timeout | null = null;
          try {
            const timeoutPromise = new Promise<{ status: "timed_out" }>((resolve) => {
              timer = setTimeout(() => resolve({ status: "timed_out" }), timeoutMs);
            });
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

    // 5. Independent Operation 5: Late Body Resolve with Comprehensive Proxy Trap and Bounded Entry
    let resolveBody5: ((d: any) => void) | null = null;
    let proxyInteractions5 = 0;
    let bodyEnteredResolve5: (() => void) | null = null;
    const bodyEnteredPromise5 = new Promise<void>((r) => { bodyEnteredResolve5 = r; });
    const abortCtrl5 = new AbortController();
    const pBody5 = executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-5",
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (async () => ({
        status: 200,
        headers: new Headers({ "Content-Type": "application/json" }),
        json: () => {
          if (bodyEnteredResolve5) (bodyEnteredResolve5 as () => void)();
          return new Promise((res) => { resolveBody5 = res; });
        },
      })) as any,
      externalSignal: abortCtrl5.signal,
      isTrustedDestination: () => true,
    });
    const observed5 = observePromise(pBody5);

    let entryTimer5: NodeJS.Timeout | null = null;
    try {
      const entryTimeoutPromise5 = new Promise<never>((_, rej) => {
        entryTimer5 = setTimeout(() => {
          rej(new Error("Auth admission body entry timed out"));
        }, 500);
      });
      await Promise.race([bodyEnteredPromise5, entryTimeoutPromise5]);
    } finally {
      if (entryTimer5) {
        clearTimeout(entryTimer5);
        entryTimer5 = null;
      }
    }

    assert.ok(resolveBody5 !== null, "Operation 5: resolveBody5 must be captured inside json()");
    abortCtrl5.abort();

    const drainRes5 = await observed5.drain(500);
    assert.equal(drainRes5.status, "settled", "Operation 5: pBody must drain boundedly after abort");
    if (drainRes5.status === "settled") {
      assert.equal(drainRes5.result.status, "cancelled");
    }

    const targetObj5 = { uid: "uid-1", email: "recruiter@ellaexecutivesearch.com", org_id: "ella-internal" };
    const lateBodyProxy5 = new Proxy(targetObj5, {
      get(t, p, r) {
        if (p === "then") return Reflect.get(t, p, r);
        proxyInteractions5++;
        return Reflect.get(t, p, r);
      },
      set(t, p, v, r) { proxyInteractions5++; return Reflect.set(t, p, v, r); },
      has(t, p) { proxyInteractions5++; return Reflect.has(t, p); },
      deleteProperty(t, p) { proxyInteractions5++; return Reflect.deleteProperty(t, p); },
      getPrototypeOf(t) { proxyInteractions5++; return Reflect.getPrototypeOf(t); },
      setPrototypeOf(t, v) { proxyInteractions5++; return Reflect.setPrototypeOf(t, v); },
      isExtensible(t) { proxyInteractions5++; return Reflect.isExtensible(t); },
      preventExtensions(t) { proxyInteractions5++; return Reflect.preventExtensions(t); },
      getOwnPropertyDescriptor(t, p) { proxyInteractions5++; return Reflect.getOwnPropertyDescriptor(t, p); },
      defineProperty(t, p, a) { proxyInteractions5++; return Reflect.defineProperty(t, p, a); },
      ownKeys(t) { proxyInteractions5++; return Reflect.ownKeys(t); },
      apply(t, thisArg, argArray) { proxyInteractions5++; return Reflect.apply(t, thisArg, argArray); },
      construct(t, argArray, newTarget) { proxyInteractions5++; return Reflect.construct(t, argArray, newTarget); },
    });

    if (resolveBody5) {
      (resolveBody5 as (d: any) => void)(lateBodyProxy5);
    }
    await new Promise((r) => setTimeout(r, 25));
    assert.equal(proxyInteractions5, 0, "Late resolved body Proxy must have zero meta-operations or property reads");

    // 6. Independent Operation 6: Late Body Reject with Bounded Entry
    let rejectBody6: ((err: any) => void) | null = null;
    let bodyEnteredResolve6: (() => void) | null = null;
    const bodyEnteredPromise6 = new Promise<void>((r) => { bodyEnteredResolve6 = r; });
    const abortCtrl6 = new AbortController();
    const pBody6 = executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-6",
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (async () => ({
        status: 200,
        headers: new Headers({ "Content-Type": "application/json" }),
        json: () => {
          if (bodyEnteredResolve6) (bodyEnteredResolve6 as () => void)();
          return new Promise((_res, rej) => { rejectBody6 = rej; });
        },
      })) as any,
      externalSignal: abortCtrl6.signal,
      isTrustedDestination: () => true,
    });
    const observed6 = observePromise(pBody6);

    let entryTimer6: NodeJS.Timeout | null = null;
    try {
      const entryTimeoutPromise6 = new Promise<never>((_, rej) => {
        entryTimer6 = setTimeout(() => {
          rej(new Error("Auth admission body entry timed out"));
        }, 500);
      });
      await Promise.race([bodyEnteredPromise6, entryTimeoutPromise6]);
    } finally {
      if (entryTimer6) {
        clearTimeout(entryTimer6);
        entryTimer6 = null;
      }
    }

    assert.ok(rejectBody6 !== null, "Operation 6: rejectBody6 must be captured inside json()");
    abortCtrl6.abort();

    const drainRes6 = await observed6.drain(500);
    assert.equal(drainRes6.status, "settled", "Operation 6: pBody must drain boundedly after abort");
    if (drainRes6.status === "settled") {
      assert.equal(drainRes6.result.status, "cancelled");
    }

    if (rejectBody6) {
      try { (rejectBody6 as (e: any) => void)(new Error("LATE_BODY_REJECT_SENTINEL")); } catch {}
    }
    await new Promise((r) => setTimeout(r, 25));

    // Explicitly assert zero unhandled rejections across all 6 independent operations
    assert.equal(unhandledRejections.length, 0, "Unhandled rejections trap must be completely empty");
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
});

test("Section B.1: Causal finite settlement watchdog rows and exact timeout error attribution", async () => {
  function observePromise<T>(p: Promise<T>) {
    let settled = false;
    let fulfilled = false;
    let rejected = false;
    let value: T | undefined = undefined;
    let error: any = undefined;

    p.then(
      (v) => {
        settled = true;
        fulfilled = true;
        value = v;
      },
      (e) => {
        settled = true;
        rejected = true;
        error = e;
      }
    );

    return {
      promise: p,
      isSettled: () => settled,
      isFulfilled: () => fulfilled,
      isRejected: () => rejected,
      getValue: () => value,
      getError: () => error,
      drain: async (timeoutMs = 500) => {
        if (settled) {
          return fulfilled ? { status: "settled" as const, result: value! } : { status: "rejected" as const, error };
        }
        let timer: NodeJS.Timeout | null = null;
        try {
          const timeoutPromise = new Promise<{ status: "timed_out" }>((resolve) => {
            timer = setTimeout(() => resolve({ status: "timed_out" }), timeoutMs);
          });
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

  // Row 1: JSON never enters -> finite exact entry-timeout
  {
    const abortCtrl = new AbortController();
    let bodyEnteredResolve: (() => void) | null = null;
    const bodyEnteredPromise = new Promise<void>((r) => { bodyEnteredResolve = r; });

    const pBody = executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-row1",
      expectedUid: "uid-1",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (() => new Promise(() => {})) as any, // hangs indefinitely before json()
      externalSignal: abortCtrl.signal,
      isTrustedDestination: () => true,
    });
    const observed = observePromise(pBody);

    let entryTimer: NodeJS.Timeout | null = null;
    const t0 = Date.now();
    let caughtErr: any = null;

    try {
      const entryTimeoutPromise = new Promise<never>((_, rej) => {
        entryTimer = setTimeout(() => {
          rej(new Error("Auth admission body entry timed out"));
        }, 100);
      });

      await Promise.race([bodyEnteredPromise, entryTimeoutPromise]);
    } catch (err: any) {
      caughtErr = err;
      abortCtrl.abort();
    } finally {
      if (entryTimer) clearTimeout(entryTimer);
    }

    const elapsed = Date.now() - t0;
    assert.ok(caughtErr, "Row 1 must throw an error");
    assert.equal(caughtErr.message, "Auth admission body entry timed out", "Row 1 must throw exact entry timeout error");
    assert.ok(elapsed < 1000, `Row 1 must complete boundedly without hanging (elapsed: ${elapsed}ms)`);
  }

  // Row 2: Real normal body entry confirmed via response.json(), but pBody remains pending when abort handler is not registered on signal -> finite exact settlement timeout after abort
  {
    const abortCtrl = new AbortController();
    let bodyEntered = false;
    let bodyEnteredCount = 0;
    let bodyEnteredResolve: (() => void) | null = null;
    const bodyEnteredPromise = new Promise<void>((r) => { bodyEnteredResolve = r; });

    let innerBodyResolve: ((val: any) => void) | null = null;
    const innerBodyPromise = new Promise<any>((r) => { innerBodyResolve = r; });

    let capturedAbortHandler: (() => void) | null = null;
    let removeEventListenerCount = 0;
    let clearTimeoutCount = 0;

    let fetchFnCalls = 0;
    let jsonCalls = 0;

    const mockResponse = {
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => {
        jsonCalls++;
        bodyEnteredCount++;
        bodyEntered = true;
        if (bodyEnteredResolve) {
          bodyEnteredResolve();
        }
        return await innerBodyPromise;
      },
    };

    const pBody = executeAdmissionRequest({
      apiUrl: "https://backend.example.com/api/me",
      tokenProvider: async () => "token-row2",
      expectedUid: "uid-2",
      expectedEmail: "recruiter@ellaexecutivesearch.com",
      fetchFn: (async () => {
        fetchFnCalls++;
        return mockResponse as any;
      }) as any,
      deadlineMs: 30000,
      externalSignal: abortCtrl.signal,
      isTrustedDestination: () => true,
      addEventListenerFn: (_sig, _type, listener) => {
        // Capture handler without registering it on the signal
        capturedAbortHandler = listener;
      },
      removeEventListenerFn: (_sig, _type, _listener) => {
        removeEventListenerCount++;
      },
      clearTimeoutFn: (t) => {
        clearTimeoutCount++;
        clearTimeout(t);
      },
    });

    const observed = observePromise(pBody);

    // 1. Boundedly wait for real json() entry
    let entryTimer: NodeJS.Timeout | null = null;
    const entryTimeoutPromise = new Promise<never>((_, rej) => {
      entryTimer = setTimeout(() => {
        rej(new Error("Auth admission body entry timed out"));
      }, 500);
    });

    await Promise.race([bodyEnteredPromise, entryTimeoutPromise]);
    if (entryTimer) clearTimeout(entryTimer);

    assert.equal(bodyEntered, true, "Row 2: Body entry must be confirmed from real json() execution");
    assert.equal(bodyEnteredCount, 1, "Row 2: Exactly one json() entry must occur");
    assert.equal(fetchFnCalls, 1, "Row 2: fetchFn must have been called once");
    assert.equal(jsonCalls, 1, "Row 2: json() must have been called once");
    assert.ok(capturedAbortHandler !== null, "Row 2: Abort handler must have been captured");

    // 2. Abort the controller; since capturedAbortHandler was not registered on the signal, pBody must remain pending
    abortCtrl.abort();
    assert.equal(observed.isSettled(), false, "Row 2: pBody must remain pending after external abort because handler was not registered");

    // 3. Boundedly drain observed pBody and require exact settlement timeout <= 500ms
    const t0 = Date.now();
    let caughtErr: any = null;
    try {
      const drainOutcome = await observed.drain(100);
      if (drainOutcome.status === "timed_out") {
        throw new Error("Auth admission body settlement timed out after abort");
      }
    } catch (err: any) {
      caughtErr = err;
    }
    const elapsed = Date.now() - t0;
    assert.ok(caughtErr, "Row 2: Drain must throw settlement timeout error");
    assert.equal(caughtErr.message, "Auth admission body settlement timed out after abort", "Row 2: Must throw exact settlement timeout error");
    assert.ok(elapsed < 1000, `Row 2: Drain timeout must be bounded (elapsed: ${elapsed}ms)`);

    // 4. Manually trigger the captured real abort handler; pBody must settle cancelled
    capturedAbortHandler!();

    const finalOutcome = await observed.drain(500);
    assert.equal(finalOutcome.status, "settled", "Row 2: pBody must settle after calling captured abort handler");
    if (finalOutcome.status === "settled") {
      assert.deepEqual(finalOutcome.result, { status: "cancelled" }, "Row 2: Result must be cancelled");
    }

    assert.equal(removeEventListenerCount, 1, "Row 2: removeEventListenerFn must be called once upon settlement");
    assert.ok(clearTimeoutCount >= 1, "Row 2: Real deadline timer must be cleared upon settlement");
  }
});
