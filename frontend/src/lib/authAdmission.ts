import { isTrustedApiDestination } from "./runtimeConfig";

export interface AuthenticatedPrincipal {
  uid: string;
  email: string;
  org_id: string;
}

export type AdmissionResult =
  | { status: "admitted"; principal: AuthenticatedPrincipal }
  | { status: "denied"; statusCode?: number }
  | { status: "retryable" }
  | { status: "cancelled" };

export interface AdmissionOptions {
  apiUrl: string;
  tokenProvider: () => Promise<string | null>;
  expectedUid: string;
  expectedEmail: string;
  fetchFn?: typeof fetch;
  deadlineMs?: number;
  externalSignal?: AbortSignal;
  isTrustedDestination?: (url: string) => boolean;
  setTimeoutFn?: typeof setTimeout;
  clearTimeoutFn?: typeof clearTimeout;
  addEventListenerFn?: (signal: AbortSignal, type: string, listener: () => void, options?: unknown) => void;
  removeEventListenerFn?: (signal: AbortSignal, type: string, listener: () => void) => void;
}

/**
 * Pure predicate checking whether a completed admission request is still current.
 */
export function admissionIsCurrent(
  signal: AbortSignal,
  requestGeneration: number,
  currentGeneration: number,
  requestUid: string,
  currentUid: string
): boolean {
  if (signal.aborted) {
    return false;
  }
  if (requestGeneration !== currentGeneration) {
    return false;
  }
  return requestUid === currentUid;
}

/**
 * Execute admission verification against backend /api/me with timeout and error mapping.
 */
export async function executeAdmissionRequest(
  options: AdmissionOptions
): Promise<AdmissionResult> {
  const {
    apiUrl: targetUrl,
    tokenProvider,
    expectedUid,
    expectedEmail,
    fetchFn = fetch,
    deadlineMs = 10000,
    externalSignal,
    isTrustedDestination = isTrustedApiDestination,
    setTimeoutFn = setTimeout,
    clearTimeoutFn = clearTimeout,
    addEventListenerFn = (sig: AbortSignal, type: string, listener: () => void, opt?: unknown) =>
      sig.addEventListener(type, listener, opt as any),
    removeEventListenerFn = (sig: AbortSignal, type: string, listener: () => void) =>
      sig.removeEventListener(type, listener),
  } = options;

  if (externalSignal?.aborted) {
    return { status: "cancelled" };
  }

  if (typeof expectedUid !== "string" || typeof expectedEmail !== "string" || typeof targetUrl !== "string") {
    return { status: "retryable" };
  }

  // Reject relative target URLs before any token/fetch call
  if (!/^https?:\/\//i.test(targetUrl)) {
    return { status: "retryable" };
  }

  // Pre-validate expected UID & email format
  const isCleanAscii = (s: string) =>
    typeof s === "string" &&
    s.length > 0 &&
    s === s.trim() &&
    !/[\u0000-\u001F\u007F-\uFFFF]/.test(s) &&
    !/\s/.test(s);

  if (!isCleanAscii(expectedUid)) {
    return { status: "retryable" };
  }

  const LOCAL_PART_ALLOWED = new Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'+=?^_`{|}~.-");
  function isValidEmail(email: string): boolean {
    if (!email || email.length > 254 || email !== email.trim() || email.includes(" ") || email.includes(",") || /[\u0000-\u001F\u007F-\uFFFF]/.test(email)) {
      return false;
    }
    const parts = email.split("@");
    if (parts.length !== 2) return false;
    const [local, domain] = parts;
    if (!local || local.length > 64 || local.startsWith(".") || local.endsWith(".") || local.includes("..")) {
      return false;
    }
    for (const c of local) {
      if (!LOCAL_PART_ALLOWED.has(c)) return false;
    }
    if (!domain || domain.length > 253) return false;
    const domainLabels = domain.split(".");
    if (domainLabels.length < 2) return false;
    for (const label of domainLabels) {
      if (!label || label.length > 63 || label.startsWith("-") || label.endsWith("-")) return false;
      if (!/^[a-zA-Z0-9-]+$/.test(label)) return false;
    }
    return true;
  }

  if (!isValidEmail(expectedEmail)) {
    return { status: "retryable" };
  }

  let timerId: ReturnType<typeof setTimeout> | null = null;
  let abortHandler: (() => void) | null = null;
  let listenerInstalled = false;
  const internalAbortController = new AbortController();

  let isSettled = false;
  let settleTerminal: ((result: AdmissionResult) => void) | null = null;
  const terminalPromise = new Promise<AdmissionResult>((resolve) => {
    settleTerminal = resolve;
  });

  const settle = (result: AdmissionResult) => {
    if (!isSettled) {
      isSettled = true;
      if (settleTerminal) {
        const fn = settleTerminal;
        settleTerminal = null;
        try {
          fn(result);
        } catch {}
      }
      if (timerId !== null && timerId !== undefined) {
        try {
          clearTimeoutFn(timerId);
        } catch {}
        timerId = null;
      }
      if (externalSignal && listenerInstalled && abortHandler) {
        try {
          removeEventListenerFn(externalSignal, "abort", abortHandler);
        } catch {}
        listenerInstalled = false;
        abortHandler = null;
      }
      try {
        internalAbortController.abort();
      } catch {}
    }
  };

  abortHandler = () => {
    settle({ status: "cancelled" });
  };

  // Safe timer and listener setup
  let setupFailed = false;
  if (externalSignal) {
    listenerInstalled = true;
    try {
      addEventListenerFn(externalSignal, "abort", abortHandler, { once: true });
    } catch {
      setupFailed = true;
      if (abortHandler) {
        try {
          removeEventListenerFn(externalSignal, "abort", abortHandler);
        } catch {}
        listenerInstalled = false;
        abortHandler = null;
      }
    }
    // If synchronous listener callback settled during addEventListenerFn
    if (isSettled && listenerInstalled && abortHandler) {
      try {
        removeEventListenerFn(externalSignal, "abort", abortHandler);
      } catch {}
      listenerInstalled = false;
      abortHandler = null;
    }
  }

  if (!setupFailed && !isSettled && !externalSignal?.aborted) {
    let scheduledTimer: ReturnType<typeof setTimeout> | null | undefined = undefined;
    try {
      scheduledTimer = setTimeoutFn(() => {
        if (externalSignal?.aborted) {
          settle({ status: "cancelled" });
        } else {
          settle({ status: "retryable" });
        }
      }, deadlineMs);
    } catch {
      setupFailed = true;
    }

    if (scheduledTimer === null || scheduledTimer === undefined) {
      setupFailed = true;
    } else if (isSettled) {
      try {
        clearTimeoutFn(scheduledTimer);
      } catch {}
    } else {
      timerId = scheduledTimer;
    }
  }

  if (setupFailed || isSettled || externalSignal?.aborted) {
    if (externalSignal?.aborted) {
      settle({ status: "cancelled" });
    } else {
      settle({ status: "retryable" });
    }
    return terminalPromise;
  }

  (async () => {
    try {
      let isTrusted = false;
      try {
        isTrusted = Boolean(isTrustedDestination(targetUrl));
      } catch {
        settle({ status: "retryable" });
        return;
      }

      if (isSettled || externalSignal?.aborted) {
        if (externalSignal?.aborted) settle({ status: "cancelled" });
        return;
      }

      if (!isTrusted) {
        settle({ status: "retryable" });
        return;
      }

      let token: string | null = null;
      try {
        token = await tokenProvider();
      } catch {
        settle({ status: "retryable" });
        return;
      }

      if (isSettled || externalSignal?.aborted) {
        if (externalSignal?.aborted) settle({ status: "cancelled" });
        return;
      }

      if (!token) {
        settle({ status: "retryable" });
        return;
      }

      let response: Response;
      try {
        response = await fetchFn(targetUrl, {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/json",
          },
          redirect: "error",
          cache: "no-store",
          signal: internalAbortController.signal,
        });
      } catch {
        settle({ status: "retryable" });
        return;
      }

      if (isSettled || externalSignal?.aborted) {
        if (externalSignal?.aborted) settle({ status: "cancelled" });
        return;
      }

      let rawStatus: unknown = undefined;
      try {
        rawStatus = response.status;
      } catch {
        settle({ status: "retryable" });
        return;
      }

      // Fence immediately after reading response.status and BEFORE classifying 401/403/2xx
      if (isSettled || externalSignal?.aborted) {
        if (externalSignal?.aborted) settle({ status: "cancelled" });
        return;
      }

      if (
        typeof rawStatus !== "number" ||
        !Number.isSafeInteger(rawStatus) ||
        rawStatus < 100 ||
        rawStatus > 599
      ) {
        settle({ status: "retryable" });
        return;
      }

      const statusVal = rawStatus;

      if (statusVal >= 300 && statusVal < 400) {
        settle({ status: "retryable" });
        return;
      }

      if (statusVal === 401 || statusVal === 403) {
        settle({ status: "denied", statusCode: statusVal });
        return;
      }

      if (statusVal < 200 || statusVal >= 300) {
        settle({ status: "retryable" });
        return;
      }

      if (isSettled || externalSignal?.aborted) {
        if (externalSignal?.aborted) settle({ status: "cancelled" });
        return;
      }

      let data: unknown;
      try {
        data = await response.json();
      } catch {
        settle({ status: "retryable" });
        return;
      }

      if (isSettled || externalSignal?.aborted) {
        if (externalSignal?.aborted) settle({ status: "cancelled" });
        return;
      }

      if (!data || typeof data !== "object" || Array.isArray(data)) {
        settle({ status: "retryable" });
        return;
      }

      const proto = Object.getPrototypeOf(data);
      if (proto !== Object.prototype && proto !== null) {
        settle({ status: "retryable" });
        return;
      }

      const descUid = Object.getOwnPropertyDescriptor(data, "uid");
      const descEmail = Object.getOwnPropertyDescriptor(data, "email");
      const descOrgId = Object.getOwnPropertyDescriptor(data, "org_id");
      if (
        !descUid ||
        typeof descUid.value !== "string" ||
        !descEmail ||
        typeof descEmail.value !== "string" ||
        !descOrgId ||
        typeof descOrgId.value !== "string"
      ) {
        settle({ status: "retryable" });
        return;
      }

      const rawUid = descUid.value;
      const rawEmail = descEmail.value;
      const rawOrgId = descOrgId.value;

      if (!isCleanAscii(rawUid) || !isCleanAscii(rawOrgId) || !isValidEmail(rawEmail)) {
        settle({ status: "retryable" });
        return;
      }

      if (rawUid !== expectedUid || rawEmail.toLowerCase() !== expectedEmail.toLowerCase()) {
        settle({ status: "retryable" });
        return;
      }

      if (isSettled) return;

      settle({
        status: "admitted",
        principal: {
          uid: rawUid,
          email: rawEmail.toLowerCase(),
          org_id: rawOrgId,
        },
      });
    } catch {
      settle({ status: "retryable" });
    }
  })().catch(() => {
    settle({ status: "retryable" });
  });

  return await terminalPromise;
}
