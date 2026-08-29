"use client";

/** Deterministic lifecycle decisions shared by the IAP React hooks. */

export const IAP_AUTH_TERMINAL_EVENT = "tars:iap-auth-terminal";
export const IAP_POLICY_CLOSE_CODE = 4003;
export const IAP_EXPIRY_CLOSE_CODE = 4001;
export const IAP_LOGOUT_MAX_WAIT_MS = 5000;

export interface LifecycleGenerationController {
  current: () => number;
  begin: () => number;
  invalidate: () => number;
  isCurrent: (generation: number) => boolean;
}

export interface LifecycleTimerApi<T = ReturnType<typeof setTimeout>> {
  setTimeout: (callback: () => void, delay: number) => T;
  clearTimeout: (handle: T) => void;
}

const defaultLifecycleTimerApi: LifecycleTimerApi = {
  setTimeout: (callback, delay) => setTimeout(callback, delay),
  clearTimeout: (handle) => clearTimeout(handle),
};

export function createLifecycleGenerationController(
  initialGeneration = 0,
): LifecycleGenerationController {
  let generation = initialGeneration;
  const advance = () => {
    generation += 1;
    return generation;
  };
  return {
    current: () => generation,
    begin: advance,
    invalidate: advance,
    isCurrent: (candidate) => candidate === generation,
  };
}

let terminalEventEmitted = false;

export function isIapTerminalHttpStatus(status: number): boolean {
  return status === 401 || status === 403;
}

export function iapHttpResponseDisposition(
  status: number,
): "terminal" | "retryable" {
  return isIapTerminalHttpStatus(status) ? "terminal" : "retryable";
}

/** Apply the one-shot terminal transition at every authenticated IAP HTTP boundary. */
export function emitIapHttpTerminalIfNeeded(
  status: number,
  iapEnabled: boolean,
  emit: () => void,
): boolean {
  if (!iapEnabled || iapHttpResponseDisposition(status) !== "terminal") {
    return false;
  }
  emit();
  return true;
}

export function boundedRetryDelay(
  delay: number,
  maxDelay = 30_000,
): number {
  return Math.min(Math.max(1, delay) * 2, maxDelay);
}

/** Commit a resource only while its async owner is still current. */
export function commitIfCurrent<T>(
  resource: T,
  isCurrent: () => boolean,
  dispose: (resource: T) => void,
): T | null {
  if (isCurrent()) return resource;
  dispose(resource);
  return null;
}

/** Schedule a retry that becomes inert if its attempt is terminal/stale. */
export function scheduleLifecycleRetry<T = ReturnType<typeof setTimeout>>(
  isCurrent: () => boolean,
  delay: number,
  retry: () => void,
  timerApi: LifecycleTimerApi<T> = defaultLifecycleTimerApi as unknown as LifecycleTimerApi<T>,
): T | null {
  if (!isCurrent()) return null;
  return timerApi.setTimeout(() => {
    if (isCurrent()) retry();
  }, delay);
}

export function cancelLifecycleRetry<T>(
  handle: T | null,
  timerApi: LifecycleTimerApi<T> = defaultLifecycleTimerApi as unknown as LifecycleTimerApi<T>,
): void {
  if (handle !== null) timerApi.clearTimeout(handle);
}

export async function commitAsyncResource<T>(
  resource: T,
  isCurrent: () => boolean,
  dispose: (resource: T) => void | Promise<void>,
): Promise<T | null> {
  if (isCurrent()) return resource;
  await dispose(resource);
  return null;
}

export function isIapTerminalClose(code: number, reason = ""): boolean {
  if (code === IAP_EXPIRY_CLOSE_CODE) return false;
  // 1008 is the server's handshake/policy rejection, including a kill or
  // revocation discovered before the socket can be fully established.
  return (
    code === IAP_POLICY_CLOSE_CODE ||
    code === 1008 ||
    /(?:kill|revok|logout|terminal|policy|forbidden|auth[_ -]?(?:denied|revoked|closed))/i.test(reason)
  );
}

export function isIapReconnectableClose(code: number, reason = ""): boolean {
  return code === IAP_EXPIRY_CLOSE_CODE || !isIapTerminalClose(code, reason);
}

export function isIapAttemptCurrent(
  attemptGeneration: number,
  currentGeneration: number,
  attemptSessionId: string,
  currentSessionId: string | null,
): boolean {
  return (
    attemptGeneration === currentGeneration &&
    attemptSessionId === currentSessionId
  );
}

export function lifecycleAttemptIsCurrent(
  generation: number,
  currentGeneration: number,
  sessionId: string,
  currentSessionId: string | null,
): boolean {
  return isIapAttemptCurrent(
    generation,
    currentGeneration,
    sessionId,
    currentSessionId,
  );
}

export function iapAdmissionAttemptIsCurrent(
  attemptGeneration: number,
  currentGeneration: number,
  signal: AbortSignal,
): boolean {
  return !signal.aborted && attemptGeneration === currentGeneration;
}

export function emitIapTerminalAuthEvent(): boolean {
  if (terminalEventEmitted) return false;
  terminalEventEmitted = true;
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(IAP_AUTH_TERMINAL_EVENT));
  }
  return true;
}

/** Allow one terminal transition for the next freshly admitted principal. */
export function resetIapTerminalAuthEvent(): void {
  terminalEventEmitted = false;
}
