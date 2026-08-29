"use client";

import type { RuntimeConfig } from "@/lib/runtimeConfig";
export const IAP_AUTH_TERMINAL_EVENT = "tars:iap-auth-terminal";
export const IAP_KILL_EVENT = "tars:iap-kill-switch";
export const IAP_POLICY_CLOSE_CODE = 4003;
export const IAP_EXPIRY_CLOSE_CODE = 4001;
export const IAP_LOGOUT_MAX_WAIT_MS = 5000;

let terminalEventEmitted = false;

export function isIapTerminalHttpStatus(status: number): boolean {
  return status === 401 || status === 403;
}

export interface IapProfile {
  uid: string;
  email: string;
  org_id: string;
}

export interface IapFetch {
  (input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
}

export interface IapLogoutLifecycleHooks {
  cleanup: () => void;
  navigate?: (url: string) => void;
  maxWaitMs?: number;
}

export function buildIapBootstrapUrl(config: Pick<RuntimeConfig, "apiOrigin">): string {
  return `${config.apiOrigin}/api/auth/bootstrap`;
}

export function buildIapSignOutUrl(config: Pick<RuntimeConfig, "apiOrigin">): string {
  const url = new URL(`${config.apiOrigin}/`);
  url.searchParams.set("gcp-iap-mode", "GCIP_SIGNOUT");
  return url.toString();
}

export function isIapTerminalClose(code: number, reason = ""): boolean {
  if (code === IAP_EXPIRY_CLOSE_CODE) return false;
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

export function emitIapTerminalAuthEvent(): boolean {
  if (terminalEventEmitted) return false;
  terminalEventEmitted = true;
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(IAP_AUTH_TERMINAL_EVENT));
  }
  return true;
}

export function resetIapTerminalAuthEvent(): void {
  terminalEventEmitted = false;
}

export async function fetchIapAdmission(
  config: Pick<RuntimeConfig, "apiOrigin">,
  fetcher: IapFetch = fetch,
  signal?: AbortSignal,
): Promise<IapProfile | null> {
  const response = await fetcher(`${config.apiOrigin}/api/me`, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    signal,
  });
  if (!response.ok) return null;
  const profile = (await response.json()) as Partial<IapProfile>;
  if (
    typeof profile.uid !== "string" ||
    typeof profile.email !== "string" ||
    typeof profile.org_id !== "string"
  ) {
    return null;
  }
  return { uid: profile.uid, email: profile.email, org_id: profile.org_id };
}

export async function performIapLogout(
  config: Pick<RuntimeConfig, "apiOrigin">,
  fetcher: IapFetch = fetch,
): Promise<void> {
  return runIapLogoutLifecycle(config, { cleanup: () => {} }, fetcher);
}

export async function runIapLogoutLifecycle(
  config: Pick<RuntimeConfig, "apiOrigin">,
  hooks: IapLogoutLifecycleHooks,
  fetcher: IapFetch = fetch,
): Promise<void> {
  // Cleanup is deliberately synchronous and happens before either network
  // logout or provider navigation, including when the request rejects.
  hooks.cleanup();
  const controller = new AbortController();
  const maxWaitMs = hooks.maxWaitMs ?? IAP_LOGOUT_MAX_WAIT_MS;
  let timer: ReturnType<typeof setTimeout> | null = null;
  try {
    let request: Promise<Response>;
    try {
      // Invoke the fetcher synchronously so cleanup is observably first and
      // callers can begin the bounded round-trip in the same turn.
      request = fetcher(`${config.apiOrigin}/api/auth/logout`, {
        method: "POST",
        credentials: "include",
        signal: controller.signal,
      });
    } catch (error) {
      request = Promise.reject(error);
    }
    const timeout = new Promise<"timeout">((resolve) => {
      timer = setTimeout(() => resolve("timeout"), maxWaitMs);
    });
    await Promise.race([request, timeout]);
  } finally {
    controller.abort();
    if (timer !== null) clearTimeout(timer);
    const navigate =
      hooks.navigate ??
      ((url: string) => {
        if (typeof window !== "undefined") window.location.assign(url);
      });
    navigate(buildIapSignOutUrl(config));
  }
}
