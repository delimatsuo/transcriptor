"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { Auth, User } from "firebase/auth";
import { getFirebaseAuth } from "./firebase";
import {
  isTrustedApiDestination,
  publicRuntimeConfigResult,
} from "./runtimeConfig";
import {
  createProductionAuthController,
  createAuthLifecycleAdapter,
  getInitialAuthState,
  type AuthController,
  type AuthLifecycleAdapter,
  type AuthState,
  type AuthStatus,
  type AuthUserInfo,
  syntheticAuthUser,
  toAuthUser,
} from "./authController";

export type { AuthStatus, AuthUserInfo, AuthLifecycleAdapter };
export { syntheticAuthUser, toAuthUser, createAuthLifecycleAdapter, getInitialAuthState };

export class ApiFetchError extends Error {
  constructor(message = "API request rejected") {
    super(message);
    this.name = "ApiFetchError";
  }
}

export const authBypassEnabled = (() => {
  try {
    return publicRuntimeConfigResult.ok
      ? publicRuntimeConfigResult.value.authBypassEnabled
      : false;
  } catch {
    return false;
  }
})();

export async function getIdToken(forceRefresh = false): Promise<string | null> {
  if (!publicRuntimeConfigResult.ok) return null;
  if (publicRuntimeConfigResult.value.authBypassEnabled) return null;
  const auth = getFirebaseAuth();
  if (!auth?.currentUser) return null;
  try {
    return await auth.currentUser.getIdToken(forceRefresh);
  } catch {
    return null;
  }
}

export interface ApiFetchDependencies {
  fetch?: typeof fetch;
  isTrustedDestination?: (dest: string | Request | URL) => boolean;
  getAuth?: () => Auth | null;
  isBypassEnabled?: () => boolean;
}

/**
 * Fetch wrapper that attaches Bearer tokens ONLY to trusted backend API endpoints,
 * enforces redirect: "error", handles Request cloning, and enforces same-principal 401 retry.
 */
export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
  deps: ApiFetchDependencies = {}
): Promise<Response> {
  const isTrustedFn = (dest: string | Request | URL): boolean => {
    try {
      const fn = deps.isTrustedDestination ?? isTrustedApiDestination;
      return Boolean(fn(dest));
    } catch {
      return false;
    }
  };

  const fetchFn = async (req: RequestInfo | URL, initObj?: RequestInit): Promise<Response> => {
    try {
      const fn = deps.fetch ?? fetch;
      return await fn(req as any, initObj);
    } catch (err: unknown) {
      if (err instanceof ApiFetchError && err.message === "Redirects are prohibited") {
        throw new ApiFetchError("Redirects are prohibited");
      }
      throw new ApiFetchError("Network request failed");
    }
  };

  let isBypass = false;
  let bypassResolved = false;
  try {
    if (deps.isBypassEnabled !== undefined) {
      let rawBypassVal: unknown;
      if (typeof deps.isBypassEnabled === "function") {
        rawBypassVal = deps.isBypassEnabled();
      } else {
        rawBypassVal = deps.isBypassEnabled;
      }
      if (typeof rawBypassVal !== "boolean") {
        throw new Error();
      }
      isBypass = rawBypassVal;
      bypassResolved = true;
    } else {
      if (!publicRuntimeConfigResult.ok) {
        throw new Error();
      }
      const rawCfgBypass = publicRuntimeConfigResult.value.authBypassEnabled;
      if (typeof rawCfgBypass !== "boolean") {
        throw new Error();
      }
      isBypass = rawCfgBypass;
      bypassResolved = true;
    }
  } catch {
    bypassResolved = false;
  }

  if (!bypassResolved) {
    throw new ApiFetchError("Authentication mode resolution failed");
  }

  const safeGetAuth = (): Auth | null => {
    try {
      const fn = deps.getAuth ?? getFirebaseAuth;
      return fn();
    } catch {
      throw new ApiFetchError("Authentication service lookup failed");
    }
  };

  const safeGetCurrentUser = (a: Auth | null): User | null => {
    if (!a) return null;
    try {
      return a.currentUser;
    } catch {
      throw new ApiFetchError("Authentication state lookup failed");
    }
  };

  const safeGetUid = (u: User | null): string | null => {
    if (!u) return null;
    let rawUid: unknown = undefined;
    try {
      rawUid = u.uid;
    } catch {
      throw new ApiFetchError("Authentication principal lookup failed");
    }
    if (
      typeof rawUid !== "string" ||
      rawUid.length < 1 ||
      rawUid.length > 128 ||
      rawUid !== rawUid.trim() ||
      /[\u0000-\u001F\u007F-\uFFFF\s]/.test(rawUid)
    ) {
      throw new ApiFetchError("Authentication principal invalid");
    }
    return rawUid;
  };

  // 1. Validate destination raw string before URL canonicalization
  let rawDestination = "";
  try {
    if (typeof input === "string") {
      rawDestination = input;
    } else if (input instanceof URL) {
      rawDestination = input.toString();
    } else if (typeof Request !== "undefined" && input instanceof Request) {
      rawDestination = input.url;
    } else if (input && typeof (input as { url?: unknown }).url === "string") {
      rawDestination = (input as { url: string }).url;
    } else {
      throw new Error();
    }
  } catch {
    throw new ApiFetchError("Invalid request destination");
  }

  // Reject whitespace, controls, non-ASCII, backslash, fragment, userinfo, or relative inputs
  if (!rawDestination || rawDestination !== rawDestination.trim() || !/^[\x20-\x7E]+$/.test(rawDestination)) {
    throw new ApiFetchError("Invalid request destination");
  }
  if (rawDestination.includes("\\") || /%5[cC]/i.test(rawDestination)) {
    throw new ApiFetchError("Invalid request destination");
  }
  if (rawDestination.includes("#") || /%23/i.test(rawDestination)) {
    throw new ApiFetchError("Invalid request destination");
  }
  if (rawDestination.includes("@")) {
    throw new ApiFetchError("Invalid request destination");
  }
  if (!rawDestination.startsWith("http://") && !rawDestination.startsWith("https://")) {
    throw new ApiFetchError("Relative request destinations are prohibited");
  }

  const match = /^(https?:\/\/[^/?#]+)(.*)/i.exec(rawDestination);
  if (!match) {
    throw new ApiFetchError("Invalid request destination");
  }
  const rawPathAndQuery = match[2];
  if (rawPathAndQuery.startsWith("//") || rawPathAndQuery.includes("//")) {
    throw new ApiFetchError("Invalid request destination");
  }
  if (/%2[fF]/i.test(rawPathAndQuery)) {
    throw new ApiFetchError("Invalid request destination");
  }
  if (
    /%2[eE]/i.test(rawPathAndQuery) ||
    /\/\.\.(?:\/|$)/.test(rawPathAndQuery) ||
    /\/\.(?:\/|$)/.test(rawPathAndQuery)
  ) {
    throw new ApiFetchError("Invalid request destination");
  }

  const [pathPortion] = rawPathAndQuery.split("?");
  if (pathPortion !== "/api" && !pathPortion.startsWith("/api/")) {
    throw new ApiFetchError("Invalid request destination");
  }

  let canonicalParsedUrl: URL;
  try {
    canonicalParsedUrl = new URL(rawDestination);
  } catch {
    throw new ApiFetchError("Invalid request destination");
  }

  if (canonicalParsedUrl.username || canonicalParsedUrl.password || canonicalParsedUrl.hash) {
    throw new ApiFetchError("Invalid request destination");
  }
  if (canonicalParsedUrl.protocol !== "http:" && canonicalParsedUrl.protocol !== "https:") {
    throw new ApiFetchError("Relative request destinations are prohibited");
  }
  const canonicalDestination = canonicalParsedUrl.toString();

  let isTrusted = false;
  try {
    isTrusted = isTrustedFn(canonicalDestination);
  } catch {
    throw new ApiFetchError("Untrusted API destination");
  }
  if (!isTrusted) {
    throw new ApiFetchError("Untrusted API destination");
  }

  // Pre-check bodyUsed before constructing native Request
  if (typeof Request !== "undefined" && input instanceof Request && input.bodyUsed) {
    throw new ApiFetchError("Request body already used");
  }

  // 2. Construct native prepared Request capturing all RequestInit overrides and forced redirect: error
  let preparedRequest: Request;
  try {
    const forcedInit: RequestInit = { ...init, redirect: "error" };
    preparedRequest = new Request(input as any, forcedInit);
  } catch {
    throw new ApiFetchError("Invalid request parameters");
  }

  // From here on, NEVER access caller input or init again — use only preparedRequest
  const reqMethod = preparedRequest.method.toUpperCase();
  const isBodyAllowed = reqMethod !== "GET" && reqMethod !== "HEAD";

  if (!isBodyAllowed && preparedRequest.body !== null) {
    throw new ApiFetchError("Request body prohibited for GET/HEAD");
  }

  let bodyBytes: ArrayBuffer | null = null;
  if (isBodyAllowed) {
    if (preparedRequest.bodyUsed) {
      throw new ApiFetchError("Request body already used");
    }
    if (preparedRequest.body !== null) {
      try {
        const clone = preparedRequest.clone();
        bodyBytes = await clone.arrayBuffer();
      } catch {
        throw new ApiFetchError("Request body is not replayable");
      }
    }
  }

  // Freeze all snapshot fields from preparedRequest
  const frozenMethod = reqMethod;
  const frozenHeaders = new Headers(preparedRequest.headers);
  const frozenMode = preparedRequest.mode;
  const frozenCredentials = preparedRequest.credentials;
  const frozenCache = preparedRequest.cache;
  const frozenReferrer = preparedRequest.referrer;
  const frozenReferrerPolicy = preparedRequest.referrerPolicy;
  const frozenIntegrity = preparedRequest.integrity;
  const frozenSignal = preparedRequest.signal;
  const frozenKeepalive = preparedRequest.keepalive;
  const frozenBodyBytes = bodyBytes;

  const createDispatchedRequest = (bearerToken?: string | null): Request => {
    let dispatchHeaders: Headers;
    try {
      dispatchHeaders = new Headers(frozenHeaders);
      if (bearerToken) {
        dispatchHeaders.set("Authorization", `Bearer ${bearerToken}`);
      } else {
        dispatchHeaders.delete("Authorization");
      }
    } catch {
      throw new ApiFetchError("Failed to prepare request headers");
    }

    const dispatchBody = frozenBodyBytes !== null ? frozenBodyBytes.slice(0) : undefined;

    try {
      return new Request(canonicalDestination, {
        method: frozenMethod,
        headers: dispatchHeaders,
        body: dispatchBody,
        mode: frozenMode,
        credentials: frozenCredentials,
        cache: frozenCache,
        redirect: "error",
        referrer: frozenReferrer,
        referrerPolicy: frozenReferrerPolicy,
        integrity: frozenIntegrity,
        signal: frozenSignal,
        keepalive: frozenKeepalive,
      });
    } catch {
      throw new ApiFetchError("Failed to construct dispatched request");
    }
  };

  const safeExtractStatus = (response: Response): number => {
    let statusVal = 0;
    try {
      statusVal = Number(response.status);
      if (!Number.isSafeInteger(statusVal) || statusVal < 100 || statusVal > 599) {
        throw new Error();
      }
    } catch {
      throw new ApiFetchError("Network response status invalid");
    }
    if (statusVal >= 300 && statusVal < 400) {
      throw new ApiFetchError("Redirects are prohibited");
    }
    return statusVal;
  };

  if (isBypass) {
    const bypassReq = createDispatchedRequest(null);
    let res: Response;
    try {
      res = await fetchFn(bypassReq);
    } catch {
      throw new ApiFetchError("Network request failed");
    }
    safeExtractStatus(res);
    return res;
  }

  const auth = safeGetAuth();
  const initiatingUser = safeGetCurrentUser(auth);

  if (!initiatingUser) {
    const unauthReq = createDispatchedRequest(null);
    let res: Response;
    try {
      res = await fetchFn(unauthReq);
    } catch {
      throw new ApiFetchError("Network request failed");
    }
    safeExtractStatus(res);
    return res;
  }

  // Boundary 0: validate initiating principal UID
  const initiatingUid = safeGetUid(initiatingUser);
  if (!initiatingUid) {
    throw new ApiFetchError("Authentication principal invalid");
  }

  // Obtain initial token
  let token: string | null = null;
  try {
    token = await initiatingUser.getIdToken(false);
  } catch {
    throw new ApiFetchError("Failed to obtain authentication token");
  }

  // Boundary 1: revalidate destination & initiating principal identity after token
  let preDispatchTrusted = false;
  try {
    preDispatchTrusted = isTrustedFn(canonicalDestination);
  } catch {
    throw new ApiFetchError("Untrusted API destination");
  }
  if (!preDispatchTrusted) {
    throw new ApiFetchError("Untrusted API destination");
  }

  const preDispatchAuth = safeGetAuth();
  const preDispatchUser = safeGetCurrentUser(preDispatchAuth);
  const preDispatchUid = safeGetUid(preDispatchUser);
  if (!preDispatchAuth || preDispatchUser !== initiatingUser || preDispatchUid !== initiatingUid) {
    throw new ApiFetchError("Authentication state changed before request dispatch");
  }

  const firstDispatchedReq = createDispatchedRequest(token);
  let firstResponse: Response;
  try {
    firstResponse = await fetchFn(firstDispatchedReq);
  } catch {
    throw new ApiFetchError("Network request failed");
  }

  const firstStatus = safeExtractStatus(firstResponse);

  // 401 Retry
  if (firstStatus === 401 && token) {
    // Boundary 2: revalidate destination & principal after 401, before refresh
    let retryTrusted = false;
    try {
      retryTrusted = isTrustedFn(canonicalDestination);
    } catch {
      throw new ApiFetchError("Untrusted API destination");
    }
    if (!retryTrusted) {
      throw new ApiFetchError("Untrusted API destination");
    }

    const preRetryAuth = safeGetAuth();
    const preRetryUser = safeGetCurrentUser(preRetryAuth);
    const preRetryUid = safeGetUid(preRetryUser);
    if (!preRetryAuth || preRetryUser !== initiatingUser || preRetryUid !== initiatingUid) {
      throw new ApiFetchError("Authentication principal changed during 401 retry");
    }

    let refreshedToken: string | null = null;
    try {
      refreshedToken = await initiatingUser.getIdToken(true);
    } catch {
      throw new ApiFetchError("Token refresh failed");
    }

    // Boundary 3: revalidate destination & principal after refresh, before retry dispatch
    let postRefreshTrusted = false;
    try {
      postRefreshTrusted = isTrustedFn(canonicalDestination);
    } catch {
      throw new ApiFetchError("Untrusted API destination");
    }
    if (!postRefreshTrusted) {
      throw new ApiFetchError("Untrusted API destination");
    }

    const postRefreshAuth = safeGetAuth();
    const postRefreshUser = safeGetCurrentUser(postRefreshAuth);
    const postRefreshUid = safeGetUid(postRefreshUser);
    if (!postRefreshAuth || postRefreshUser !== initiatingUser || postRefreshUid !== initiatingUid || !refreshedToken) {
      throw new ApiFetchError("Authentication principal changed during 401 retry");
    }

    const retryDispatchedReq = createDispatchedRequest(refreshedToken);
    let secondResponse: Response;
    try {
      secondResponse = await fetchFn(retryDispatchedReq);
    } catch {
      throw new ApiFetchError("Network request failed");
    }

    safeExtractStatus(secondResponse);
    return secondResponse;
  }

  return firstResponse;
}

export function setupAuthLifecycle(
  adapter: AuthLifecycleAdapter,
  onStateChange: (state: AuthState) => void
): () => void {
  const unsubscribe = adapter.subscribe(onStateChange);
  adapter.start();
  return () => {
    unsubscribe();
    adapter.dispose();
  };
}

export function useAuth() {
  const adapterRef = useRef<AuthLifecycleAdapter | null>(null);
  const [state, setState] = useState<AuthState>(() => getInitialAuthState());

  useEffect(() => {
    const adapter = createAuthLifecycleAdapter();
    adapterRef.current = adapter;
    setState(adapter.getInitialState());

    const cleanup = setupAuthLifecycle(adapter, (nextState) => {
      setState(nextState);
    });

    return () => {
      cleanup();
      if (adapterRef.current === adapter) {
        adapterRef.current = null;
      }
    };
  }, []);

  const signIn = useCallback(() => adapterRef.current?.signIn() ?? Promise.resolve(), []);
  const signOut = useCallback(() => adapterRef.current?.signOut() ?? Promise.resolve(), []);
  const useAnotherAccount = useCallback(() => adapterRef.current?.useAnotherAccount() ?? Promise.resolve(), []);
  const retry = useCallback(() => adapterRef.current?.retry() ?? Promise.resolve(), []);

  return {
    status: state.status,
    user: state.user,
    error: state.error,
    busy: state.busy,
    signIn,
    signOut,
    useAnotherAccount,
    retry,
  };
}
