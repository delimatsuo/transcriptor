import { test, expect, type BrowserContext, type Page, type WebSocketRoute } from "@playwright/test";

export interface NetworkRecorders {
  recordedRequests: string[];
  failedRequests: string[];
  finishedRequests: string[];
  recordedWebSockets: string[];
  recordedPopups: Page[];
  pageErrors: Error[];
}

export interface GuardDelegateSeams {
  pageClose?: (page: Page) => Promise<void>;
  wsClose?: (ws: WebSocketRoute, details: { code: number; reason: string; side?: "server" | "client" }) => Promise<void>;
  wsOnClose?: (ws: WebSocketRoute, cb: () => void, side?: "server" | "client") => void;
  unrouteAll?: (ctx: BrowserContext) => Promise<void>;
  socketCloseTimeoutMs?: number;
}

export interface NetworkGuardDiagnostics {
  serverRoutesCount: number;
  serverCloseAttemptedCount: number;
  serverCloseFulfilledCount: number;
  serverCloseRejectedCount: number;
  serverObservedClosedCount: number;
  serverCloseTimedOutCount: number;
  clientRoutesCount: number;
  clientCloseAttemptedCount: number;
  clientCloseFulfilledCount: number;
  clientCloseRejectedCount: number;
  clientObservedClosedCount: number;
  clientCloseTimedOutCount: number;
  popupsCount: number;
  openPopupsCountAtDispose: number;
  popupCloseAttemptedCount: number;
  popupCloseFulfilledCount: number;
  popupCloseRejectedCount: number;
  closedPopupsCount: number;
  initialPageListenerDetached: boolean;
  popupListenersDetachedCount: number;
  retainedPageErrorHandlersCount: number;
  contextListenersDetached: boolean;
  unrouteAttempted: boolean;
  httpRouteRemoved: boolean;
  isDisposed: boolean;
}

export interface NetworkGuardController {
  dispose: () => Promise<void>;
  getDiagnostics: () => NetworkGuardDiagnostics;
}

interface EventEmitterLike {
  listenerCount?(event: string): number;
  listeners?(event: string): Function[];
}

function hasListener(emitter: unknown, event: string, handler: Function): boolean {
  try {
    const ee = emitter as EventEmitterLike;
    if (typeof ee.listeners === "function") {
      return ee.listeners(event).includes(handler);
    }
    if (typeof ee.listenerCount === "function") {
      return ee.listenerCount(event) > 0;
    }
  } catch {}
  return false;
}

const pristineNetworkGuardError = new Error("Network guard teardown failure");
export const pristineNetworkGuardOwnKeys = Object.freeze(Reflect.ownKeys(pristineNetworkGuardError));
export const pristineStackBaseline: PropertyDescriptor = Object.getOwnPropertyDescriptor(pristineNetworkGuardError, "stack")!;
if (!pristineStackBaseline) {
  throw new Error("Pristine Network-guard Error stack descriptor must exist");
}
export const pristineMsgBaseline: PropertyDescriptor = Object.getOwnPropertyDescriptor(pristineNetworkGuardError, "message")!;
if (!pristineMsgBaseline) {
  throw new Error("Pristine Network-guard Error message descriptor must exist");
}

export function validateAndExtractLiveTeardownStackDescriptor(
  candidate: unknown,
  pristineStack: PropertyDescriptor = pristineStackBaseline,
  pristineMsg: PropertyDescriptor = pristineMsgBaseline
): PropertyDescriptor {
  expect(typeof candidate === "object" && candidate !== null).toBe(true);
  const stackDesc = Object.getOwnPropertyDescriptor(candidate, "stack");
  expect(stackDesc).toBeDefined();
  const msgDesc = Object.getOwnPropertyDescriptor(candidate, "message");
  expect(msgDesc).toBeDefined();

  const isNativeAccessor = "get" in pristineStack || "set" in pristineStack;
  const isCandidateAccessor = "get" in stackDesc! || "set" in stackDesc!;
  expect(isCandidateAccessor).toBe(isNativeAccessor);

  if (isNativeAccessor) {
    expect(stackDesc!.enumerable).toBe(pristineStack.enumerable);
    expect(stackDesc!.configurable).toBe(pristineStack.configurable);
  } else {
    expect(stackDesc!.writable).toBe(pristineStack.writable);
    expect(stackDesc!.enumerable).toBe(pristineStack.enumerable);
    expect(stackDesc!.configurable).toBe(pristineStack.configurable);
  }

  expect("get" in msgDesc! || "set" in msgDesc!).toBe(false);
  expect(msgDesc!.writable).toBe(pristineMsg.writable);
  expect(msgDesc!.enumerable).toBe(pristineMsg.enumerable);
  expect(msgDesc!.configurable).toBe(pristineMsg.configurable);

  return stackDesc!;
}

export function assertExactNetworkGuardTeardownError(
  err: unknown,
  forbiddenSentinels: string[],
  expectedOwnKeys: readonly (string | symbol)[],
  expectedStackDesc: PropertyDescriptor,
  expectedMsgDesc: PropertyDescriptor
) {
  expect(typeof err === "object" && err !== null).toBe(true);
  expect(Object.getPrototypeOf(err)).toBe(Error.prototype);

  const ownKeys = Reflect.ownKeys(err as object);
  expect(ownKeys.length).toBe(expectedOwnKeys.length);

  const capturedDescriptors = Object.getOwnPropertyDescriptors(err as object);

  for (const key of ownKeys) {
    expect(typeof key).toBe("string");
    expect(expectedOwnKeys.includes(key)).toBe(true);

    const desc = capturedDescriptors[key as string];
    expect(desc).toBeDefined();

    if (key === "message") {
      expect(desc!.get).toBeUndefined();
      expect(desc!.set).toBeUndefined();
      expect(typeof desc!.value).toBe("string");
      expect(desc!.value).toBe("Network guard teardown failure");
      expect(desc!.writable).toBe(expectedMsgDesc.writable);
      expect(desc!.enumerable).toBe(expectedMsgDesc.enumerable);
      expect(desc!.configurable).toBe(expectedMsgDesc.configurable);
      for (const sentinel of forbiddenSentinels) {
        expect(!desc!.value.includes(sentinel)).toBe(true);
      }
    } else if (key === "stack") {
      const isExpectedAccessor = "get" in expectedStackDesc || "set" in expectedStackDesc;
      const isCapturedAccessor = "get" in desc! || "set" in desc!;

      if (isExpectedAccessor) {
        expect(isCapturedAccessor).toBe(true);
        expect(desc!.get).toBe(expectedStackDesc.get);
        expect(desc!.set).toBe(expectedStackDesc.set);
        expect(desc!.enumerable).toBe(expectedStackDesc.enumerable);
        expect(desc!.configurable).toBe(expectedStackDesc.configurable);
      } else {
        expect(!isCapturedAccessor).toBe(true);
        expect(typeof desc!.value).toBe("string");
        expect(desc!.writable).toBe(expectedStackDesc.writable);
        expect(desc!.enumerable).toBe(expectedStackDesc.enumerable);
        expect(desc!.configurable).toBe(expectedStackDesc.configurable);
        for (const sentinel of forbiddenSentinels) {
          expect(!(desc!.value as string).includes(sentinel)).toBe(true);
        }
      }
    } else {
      expect(desc!.get).toBeUndefined();
      expect(desc!.set).toBeUndefined();
      expect(typeof desc!.value).toBe("string");
    }
  }

  // Inherited name (no own property name) and descriptor validation
  expect(capturedDescriptors.name).toBeUndefined();
  expect(capturedDescriptors.cause).toBeUndefined();
  expect(capturedDescriptors.details).toBeUndefined();
  expect(capturedDescriptors.metadata).toBeUndefined();
  expect(capturedDescriptors.nestedObject).toBeUndefined();
}

export interface CommonCleanupExpectations {
  popupsCount: number;
  openPopupsCountAtDispose: number;
  popupCloseAttemptedCount: number;
  popupCloseFulfilledCount: number;
  popupCloseRejectedCount: number;
  closedPopupsCount: number;
  popupListenersDetachedCount: number;
  httpRouteRemoved: boolean;
}

export function assertCommonCleanupDiagnostics(
  diag: NetworkGuardDiagnostics,
  expected: CommonCleanupExpectations
) {
  expect(diag.isDisposed).toBe(true);
  expect(diag.initialPageListenerDetached).toBe(true);
  expect(diag.contextListenersDetached).toBe(true);
  expect(diag.retainedPageErrorHandlersCount).toBe(0);
  expect(diag.unrouteAttempted).toBe(true);
  expect(diag.httpRouteRemoved).toBe(expected.httpRouteRemoved);
  expect(diag.popupsCount).toBe(expected.popupsCount);
  expect(diag.openPopupsCountAtDispose).toBe(expected.openPopupsCountAtDispose);
  expect(diag.popupCloseAttemptedCount).toBe(expected.popupCloseAttemptedCount);
  expect(diag.popupCloseFulfilledCount).toBe(expected.popupCloseFulfilledCount);
  expect(diag.popupCloseRejectedCount).toBe(expected.popupCloseRejectedCount);
  expect(diag.closedPopupsCount).toBe(expected.closedPopupsCount);
  expect(diag.popupListenersDetachedCount).toBe(expected.popupListenersDetachedCount);
}

export interface SocketSideVector {
  routesCount: number;
  attemptedCount: number;
  fulfilledCount: number;
  rejectedCount: number;
  observedClosedCount: number;
  timedOutCount: number;
}

export function assertSocketVector(
  diag: NetworkGuardDiagnostics,
  expected: { server: SocketSideVector; client: SocketSideVector }
) {
  expect({
    routesCount: diag.serverRoutesCount,
    attemptedCount: diag.serverCloseAttemptedCount,
    fulfilledCount: diag.serverCloseFulfilledCount,
    rejectedCount: diag.serverCloseRejectedCount,
    observedClosedCount: diag.serverObservedClosedCount,
    timedOutCount: diag.serverCloseTimedOutCount,
  }).toEqual(expected.server);

  expect({
    routesCount: diag.clientRoutesCount,
    attemptedCount: diag.clientCloseAttemptedCount,
    fulfilledCount: diag.clientCloseFulfilledCount,
    rejectedCount: diag.clientCloseRejectedCount,
    observedClosedCount: diag.clientObservedClosedCount,
    timedOutCount: diag.clientCloseTimedOutCount,
  }).toEqual(expected.client);
}

export interface QuiescenceEvidence {
  nonce: string;
  state: "fired";
  pageStart: number;
  pageEnd: number;
  pageElapsedMs: number;
  hostElapsedMs: number;
}

export async function observeBoundedQuiescence(
  page: Page,
  expectedNonce: string,
  minQuiescenceMs: number = 350,
  maxWatchdogMs: number = 5000,
): Promise<QuiescenceEvidence> {
  const hostStart = Date.now();
  let watchdogTimer: NodeJS.Timeout | null = null;
  const watchdogPromise = new Promise<never>((_, reject) => {
    watchdogTimer = setTimeout(() => {
      reject(new Error(`Host quiescence watchdog timed out after ${maxWatchdogMs}ms`));
    }, maxWatchdogMs);
  });

  try {
    const pageOutcome = await Promise.race([
      page.evaluate(
        async ({ nonce, targetMs }) => {
          const w = window as any;
          if (!w.__task07_quiescence || w.__task07_quiescence.nonce !== nonce || w.__task07_quiescence.state !== "armed") {
            throw new Error(`Invalid quiescence nonce or state in page: expected armed with ${nonce}`);
          }
          const pageStart = Date.now();
          await new Promise((resolve) => setTimeout(resolve, targetMs));
          const pageEnd = Date.now();
          const pageElapsedMs = pageEnd - pageStart;
          w.__task07_quiescence = {
            nonce,
            state: "fired",
            pageStart,
            pageEnd,
            pageElapsedMs,
          };
          return w.__task07_quiescence;
        },
        { nonce: expectedNonce, targetMs: minQuiescenceMs }
      ),
      watchdogPromise,
    ]);

    const hostEnd = Date.now();
    const hostElapsedMs = hostEnd - hostStart;

    if (
      !pageOutcome ||
      pageOutcome.nonce !== expectedNonce ||
      pageOutcome.state !== "fired" ||
      typeof pageOutcome.pageElapsedMs !== "number"
    ) {
      throw new Error("Quiescence evidence verification failed");
    }

    return {
      nonce: pageOutcome.nonce,
      state: "fired",
      pageStart: pageOutcome.pageStart,
      pageEnd: pageOutcome.pageEnd,
      pageElapsedMs: pageOutcome.pageElapsedMs,
      hostElapsedMs,
    };
  } finally {
    if (watchdogTimer) {
      clearTimeout(watchdogTimer);
      watchdogTimer = null;
    }
  }
}

export async function installAuthSourceNetworkGuards(
  context: BrowserContext,
  initialPage: Page,
  recorders: NetworkRecorders,
  delegates?: GuardDelegateSeams,
): Promise<NetworkGuardController> {
  const pageClose = delegates?.pageClose ?? ((p: Page) => p.close());
  const wsClose = delegates?.wsClose ?? ((ws: WebSocketRoute, details: { code: number; reason: string; side?: "server" | "client" }) => ws.close(details));
  const wsOnClose = delegates?.wsOnClose ?? ((ws: WebSocketRoute, cb: () => void, _side?: "server" | "client") => ws.onClose(cb));
  const unrouteAll = delegates?.unrouteAll ?? ((ctx: BrowserContext) => ctx.unrouteAll({ behavior: "ignoreErrors" }));
  const socketCloseTimeoutMs = delegates?.socketCloseTimeoutMs ?? 5000;

  const serverWsRoutes: WebSocketRoute[] = [];
  const clientWsRoutes: WebSocketRoute[] = [];
  const closedServerSockets = new Set<WebSocketRoute>();
  const closedClientSockets = new Set<WebSocketRoute>();
  const serverWsClosedByCaller = new Set<WebSocketRoute>();
  const clientWsClosedByCaller = new Set<WebSocketRoute>();

  let serverCloseAttemptedCount = 0;
  let serverCloseFulfilledCount = 0;
  let serverCloseRejectedCount = 0;
  let serverCloseTimedOutCount = 0;

  let clientCloseAttemptedCount = 0;
  let clientCloseFulfilledCount = 0;
  let clientCloseRejectedCount = 0;
  let clientCloseTimedOutCount = 0;

  let openPopupsCountAtDispose = 0;
  let popupCloseAttemptedCount = 0;
  let popupCloseFulfilledCount = 0;
  let popupCloseRejectedCount = 0;
  let popupListenersDetachedCount = 0;
  let unrouteAttempted = false;
  let httpRouteRemoved = false;
  let isDisposed = false;
  let disposePromise: Promise<void> | null = null;

  const pageErrorHandlers = new Map<Page, (err: Error) => void>();

  const onInitialPageError = (err: Error) => {
    recorders.pageErrors.push(err);
  };
  pageErrorHandlers.set(initialPage, onInitialPageError);
  initialPage.on("pageerror", onInitialPageError);

  const onRequestFailed = (req: any) => {
    recorders.failedRequests.push(req.url());
  };
  const onRequestFinished = (req: any) => {
    recorders.finishedRequests.push(req.url());
  };

  context.on("requestfailed", onRequestFailed);
  context.on("requestfinished", onRequestFinished);

  const onPage = (page: Page) => {
    recorders.recordedPopups.push(page);
    const handler = (err: Error) => {
      recorders.pageErrors.push(err);
    };
    pageErrorHandlers.set(page, handler);
    page.on("pageerror", handler);
  };
  context.on("page", onPage);

  await context.route(/.*/, async (route) => {
    const request = route.request();
    const rawUrl = request.url();
    let parsed: URL;
    try {
      parsed = new URL(rawUrl);
    } catch {
      recorders.recordedRequests.push(rawUrl);
      await route.abort("blockedbyclient");
      return;
    }

    const isAllowedHttp =
      parsed.protocol === "http:" &&
      (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost") &&
      parsed.port === "3105" &&
      (parsed.pathname === "/" || parsed.pathname.startsWith("/_next/"));

    if (isAllowedHttp) {
      await route.continue();
      return;
    }

    // Any loopback /api, loopback /favicon.ico, non-loopback, or unexpected scheme/path is forbidden
    recorders.recordedRequests.push(rawUrl);
    await route.abort("blockedbyclient");
  });

  await context.routeWebSocket(/.*/, async (wsRoute) => {
    const rawUrl = wsRoute.url();
    let parsed: URL;
    try {
      parsed = new URL(rawUrl);
    } catch {
      recorders.recordedWebSockets.push(rawUrl);
      await wsRoute.close({ code: 1008, reason: "Forbidden socket" });
      return;
    }

    if (isDisposed) {
      recorders.recordedWebSockets.push(rawUrl);
      await wsRoute.close({ code: 1008, reason: "Forbidden socket post-dispose" });
      return;
    }

    const isAllowedHmr =
      parsed.protocol === "ws:" &&
      (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost") &&
      parsed.port === "3105" &&
      (parsed.pathname === "/_next/hmr" || parsed.pathname === "/_next/webpack-hmr");

    if (isAllowedHmr) {
      const serverRoute = wsRoute.connectToServer();
      wsOnClose(serverRoute, () => {
        if (serverWsClosedByCaller.has(serverRoute)) {
          closedServerSockets.add(serverRoute);
        }
      }, "server");
      wsOnClose(wsRoute, () => {
        if (clientWsClosedByCaller.has(wsRoute)) {
          closedClientSockets.add(wsRoute);
        }
      }, "client");
      serverWsRoutes.push(serverRoute);
      clientWsRoutes.push(wsRoute);
      return;
    }

    // All application sockets are recorded and closed with policy code 1008
    recorders.recordedWebSockets.push(rawUrl);
    await wsRoute.close({ code: 1008, reason: "Forbidden socket" });
  });

  const dispose = (): Promise<void> => {
    if (disposePromise) {
      return disposePromise;
    }

    disposePromise = (async () => {
      isDisposed = true;
      let hasCleanupError = false;

      // 1. Detach initialPage listener
      try {
        initialPage.off("pageerror", onInitialPageError);
      } catch {
        hasCleanupError = true;
      }

      // 2. Detach popup listeners (separate try/catch, always call off even if Page is closed)
      for (const [popup, handler] of pageErrorHandlers) {
        if (popup !== initialPage) {
          try {
            popup.off("pageerror", handler);
          } catch {
            hasCleanupError = true;
          }
          if (!hasListener(popup, "pageerror", handler)) {
            popupListenersDetachedCount++;
          }
        }
      }
      pageErrorHandlers.clear();

      // 3. Detach context listeners in separate attempts
      try {
        context.off("page", onPage);
      } catch {
        hasCleanupError = true;
      }
      try {
        context.off("requestfailed", onRequestFailed);
      } catch {
        hasCleanupError = true;
      }
      try {
        context.off("requestfinished", onRequestFinished);
      } catch {
        hasCleanupError = true;
      }

      // 4. Close popups if still open (execute pageClose delegate)
      for (const popup of recorders.recordedPopups) {
        try {
          if (!popup.isClosed()) {
            openPopupsCountAtDispose++;
            popupCloseAttemptedCount++;
            await pageClose(popup);
            popupCloseFulfilledCount++;
          }
        } catch {
          popupCloseRejectedCount++;
          hasCleanupError = true;
        }
      }

      // 5. Close HMR WebSocket routes (both server and client, tracking each side separately)
      const serverClosePromises = serverWsRoutes.map(async (sWs) => {
        serverCloseAttemptedCount++;
        try {
          const wrappedSWs = Object.create(sWs, {
            close: {
              value: async (opt?: any) => {
                serverWsClosedByCaller.add(sWs);
                return sWs.close(opt);
              },
              writable: true,
              configurable: true,
            },
          });
          await wsClose(wrappedSWs, { code: 1000, reason: "Test teardown", side: "server" });
          serverCloseFulfilledCount++;
        } catch {
          serverCloseRejectedCount++;
          hasCleanupError = true;
        }
      });

      const clientClosePromises = clientWsRoutes.map(async (cWs) => {
        clientCloseAttemptedCount++;
        try {
          const wrappedCWs = Object.create(cWs, {
            close: {
              value: async (opt?: any) => {
                clientWsClosedByCaller.add(cWs);
                return cWs.close(opt);
              },
              writable: true,
              configurable: true,
            },
          });
          await wsClose(wrappedCWs, { code: 1000, reason: "Test teardown", side: "client" });
          clientCloseFulfilledCount++;
        } catch {
          clientCloseRejectedCount++;
          hasCleanupError = true;
        }
      });

      await Promise.allSettled([...serverClosePromises, ...clientClosePromises]);

      // 6. Bounded wait for actual onClose events per side (poll fulfilled only)
      const pollStart = Date.now();
      while (closedServerSockets.size < serverCloseFulfilledCount || closedClientSockets.size < clientCloseFulfilledCount) {
        if (Date.now() - pollStart > socketCloseTimeoutMs) {
          if (closedServerSockets.size < serverCloseFulfilledCount) {
            serverCloseTimedOutCount = serverCloseFulfilledCount - closedServerSockets.size;
          }
          if (closedClientSockets.size < clientCloseFulfilledCount) {
            clientCloseTimedOutCount = clientCloseFulfilledCount - closedClientSockets.size;
          }
          hasCleanupError = true;
          break;
        }
        await new Promise((r) => setTimeout(r, 20));
      }

      // 7. Unroute HTTP (always runs finally-style)
      unrouteAttempted = true;
      try {
        await unrouteAll(context);
        httpRouteRemoved = true;
      } catch {
        hasCleanupError = true;
      }

      // If any stage collected an error, reject cached work with exact fixed message
      if (hasCleanupError) {
        throw new Error("Network guard teardown failure");
      }
    })();

    return disposePromise;
  };

  return {
    dispose,
    getDiagnostics: () => ({
      serverRoutesCount: serverWsRoutes.length,
      serverCloseAttemptedCount,
      serverCloseFulfilledCount,
      serverCloseRejectedCount,
      serverObservedClosedCount: closedServerSockets.size,
      serverCloseTimedOutCount,
      clientRoutesCount: clientWsRoutes.length,
      clientCloseAttemptedCount,
      clientCloseFulfilledCount,
      clientCloseRejectedCount,
      clientObservedClosedCount: closedClientSockets.size,
      clientCloseTimedOutCount,
      popupsCount: recorders.recordedPopups.length,
      openPopupsCountAtDispose,
      popupCloseAttemptedCount,
      popupCloseFulfilledCount,
      popupCloseRejectedCount,
      closedPopupsCount: recorders.recordedPopups.filter((p) => p.isClosed()).length,
      initialPageListenerDetached: !hasListener(initialPage, "pageerror", onInitialPageError),
      popupListenersDetachedCount,
      retainedPageErrorHandlersCount: pageErrorHandlers.size,
      contextListenersDetached:
        !hasListener(context, "page", onPage) &&
        !hasListener(context, "requestfailed", onRequestFailed) &&
        !hasListener(context, "requestfinished", onRequestFinished),
      unrouteAttempted,
      httpRouteRemoved,
      isDisposed,
    }),
  };
}

test.describe("Auth Source Readiness Offline E2E", () => {
  test("offline configuration error prevents data leakage and network calls", async ({
    page,
    context,
  }) => {
    const recorders: NetworkRecorders = {
      recordedRequests: [],
      failedRequests: [],
      finishedRequests: [],
      recordedWebSockets: [],
      recordedPopups: [],
      pageErrors: [],
    };

    const guard = await installAuthSourceNetworkGuards(context, page, recorders);
    try {
      await page.goto("/", { waitUntil: "domcontentloaded" });

      // Observe deterministic neutral client-hydrated marker driven by useAuth lifecycle
      await expect(page.locator("[data-client-hydrated='true']")).toBeAttached();

      // Assert error alert displays expected configuration message
      const errorBox = page.getByRole("alert").filter({ hasText: "Configuração de autenticação" });
      await expect(errorBox).toBeVisible();
      await expect(errorBox).toContainText("Configuração de autenticação inválida ou ausente");

      // Assert absence of real interactive buttons / markers
      await expect(page.getByRole("button", { name: "Entrar com Google" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Tentar novamente" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Iniciar sessão" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Iniciar reunião" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Iniciar entrevista" })).toHaveCount(0);
      await expect(page.getByLabel("Entrevistas recentes")).toHaveCount(0);
      await expect(page.locator("text=Canal do Candidato:")).toHaveCount(0);
      await expect(page.locator("[aria-label^='Conta autenticada:']")).toHaveCount(0);
      await expect(page.locator("textarea")).toHaveCount(0);

      // Arm unique nonce independently in page state
      const expectedNonce = `nonce-missing-config-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      await page.evaluate((nonce) => {
        (window as any).__task07_quiescence = { nonce, state: "armed" };
      }, expectedNonce);

      // Bounded quiescence window of at least 300 ms before zero-effect assertions and disposition
      const quiescenceEvidence = await observeBoundedQuiescence(page, expectedNonce, 350);
      expect(quiescenceEvidence.nonce).toBe(expectedNonce);
      expect(quiescenceEvidence.state).toBe("fired");
      expect(quiescenceEvidence.pageElapsedMs).toBeGreaterThanOrEqual(300);
      expect(quiescenceEvidence.hostElapsedMs).toBeGreaterThanOrEqual(300);

      // Double-verify page state directly
      const verifiedPageState = await page.evaluate(() => (window as any).__task07_quiescence);
      expect(verifiedPageState.nonce).toBe(expectedNonce);
      expect(verifiedPageState.state).toBe("fired");

      // Verify no non-HMR WebSocket connections occurred
      expect(recorders.recordedWebSockets).toEqual([]);

      // Verify no popups or page errors occurred
      expect(recorders.recordedPopups).toEqual([]);
      expect(recorders.pageErrors).toEqual([]);

      // Verify no forbidden HTTP requests occurred
      expect(recorders.recordedRequests).toEqual([]);
    } finally {
      await expect.poll(() => guard.getDiagnostics().serverRoutesCount).toBeGreaterThan(0);
      await expect.poll(() => guard.getDiagnostics().clientRoutesCount).toBeGreaterThan(0);

      // Assert concurrent identity of cached disposal promise
      const p1 = guard.dispose();
      const p2 = guard.dispose();
      expect(p1).toBe(p2);
      await p1;

      // Sequential repeat p3 identity and deep-equal diagnostics snapshot
      const diagBeforeP3 = guard.getDiagnostics();
      const p3 = guard.dispose();
      expect(p3).toBe(p1);
      await p3;
      const diagAfterP3 = guard.getDiagnostics();
      expect(diagAfterP3).toEqual(diagBeforeP3);

      const diag = diagAfterP3;
      expect(diag.isDisposed).toBe(true);
      expect(diag.serverRoutesCount).toBeGreaterThan(0);
      expect(diag.clientRoutesCount).toBeGreaterThan(0);

      const expectedNormalVectors = {
        server: {
          routesCount: diag.serverRoutesCount,
          attemptedCount: diag.serverRoutesCount,
          fulfilledCount: diag.serverRoutesCount,
          rejectedCount: 0,
          observedClosedCount: diag.serverRoutesCount,
          timedOutCount: 0,
        },
        client: {
          routesCount: diag.clientRoutesCount,
          attemptedCount: diag.clientRoutesCount,
          fulfilledCount: diag.clientRoutesCount,
          rejectedCount: 0,
          observedClosedCount: diag.clientRoutesCount,
          timedOutCount: 0,
        },
      };
      assertSocketVector(diag, expectedNormalVectors);

      assertCommonCleanupDiagnostics(diag, {
        popupsCount: 0,
        openPopupsCountAtDispose: 0,
        popupCloseAttemptedCount: 0,
        popupCloseFulfilledCount: 0,
        popupCloseRejectedCount: 0,
        closedPopupsCount: 0,
        popupListenersDetachedCount: 0,
        httpRouteRemoved: true,
      });

      // Safe loopback-only probe post-unroute proves route removal without hitting external network
      const unrouteProbeUrl = "http://127.0.0.1:3105/favicon.ico?task07_unroute=1";
      const postUnrouteFetch = await page.evaluate(async (url) => {
        try {
          const res = await fetch(url, { cache: "no-store" });
          return { ok: true, status: res.status };
        } catch (err: unknown) {
          return { ok: false, error: String(err) };
        }
      }, unrouteProbeUrl);
      expect(postUnrouteFetch.ok).toBe(true);
      expect(recorders.recordedRequests).not.toContain(unrouteProbeUrl);
    }
  });

  test("controlled guard-strength intercepts forbidden HTTP, WebSocket, and popup attempts", async ({
    page,
    context,
  }) => {
    const recorders: NetworkRecorders = {
      recordedRequests: [],
      failedRequests: [],
      finishedRequests: [],
      recordedWebSockets: [],
      recordedPopups: [],
      pageErrors: [],
    };

    let assetPopupPage: Page | null = null;
    let authPopupPage: Page | null = null;
    let openLoopbackPopupPage: Page | null = null;

    const guard = await installAuthSourceNetworkGuards(context, page, recorders);
    try {
      await page.goto("/", { waitUntil: "domcontentloaded" });

      // Observe deterministic neutral client-hydrated marker driven by useAuth lifecycle
      await expect(page.locator("[data-client-hydrated='true']")).toBeAttached();

      // 1. Attempt synthetic external asset popup with explicit short timeout
      const assetPopupPromise = context.waitForEvent("page", { timeout: 5000 });
      await page.evaluate(() => {
        window.open("https://asset.invalid/favicon.ico?probe=1", "_blank");
      });
      assetPopupPage = await assetPopupPromise;
      await expect.poll(() => recorders.recordedRequests).toContain("https://asset.invalid/favicon.ico?probe=1");
      await expect.poll(() => recorders.failedRequests).toContain("https://asset.invalid/favicon.ico?probe=1");
      expect(recorders.finishedRequests).not.toContain("https://asset.invalid/favicon.ico?probe=1");

      // Attempt loopback /api request and verify it specifically fails with requestfailed
      const apiFetchOutcome = await page.evaluate(async () => {
        try {
          await fetch("http://127.0.0.1:3105/api/me");
          return { ok: true, error: null };
        } catch (err: unknown) {
          return { ok: false, error: String(err) };
        }
      });
      expect(apiFetchOutcome.ok).toBe(false);
      await expect.poll(() => recorders.failedRequests).toContain("http://127.0.0.1:3105/api/me");

      // Attempt synthetic application WebSocket and verify real 1008 close code
      const appWsOutcome = await page.evaluate(() => {
        return new Promise<{ closed: boolean; code?: number }>((resolve) => {
          try {
            const ws = new WebSocket("ws://127.0.0.1:3105/ws/test-session");
            const timer = setTimeout(() => { try { ws.close(); } catch {} resolve({ closed: false }); }, 2000);
            ws.onclose = (ev) => { clearTimeout(timer); resolve({ closed: true, code: ev.code }); };
          } catch {
            resolve({ closed: false });
          }
        });
      });
      expect(appWsOutcome.closed).toBe(true);
      expect(appWsOutcome.code).toBe(1008);

      // Attempt non-loopback WebSocket and verify real 1008 close code
      const nonLoopbackWsOutcome = await page.evaluate(() => {
        return new Promise<{ closed: boolean; code?: number }>((resolve) => {
          try {
            const ws = new WebSocket("wss://socket.invalid/");
            const timer = setTimeout(() => { try { ws.close(); } catch {} resolve({ closed: false }); }, 2000);
            ws.onclose = (ev) => { clearTimeout(timer); resolve({ closed: true, code: ev.code }); };
          } catch {
            resolve({ closed: false });
          }
        });
      });
      expect(nonLoopbackWsOutcome.closed).toBe(true);
      expect(nonLoopbackWsOutcome.code).toBe(1008);

      // 2. Attempt synthetic external auth popup with explicit short timeout
      const authPopupPromise = context.waitForEvent("page", { timeout: 5000 });
      await page.evaluate(() => {
        window.open("https://popup.invalid/auth", "_blank");
      });
      authPopupPage = await authPopupPromise;
      await expect.poll(() => recorders.recordedRequests).toContain("https://popup.invalid/auth");
      await expect.poll(() => recorders.failedRequests).toContain("https://popup.invalid/auth");
      expect(recorders.finishedRequests).not.toContain("https://popup.invalid/auth");

      // 3. Open a guard-owned loopback/about:blank popup that remains OPEN through dispose
      const openPopupPromise = context.waitForEvent("page", { timeout: 5000 });
      await page.evaluate(() => {
        window.open("about:blank", "_blank");
      });
      openLoopbackPopupPage = await openPopupPromise;
      expect(openLoopbackPopupPage.isClosed()).toBe(false);

      // Verify each forbidden attempt was intercepted before contact with bounded polling
      await expect.poll(() => recorders.recordedRequests).toContain("https://asset.invalid/favicon.ico?probe=1");
      await expect.poll(() => recorders.recordedRequests).toContain("http://127.0.0.1:3105/api/me");
      await expect.poll(() => recorders.recordedRequests).toContain("https://popup.invalid/auth");
      await expect.poll(() => recorders.recordedWebSockets).toContain("ws://127.0.0.1:3105/ws/test-session");
      await expect.poll(() => recorders.recordedWebSockets).toContain("wss://socket.invalid/");
      expect(recorders.recordedPopups.length).toBe(3);

      // 4. Schedule a deterministic delayed loopback-only request inside the quiescence window
      const delayedQuiescenceUrl = `http://127.0.0.1:3105/api/task07-quiescence-${Date.now()}`;
      await page.evaluate((url) => {
        setTimeout(() => {
          fetch(url).catch(() => {});
        }, 50);
      }, delayedQuiescenceUrl);

      // Arm unique nonce in page state for test 2
      const test2Nonce = `nonce-test2-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      await page.evaluate((nonce) => {
        (window as any).__task07_quiescence = { nonce, state: "armed" };
      }, test2Nonce);

      const qOutcome = await observeBoundedQuiescence(page, test2Nonce, 350);
      expect(qOutcome.nonce).toBe(test2Nonce);
      expect(qOutcome.state).toBe("fired");
      expect(qOutcome.pageElapsedMs).toBeGreaterThanOrEqual(300);
      expect(qOutcome.hostElapsedMs).toBeGreaterThanOrEqual(300);
      expect(recorders.recordedRequests).toContain(delayedQuiescenceUrl);
      expect(recorders.failedRequests).toContain(delayedQuiescenceUrl);

      // 5. Preclose first two popups before dispose; leave openLoopbackPopupPage OPEN to prove dispose closes open popups
      await assetPopupPage.close().catch(() => {});
      await authPopupPage.close().catch(() => {});
      expect(assetPopupPage.isClosed()).toBe(true);
      expect(authPopupPage.isClosed()).toBe(true);
      expect(openLoopbackPopupPage.isClosed()).toBe(false);
    } finally {
      await expect.poll(() => guard.getDiagnostics().serverRoutesCount).toBeGreaterThan(0);
      await expect.poll(() => guard.getDiagnostics().clientRoutesCount).toBeGreaterThan(0);

      // Assert concurrent identity of cached disposal promise
      const p1 = guard.dispose();
      const p2 = guard.dispose();
      expect(p1).toBe(p2);
      await p1;

      // Sequential repeat p3 identity and deep-equal diagnostics snapshot
      const diagBeforeP3 = guard.getDiagnostics();
      const p3 = guard.dispose();
      expect(p3).toBe(p1);
      await p3;
      const diagAfterP3 = guard.getDiagnostics();
      expect(diagAfterP3).toEqual(diagBeforeP3);

      const diag = diagAfterP3;
      expect(diag.isDisposed).toBe(true);
      expect(diag.serverRoutesCount).toBeGreaterThan(0);
      expect(diag.clientRoutesCount).toBeGreaterThan(0);

      const expectedNormalVectors = {
        server: {
          routesCount: diag.serverRoutesCount,
          attemptedCount: diag.serverRoutesCount,
          fulfilledCount: diag.serverRoutesCount,
          rejectedCount: 0,
          observedClosedCount: diag.serverRoutesCount,
          timedOutCount: 0,
        },
        client: {
          routesCount: diag.clientRoutesCount,
          attemptedCount: diag.clientRoutesCount,
          fulfilledCount: diag.clientRoutesCount,
          rejectedCount: 0,
          observedClosedCount: diag.clientRoutesCount,
          timedOutCount: 0,
        },
      };
      assertSocketVector(diag, expectedNormalVectors);

      assertCommonCleanupDiagnostics(diag, {
        popupsCount: 3,
        openPopupsCountAtDispose: 1,
        popupCloseAttemptedCount: 1,
        popupCloseFulfilledCount: 1,
        popupCloseRejectedCount: 0,
        closedPopupsCount: 3,
        popupListenersDetachedCount: 3,
        httpRouteRemoved: true,
      });

      // Post-dispose loopback WS probe receives real 1008
      const postDisposeWsOutcome = await page.evaluate(() => {
        return new Promise<{ closed: boolean; code?: number }>((resolve) => {
          try {
            const ws = new WebSocket("ws://127.0.0.1:3105/ws/post-dispose-probe");
            const timer = setTimeout(() => { try { ws.close(); } catch {} resolve({ closed: false }); }, 2000);
            ws.onclose = (ev) => { clearTimeout(timer); resolve({ closed: true, code: ev.code }); };
          } catch {
            resolve({ closed: false });
          }
        });
      });
      expect(postDisposeWsOutcome.closed).toBe(true);
      expect(postDisposeWsOutcome.code).toBe(1008);
    }
  });

  test("table-driven teardown failure rows in isolated contexts reject with cached error and complete unrelated cleanup", async ({
    browser,
  }) => {
    const trackedContexts: BrowserContext[] = [];

    async function withWatchdog<T>(promise: Promise<T>, timeoutMs: number, label: string): Promise<T> {
      let timer: NodeJS.Timeout | null = null;
      const timeoutPromise = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(`Timeout after ${timeoutMs}ms waiting for ${label}`)), timeoutMs);
      });
      try {
        return await Promise.race([promise, timeoutPromise]);
      } finally {
        if (timer !== null) {
          clearTimeout(timer);
        }
      }
    }

    try {
      // Row a: Open popup close() rejects via injected pageClose delegate
      {
        const ctxA = await browser.newContext({ baseURL: "http://127.0.0.1:3105" });
        trackedContexts.push(ctxA);
        const pageA = await ctxA.newPage();
        const recordersA: NetworkRecorders = {
          recordedRequests: [],
          failedRequests: [],
          finishedRequests: [],
          recordedWebSockets: [],
          recordedPopups: [],
          pageErrors: [],
        };

        let openPopupA: Page | null = null;
        const guardA = await installAuthSourceNetworkGuards(ctxA, pageA, recordersA, {
          pageClose: async () => {
            throw new Error("POPUP_CLOSE_SENTINEL");
          },
        });

        try {
          const popupPromise = ctxA.waitForEvent("page", { timeout: 5000 });
          await pageA.evaluate(() => {
            window.open("about:blank", "_blank");
          });
          openPopupA = await popupPromise;
          expect(openPopupA.isClosed()).toBe(false);

          const p1 = guardA.dispose();
          const p2 = guardA.dispose();
          expect(p1).toBe(p2);

          let caught1: any;
          try {
            await p1;
          } catch (e) {
            caught1 = e;
          }

          const diagBeforeP3 = guardA.getDiagnostics();
          const p3 = guardA.dispose();
          expect(p3).toBe(p1);

          let caught2: any;
          try {
            await p3;
          } catch (e) {
            caught2 = e;
          }
          expect(caught1).toBe(caught2);
          const diagAfterP3 = guardA.getDiagnostics();
          expect(diagAfterP3).toEqual(diagBeforeP3);

          const liveStackA = validateAndExtractLiveTeardownStackDescriptor(caught1);
          assertExactNetworkGuardTeardownError(caught1, ["POPUP_CLOSE_SENTINEL"], pristineNetworkGuardOwnKeys, liveStackA, pristineMsgBaseline);

          const diag = diagAfterP3;
          assertCommonCleanupDiagnostics(diag, {
            popupsCount: 1,
            openPopupsCountAtDispose: 1,
            popupCloseAttemptedCount: 1,
            popupCloseFulfilledCount: 0,
            popupCloseRejectedCount: 1,
            closedPopupsCount: 0,
            popupListenersDetachedCount: 1,
            httpRouteRemoved: true,
          });
        } finally {
          if (openPopupA && !openPopupA.isClosed()) {
            await openPopupA.close().catch(() => {});
          }
          await ctxA.close().catch(() => {});
        }
      }

      // Row b1: Server WebSocket close() rejects via injected wsClose delegate
      {
        const ctxB1 = await browser.newContext({ baseURL: "http://127.0.0.1:3105" });
        trackedContexts.push(ctxB1);
        const pageB1 = await ctxB1.newPage();
        const recordersB1: NetworkRecorders = {
          recordedRequests: [],
          failedRequests: [],
          finishedRequests: [],
          recordedWebSockets: [],
          recordedPopups: [],
          pageErrors: [],
        };

        const guardB1 = await installAuthSourceNetworkGuards(ctxB1, pageB1, recordersB1, {
          wsClose: async (ws, details) => {
            if (details.side === "server") {
              throw new Error("SERVER_WS_REJECT_SENTINEL");
            }
            await ws.close(details);
          },
        });

        try {
          await pageB1.goto("/", { waitUntil: "domcontentloaded" });
          await expect.poll(() => guardB1.getDiagnostics().serverRoutesCount).toBeGreaterThan(0);
          await expect.poll(() => guardB1.getDiagnostics().clientRoutesCount).toBeGreaterThan(0);

          const p1 = guardB1.dispose();
          const p2 = guardB1.dispose();
          expect(p1).toBe(p2);

          let caught1: any;
          try {
            await p1;
          } catch (e) {
            caught1 = e;
          }

          const diagBeforeP3 = guardB1.getDiagnostics();
          const p3 = guardB1.dispose();
          expect(p3).toBe(p1);

          let caught2: any;
          try {
            await p3;
          } catch (e) {
            caught2 = e;
          }
          expect(caught1).toBe(caught2);
          const diagAfterP3 = guardB1.getDiagnostics();
          expect(diagAfterP3).toEqual(diagBeforeP3);

          const liveStackB1 = validateAndExtractLiveTeardownStackDescriptor(caught1);
          assertExactNetworkGuardTeardownError(caught1, ["SERVER_WS_REJECT_SENTINEL"], pristineNetworkGuardOwnKeys, liveStackB1, pristineMsgBaseline);

          const diag = diagAfterP3;
          expect(diag.isDisposed).toBe(true);
          expect(diag.serverRoutesCount).toBeGreaterThan(0);
          expect(diag.clientRoutesCount).toBeGreaterThan(0);

          const sR = diag.serverRoutesCount;
          const cR = diag.clientRoutesCount;
          const expectedVectors = {
            server: {
              routesCount: sR,
              attemptedCount: sR,
              fulfilledCount: 0,
              rejectedCount: sR,
              observedClosedCount: 0,
              timedOutCount: 0,
            },
            client: {
              routesCount: cR,
              attemptedCount: cR,
              fulfilledCount: cR,
              rejectedCount: 0,
              observedClosedCount: cR,
              timedOutCount: 0,
            },
          };
          assertSocketVector(diag, expectedVectors);

          assertCommonCleanupDiagnostics(diag, {
            popupsCount: 0,
            openPopupsCountAtDispose: 0,
            popupCloseAttemptedCount: 0,
            popupCloseFulfilledCount: 0,
            popupCloseRejectedCount: 0,
            closedPopupsCount: 0,
            popupListenersDetachedCount: 0,
            httpRouteRemoved: true,
          });
        } finally {
          await ctxB1.close().catch(() => {});
        }
      }

      // Row b2: Client WebSocket close() rejects via injected wsClose delegate
      {
        const ctxB2 = await browser.newContext({ baseURL: "http://127.0.0.1:3105" });
        trackedContexts.push(ctxB2);
        const pageB2 = await ctxB2.newPage();
        const recordersB2: NetworkRecorders = {
          recordedRequests: [],
          failedRequests: [],
          finishedRequests: [],
          recordedWebSockets: [],
          recordedPopups: [],
          pageErrors: [],
        };

        const guardB2 = await installAuthSourceNetworkGuards(ctxB2, pageB2, recordersB2, {
          wsClose: async (ws, details) => {
            if (details.side === "client") {
              throw new Error("CLIENT_WS_REJECT_SENTINEL");
            }
            await ws.close(details);
          },
        });

        try {
          await pageB2.goto("/", { waitUntil: "domcontentloaded" });
          await expect.poll(() => guardB2.getDiagnostics().serverRoutesCount).toBeGreaterThan(0);
          await expect.poll(() => guardB2.getDiagnostics().clientRoutesCount).toBeGreaterThan(0);

          const p1 = guardB2.dispose();
          const p2 = guardB2.dispose();
          expect(p1).toBe(p2);

          let caught1: any;
          try {
            await p1;
          } catch (e) {
            caught1 = e;
          }

          const diagBeforeP3 = guardB2.getDiagnostics();
          const p3 = guardB2.dispose();
          expect(p3).toBe(p1);

          let caught2: any;
          try {
            await p3;
          } catch (e) {
            caught2 = e;
          }
          expect(caught1).toBe(caught2);
          const diagAfterP3 = guardB2.getDiagnostics();
          expect(diagAfterP3).toEqual(diagBeforeP3);

          const liveStackB2 = validateAndExtractLiveTeardownStackDescriptor(caught1);
          assertExactNetworkGuardTeardownError(caught1, ["CLIENT_WS_REJECT_SENTINEL"], pristineNetworkGuardOwnKeys, liveStackB2, pristineMsgBaseline);

          const diag = diagAfterP3;
          expect(diag.isDisposed).toBe(true);
          expect(diag.serverRoutesCount).toBeGreaterThan(0);
          expect(diag.clientRoutesCount).toBeGreaterThan(0);

          const sR = diag.serverRoutesCount;
          const cR = diag.clientRoutesCount;
          const expectedVectors = {
            server: {
              routesCount: sR,
              attemptedCount: sR,
              fulfilledCount: sR,
              rejectedCount: 0,
              observedClosedCount: sR,
              timedOutCount: 0,
            },
            client: {
              routesCount: cR,
              attemptedCount: cR,
              fulfilledCount: 0,
              rejectedCount: cR,
              observedClosedCount: 0,
              timedOutCount: 0,
            },
          };
          assertSocketVector(diag, expectedVectors);

          assertCommonCleanupDiagnostics(diag, {
            popupsCount: 0,
            openPopupsCountAtDispose: 0,
            popupCloseAttemptedCount: 0,
            popupCloseFulfilledCount: 0,
            popupCloseRejectedCount: 0,
            closedPopupsCount: 0,
            popupListenersDetachedCount: 0,
            httpRouteRemoved: true,
          });
        } finally {
          await ctxB2.close().catch(() => {});
        }
      }

      // Row c1: Server-only resolved no-op with client real normal close
      {
        const ctxC1 = await browser.newContext({ baseURL: "http://127.0.0.1:3105" });
        trackedContexts.push(ctxC1);
        const pageC1 = await ctxC1.newPage();
        const recordersC1: NetworkRecorders = {
          recordedRequests: [],
          failedRequests: [],
          finishedRequests: [],
          recordedWebSockets: [],
          recordedPopups: [],
          pageErrors: [],
        };

        const guardC1 = await installAuthSourceNetworkGuards(ctxC1, pageC1, recordersC1, {
          socketCloseTimeoutMs: 50,
          wsClose: async (ws, details) => {
            if (details.side === "client") {
              await ws.close(details);
            }
            // Server side does no-op
          },
        });

        try {
          await pageC1.goto("/", { waitUntil: "domcontentloaded" });
          await expect.poll(() => guardC1.getDiagnostics().serverRoutesCount).toBeGreaterThan(0);
          await expect.poll(() => guardC1.getDiagnostics().clientRoutesCount).toBeGreaterThan(0);

          const p1 = guardC1.dispose();
          const p2 = guardC1.dispose();
          expect(p1).toBe(p2);

          let caught1: any;
          try {
            await p1;
          } catch (e) {
            caught1 = e;
          }

          const diagBeforeP3 = guardC1.getDiagnostics();
          const p3 = guardC1.dispose();
          expect(p3).toBe(p1);

          let caught2: any;
          try {
            await p3;
          } catch (e) {
            caught2 = e;
          }
          expect(caught1).toBe(caught2);
          const diagAfterP3 = guardC1.getDiagnostics();
          expect(diagAfterP3).toEqual(diagBeforeP3);

          const liveStackC1 = validateAndExtractLiveTeardownStackDescriptor(caught1);
          assertExactNetworkGuardTeardownError(caught1, [], pristineNetworkGuardOwnKeys, liveStackC1, pristineMsgBaseline);

          const diag = diagAfterP3;
          expect(diag.isDisposed).toBe(true);
          expect(diag.serverRoutesCount).toBeGreaterThan(0);
          expect(diag.clientRoutesCount).toBeGreaterThan(0);

          const sR = diag.serverRoutesCount;
          const cR = diag.clientRoutesCount;
          const expectedVectors = {
            server: {
              routesCount: sR,
              attemptedCount: sR,
              fulfilledCount: sR,
              rejectedCount: 0,
              observedClosedCount: 0,
              timedOutCount: sR,
            },
            client: {
              routesCount: cR,
              attemptedCount: cR,
              fulfilledCount: cR,
              rejectedCount: 0,
              observedClosedCount: cR,
              timedOutCount: 0,
            },
          };
          assertSocketVector(diag, expectedVectors);

          assertCommonCleanupDiagnostics(diag, {
            popupsCount: 0,
            openPopupsCountAtDispose: 0,
            popupCloseAttemptedCount: 0,
            popupCloseFulfilledCount: 0,
            popupCloseRejectedCount: 0,
            closedPopupsCount: 0,
            popupListenersDetachedCount: 0,
            httpRouteRemoved: true,
          });
        } finally {
          await ctxC1.close().catch(() => {});
        }
      }

      // Row c2: Client-only resolved no-op with server real normal close
      {
        const ctxC2 = await browser.newContext({ baseURL: "http://127.0.0.1:3105" });
        trackedContexts.push(ctxC2);
        const pageC2 = await ctxC2.newPage();
        const recordersC2: NetworkRecorders = {
          recordedRequests: [],
          failedRequests: [],
          finishedRequests: [],
          recordedWebSockets: [],
          recordedPopups: [],
          pageErrors: [],
        };

        const guardC2 = await installAuthSourceNetworkGuards(ctxC2, pageC2, recordersC2, {
          socketCloseTimeoutMs: 50,
          wsClose: async (ws, details) => {
            if (details.side === "server") {
              await ws.close(details);
            }
            // Client side does no-op
          },
        });

        try {
          await pageC2.goto("/", { waitUntil: "domcontentloaded" });
          await expect.poll(() => guardC2.getDiagnostics().serverRoutesCount).toBeGreaterThan(0);
          await expect.poll(() => guardC2.getDiagnostics().clientRoutesCount).toBeGreaterThan(0);

          const p1 = guardC2.dispose();
          const p2 = guardC2.dispose();
          expect(p1).toBe(p2);

          let caught1: any;
          try {
            await p1;
          } catch (e) {
            caught1 = e;
          }

          const diagBeforeP3 = guardC2.getDiagnostics();
          const p3 = guardC2.dispose();
          expect(p3).toBe(p1);

          let caught2: any;
          try {
            await p3;
          } catch (e) {
            caught2 = e;
          }
          expect(caught1).toBe(caught2);
          const diagAfterP3 = guardC2.getDiagnostics();
          expect(diagAfterP3).toEqual(diagBeforeP3);

          const liveStackC2 = validateAndExtractLiveTeardownStackDescriptor(caught1);
          assertExactNetworkGuardTeardownError(caught1, [], pristineNetworkGuardOwnKeys, liveStackC2, pristineMsgBaseline);

          const diag = diagAfterP3;
          expect(diag.isDisposed).toBe(true);
          expect(diag.serverRoutesCount).toBeGreaterThan(0);
          expect(diag.clientRoutesCount).toBeGreaterThan(0);

          const sR = diag.serverRoutesCount;
          const cR = diag.clientRoutesCount;
          const expectedVectors = {
            server: {
              routesCount: sR,
              attemptedCount: sR,
              fulfilledCount: sR,
              rejectedCount: 0,
              observedClosedCount: sR,
              timedOutCount: 0,
            },
            client: {
              routesCount: cR,
              attemptedCount: cR,
              fulfilledCount: cR,
              rejectedCount: 0,
              observedClosedCount: 0,
              timedOutCount: cR,
            },
          };
          assertSocketVector(diag, expectedVectors);

          assertCommonCleanupDiagnostics(diag, {
            popupsCount: 0,
            openPopupsCountAtDispose: 0,
            popupCloseAttemptedCount: 0,
            popupCloseFulfilledCount: 0,
            popupCloseRejectedCount: 0,
            closedPopupsCount: 0,
            popupListenersDetachedCount: 0,
            httpRouteRemoved: true,
          });
        } finally {
          await ctxC2.close().catch(() => {});
        }
      }

      // Row c3: Missing onClose registration on server socket results in server timeout
      {
        const ctxC3 = await browser.newContext({ baseURL: "http://127.0.0.1:3105" });
        trackedContexts.push(ctxC3);
        const pageC3 = await ctxC3.newPage();
        const recordersC3: NetworkRecorders = {
          recordedRequests: [],
          failedRequests: [],
          finishedRequests: [],
          recordedWebSockets: [],
          recordedPopups: [],
          pageErrors: [],
        };

        const guardC3 = await installAuthSourceNetworkGuards(ctxC3, pageC3, recordersC3, {
          socketCloseTimeoutMs: 50,
          wsOnClose: (ws, cb, side) => {
            if (side === "server") {
              // Intentionally do not register server onClose
            } else {
              ws.onClose(cb);
            }
          },
        });

        try {
          await pageC3.goto("/", { waitUntil: "domcontentloaded" });
          await expect.poll(() => guardC3.getDiagnostics().serverRoutesCount).toBeGreaterThan(0);
          await expect.poll(() => guardC3.getDiagnostics().clientRoutesCount).toBeGreaterThan(0);

          const p1 = guardC3.dispose();
          const p2 = guardC3.dispose();
          expect(p1).toBe(p2);

          let caught1: any;
          try {
            await p1;
          } catch (e) {
            caught1 = e;
          }

          const diagBeforeP3 = guardC3.getDiagnostics();
          const p3 = guardC3.dispose();
          expect(p3).toBe(p1);

          let caught2: any;
          try {
            await p3;
          } catch (e) {
            caught2 = e;
          }
          expect(caught1).toBe(caught2);
          const diagAfterP3 = guardC3.getDiagnostics();
          expect(diagAfterP3).toEqual(diagBeforeP3);

          const liveStackC3 = validateAndExtractLiveTeardownStackDescriptor(caught1);
          assertExactNetworkGuardTeardownError(caught1, [], pristineNetworkGuardOwnKeys, liveStackC3, pristineMsgBaseline);

          const diag = diagAfterP3;
          expect(diag.isDisposed).toBe(true);
          expect(diag.serverRoutesCount).toBeGreaterThan(0);
          expect(diag.clientRoutesCount).toBeGreaterThan(0);

          const sR = diag.serverRoutesCount;
          const cR = diag.clientRoutesCount;
          const expectedVectors = {
            server: {
              routesCount: sR,
              attemptedCount: sR,
              fulfilledCount: sR,
              rejectedCount: 0,
              observedClosedCount: 0,
              timedOutCount: sR,
            },
            client: {
              routesCount: cR,
              attemptedCount: cR,
              fulfilledCount: cR,
              rejectedCount: 0,
              observedClosedCount: cR,
              timedOutCount: 0,
            },
          };
          assertSocketVector(diag, expectedVectors);

          assertCommonCleanupDiagnostics(diag, {
            popupsCount: 0,
            openPopupsCountAtDispose: 0,
            popupCloseAttemptedCount: 0,
            popupCloseFulfilledCount: 0,
            popupCloseRejectedCount: 0,
            closedPopupsCount: 0,
            popupListenersDetachedCount: 0,
            httpRouteRemoved: true,
          });
        } finally {
          await ctxC3.close().catch(() => {});
        }
      }

      // Row c4: Missing onClose registration on client socket results in client timeout
      {
        const ctxC4 = await browser.newContext({ baseURL: "http://127.0.0.1:3105" });
        trackedContexts.push(ctxC4);
        const pageC4 = await ctxC4.newPage();
        const recordersC4: NetworkRecorders = {
          recordedRequests: [],
          failedRequests: [],
          finishedRequests: [],
          recordedWebSockets: [],
          recordedPopups: [],
          pageErrors: [],
        };

        const guardC4 = await installAuthSourceNetworkGuards(ctxC4, pageC4, recordersC4, {
          socketCloseTimeoutMs: 50,
          wsOnClose: (ws, cb, side) => {
            if (side === "client") {
              // Intentionally do not register client onClose
            } else {
              ws.onClose(cb);
            }
          },
        });

        try {
          await pageC4.goto("/", { waitUntil: "domcontentloaded" });
          await expect.poll(() => guardC4.getDiagnostics().serverRoutesCount).toBeGreaterThan(0);
          await expect.poll(() => guardC4.getDiagnostics().clientRoutesCount).toBeGreaterThan(0);

          const p1 = guardC4.dispose();
          const p2 = guardC4.dispose();
          expect(p1).toBe(p2);

          let caught1: any;
          try {
            await p1;
          } catch (e) {
            caught1 = e;
          }

          const diagBeforeP3 = guardC4.getDiagnostics();
          const p3 = guardC4.dispose();
          expect(p3).toBe(p1);

          let caught2: any;
          try {
            await p3;
          } catch (e) {
            caught2 = e;
          }
          expect(caught1).toBe(caught2);
          const diagAfterP3 = guardC4.getDiagnostics();
          expect(diagAfterP3).toEqual(diagBeforeP3);

          const liveStackC4 = validateAndExtractLiveTeardownStackDescriptor(caught1);
          assertExactNetworkGuardTeardownError(caught1, [], pristineNetworkGuardOwnKeys, liveStackC4, pristineMsgBaseline);

          const diag = diagAfterP3;
          expect(diag.isDisposed).toBe(true);
          expect(diag.serverRoutesCount).toBeGreaterThan(0);
          expect(diag.clientRoutesCount).toBeGreaterThan(0);

          const sR = diag.serverRoutesCount;
          const cR = diag.clientRoutesCount;
          const expectedVectors = {
            server: {
              routesCount: sR,
              attemptedCount: sR,
              fulfilledCount: sR,
              rejectedCount: 0,
              observedClosedCount: sR,
              timedOutCount: 0,
            },
            client: {
              routesCount: cR,
              attemptedCount: cR,
              fulfilledCount: cR,
              rejectedCount: 0,
              observedClosedCount: 0,
              timedOutCount: cR,
            },
          };
          assertSocketVector(diag, expectedVectors);

          assertCommonCleanupDiagnostics(diag, {
            popupsCount: 0,
            openPopupsCountAtDispose: 0,
            popupCloseAttemptedCount: 0,
            popupCloseFulfilledCount: 0,
            popupCloseRejectedCount: 0,
            closedPopupsCount: 0,
            popupListenersDetachedCount: 0,
            httpRouteRemoved: true,
          });
        } finally {
          await ctxC4.close().catch(() => {});
        }
      }

      // Row d: 5-row bounded causal table executor
      {
        const EXPECTED_DIAGNOSTIC_SCHEMA: Readonly<Record<keyof NetworkGuardDiagnostics, "number" | "boolean">> = Object.freeze({
          serverRoutesCount: "number",
          serverCloseAttemptedCount: "number",
          serverCloseFulfilledCount: "number",
          serverCloseRejectedCount: "number",
          serverObservedClosedCount: "number",
          serverCloseTimedOutCount: "number",
          clientRoutesCount: "number",
          clientCloseAttemptedCount: "number",
          clientCloseFulfilledCount: "number",
          clientCloseRejectedCount: "number",
          clientObservedClosedCount: "number",
          clientCloseTimedOutCount: "number",
          popupsCount: "number",
          openPopupsCountAtDispose: "number",
          popupCloseAttemptedCount: "number",
          popupCloseFulfilledCount: "number",
          popupCloseRejectedCount: "number",
          closedPopupsCount: "number",
          initialPageListenerDetached: "boolean",
          popupListenersDetachedCount: "number",
          retainedPageErrorHandlersCount: "number",
          contextListenersDetached: "boolean",
          unrouteAttempted: "boolean",
          httpRouteRemoved: "boolean",
          isDisposed: "boolean",
        });

        const EXPECTED_DIAGNOSTIC_KEYS = Object.freeze(Object.keys(EXPECTED_DIAGNOSTIC_SCHEMA)) as readonly (keyof NetworkGuardDiagnostics)[];
        const EXPECTED_DIAGNOSTIC_KEYS_SET = Object.freeze(new Set<string>(EXPECTED_DIAGNOSTIC_KEYS));

        interface DiagnosticPropertySnapshot {
          readonly key: string;
          readonly isAccessor: false;
          readonly value: number | boolean;
          readonly writable: boolean;
          readonly enumerable: boolean;
          readonly configurable: boolean;
        }

        type ImmutableDiagnosticSnapshot = readonly DiagnosticPropertySnapshot[];

        function areDiagnosticsEqual(a: unknown, b: unknown): boolean {
          if (typeof a !== "object" || a === null || typeof b !== "object" || b === null) {
            return false;
          }
          const keysA = Reflect.ownKeys(a);
          const keysB = Reflect.ownKeys(b);
          if (keysA.length !== keysB.length) return false;

          const setB = new Set(keysB);
          for (const k of keysA) {
            if (!setB.has(k)) return false;
            const descA = Object.getOwnPropertyDescriptor(a, k);
            const descB = Object.getOwnPropertyDescriptor(b, k);
            if (!descA || !descB) return false;

            const isAccessorA = "get" in descA || "set" in descA;
            const isAccessorB = "get" in descB || "set" in descB;
            if (isAccessorA || isAccessorB) return false;

            if (!Object.is(descA.value, descB.value)) return false;
            if (descA.writable !== descB.writable) return false;
            if (descA.enumerable !== descB.enumerable) return false;
            if (descA.configurable !== descB.configurable) return false;
          }
          return true;
        }

        function createImmutableDiagnosticSnapshot(diag: NetworkGuardDiagnostics): ImmutableDiagnosticSnapshot {
          if (typeof diag !== "object" || diag === null) {
            throw new Error("diagnostics must be a non-null object");
          }
          const keys = Reflect.ownKeys(diag);
          if (keys.length !== EXPECTED_DIAGNOSTIC_KEYS.length) {
            throw new Error(`diagnostics key count ${keys.length} does not match expected schema ${EXPECTED_DIAGNOSTIC_KEYS.length}`);
          }
          const seenKeys = new Set<string>();
          const entries: DiagnosticPropertySnapshot[] = [];
          for (const k of keys) {
            if (typeof k !== "string" || !EXPECTED_DIAGNOSTIC_KEYS_SET.has(k)) {
              throw new Error(`unexpected diagnostic key: ${String(k)}`);
            }
            if (seenKeys.has(k)) {
              throw new Error(`duplicate diagnostic key: ${k}`);
            }
            seenKeys.add(k);

            const expectedType = EXPECTED_DIAGNOSTIC_SCHEMA[k as keyof NetworkGuardDiagnostics];
            const desc = Object.getOwnPropertyDescriptor(diag, k);
            if (!desc) throw new Error(`missing property descriptor for key: ${k}`);
            if ("get" in desc || "set" in desc) {
              throw new Error(`diagnostic key ${k} must not have accessor descriptor`);
            }
            if (typeof desc.value !== expectedType) {
              throw new Error(`diagnostic key ${k} value type mismatch: expected ${expectedType}, got ${typeof desc.value}`);
            }
            if (expectedType === "number" && !Number.isFinite(desc.value)) {
              throw new Error(`diagnostic key ${k} number value must be finite`);
            }
            entries.push(
              Object.freeze({
                key: k,
                isAccessor: false,
                value: desc.value,
                writable: desc.writable ?? true,
                enumerable: desc.enumerable ?? true,
                configurable: desc.configurable ?? true,
              })
            );
          }
          for (const expKey of EXPECTED_DIAGNOSTIC_KEYS) {
            if (!seenKeys.has(expKey)) {
              throw new Error(`missing required diagnostic key: ${expKey}`);
            }
          }
          return Object.freeze(entries);
        }

        function validateSnapshotAgainstRaw(snapshot: ImmutableDiagnosticSnapshot, rawDiag: NetworkGuardDiagnostics): void {
          if (!Object.isFrozen(snapshot)) throw new Error("snapshot array must be frozen");
          if (snapshot.length !== EXPECTED_DIAGNOSTIC_KEYS.length) {
            throw new Error(`snapshot length ${snapshot.length} does not match expected ${EXPECTED_DIAGNOSTIC_KEYS.length}`);
          }
          const rawKeys = Reflect.ownKeys(rawDiag);
          if (rawKeys.length !== EXPECTED_DIAGNOSTIC_KEYS.length) {
            throw new Error("raw diagnostics key count mismatch");
          }
          const seenSnapshotKeys = new Set<string>();
          for (const entry of snapshot) {
            if (!Object.isFrozen(entry)) throw new Error(`snapshot entry ${String(entry.key)} must be frozen`);
            if (typeof entry.key !== "string" || !EXPECTED_DIAGNOSTIC_KEYS_SET.has(entry.key)) {
              throw new Error(`unexpected snapshot key: ${String(entry.key)}`);
            }
            if (seenSnapshotKeys.has(entry.key)) {
              throw new Error(`duplicate snapshot key: ${entry.key}`);
            }
            seenSnapshotKeys.add(entry.key);

            const rawDesc = Object.getOwnPropertyDescriptor(rawDiag, entry.key);
            if (!rawDesc) throw new Error(`missing raw descriptor for ${entry.key}`);
            if ("get" in rawDesc || "set" in rawDesc) throw new Error(`raw diagnostic ${entry.key} must not be accessor`);
            if (entry.isAccessor) throw new Error(`snapshot ${entry.key} must not be accessor`);
            if (!Object.is(entry.value, rawDesc.value)) throw new Error(`snapshot value mismatch for ${entry.key}`);
            if (entry.writable !== rawDesc.writable) throw new Error(`snapshot writable mismatch for ${entry.key}`);
            if (entry.enumerable !== rawDesc.enumerable) throw new Error(`snapshot enumerable mismatch for ${entry.key}`);
            if (entry.configurable !== rawDesc.configurable) throw new Error(`snapshot configurable mismatch for ${entry.key}`);
          }
          for (const expKey of EXPECTED_DIAGNOSTIC_KEYS) {
            if (!seenSnapshotKeys.has(expKey)) {
              throw new Error(`missing required snapshot key: ${expKey}`);
            }
          }
        }

        function areImmutableDiagnosticsEqual(a: ImmutableDiagnosticSnapshot, b: ImmutableDiagnosticSnapshot): boolean {
          if (a.length !== b.length) return false;
          if (a.length !== EXPECTED_DIAGNOSTIC_KEYS.length) return false;
          const seenA = new Set<string>();
          const seenB = new Set<string>();
          for (let i = 0; i < a.length; i++) {
            const pA = a[i];
            if (typeof pA.key !== "string" || !EXPECTED_DIAGNOSTIC_KEYS_SET.has(pA.key) || seenA.has(pA.key)) return false;
            seenA.add(pA.key);

            const pB = b.find((p) => p.key === pA.key);
            if (!pB) return false;
            if (typeof pB.key !== "string" || !EXPECTED_DIAGNOSTIC_KEYS_SET.has(pB.key) || seenB.has(pB.key)) return false;
            seenB.add(pB.key);

            if (pA.isAccessor || pB.isAccessor) return false;
            if (!Object.is(pA.value, pB.value)) return false;
            if (pA.writable !== pB.writable) return false;
            if (pA.enumerable !== pB.enumerable) return false;
            if (pA.configurable !== pB.configurable) return false;
          }
          if (seenA.size !== EXPECTED_DIAGNOSTIC_KEYS.length || seenB.size !== EXPECTED_DIAGNOSTIC_KEYS.length) return false;
          return true;
        }

        // Section D.2: Local causal probes for areDiagnosticsEqual
        const baselineDiagProbe: NetworkGuardDiagnostics = {
          serverRoutesCount: 1,
          serverCloseAttemptedCount: 1,
          serverCloseFulfilledCount: 1,
          serverCloseRejectedCount: 0,
          serverObservedClosedCount: 1,
          serverCloseTimedOutCount: 0,
          clientRoutesCount: 1,
          clientCloseAttemptedCount: 1,
          clientCloseFulfilledCount: 1,
          clientCloseRejectedCount: 0,
          clientObservedClosedCount: 1,
          clientCloseTimedOutCount: 0,
          popupsCount: 0,
          openPopupsCountAtDispose: 0,
          popupCloseAttemptedCount: 0,
          popupCloseFulfilledCount: 0,
          popupCloseRejectedCount: 0,
          closedPopupsCount: 0,
          initialPageListenerDetached: true,
          popupListenersDetachedCount: 0,
          retainedPageErrorHandlersCount: 0,
          contextListenersDetached: true,
          unrouteAttempted: true,
          httpRouteRemoved: true,
          isDisposed: true,
        };

        const cloneDiagProbe = Object.defineProperties({}, Object.getOwnPropertyDescriptors(baselineDiagProbe));
        expect(areDiagnosticsEqual(baselineDiagProbe, cloneDiagProbe)).toBe(true);

        const diagWritableFlip = Object.defineProperties({}, Object.getOwnPropertyDescriptors(baselineDiagProbe));
        const dW = Object.getOwnPropertyDescriptor(diagWritableFlip, "serverRoutesCount")!;
        Object.defineProperty(diagWritableFlip, "serverRoutesCount", { ...dW, writable: !dW.writable });
        expect(areDiagnosticsEqual(baselineDiagProbe, diagWritableFlip)).toBe(false);

        const diagEnumFlip = Object.defineProperties({}, Object.getOwnPropertyDescriptors(baselineDiagProbe));
        const dE = Object.getOwnPropertyDescriptor(diagEnumFlip, "serverRoutesCount")!;
        Object.defineProperty(diagEnumFlip, "serverRoutesCount", { ...dE, enumerable: !dE.enumerable });
        expect(areDiagnosticsEqual(baselineDiagProbe, diagEnumFlip)).toBe(false);

        const diagConfigFlip = Object.defineProperties({}, Object.getOwnPropertyDescriptors(baselineDiagProbe));
        const dC = Object.getOwnPropertyDescriptor(diagConfigFlip, "serverRoutesCount")!;
        Object.defineProperty(diagConfigFlip, "serverRoutesCount", { ...dC, configurable: !dC.configurable });
        expect(areDiagnosticsEqual(baselineDiagProbe, diagConfigFlip)).toBe(false);

        let diagGetterCount = 0;
        const diagAccessor = Object.defineProperties({}, Object.getOwnPropertyDescriptors(baselineDiagProbe));
        Object.defineProperty(diagAccessor, "serverRoutesCount", {
          get: () => {
            diagGetterCount++;
            return 1;
          },
          enumerable: true,
          configurable: true,
        });
        expect(areDiagnosticsEqual(baselineDiagProbe, diagAccessor)).toBe(false);
        expect(diagGetterCount).toBe(0);

        const diagSymbolExtra = Object.defineProperties({}, Object.getOwnPropertyDescriptors(baselineDiagProbe));
        (diagSymbolExtra as any)[Symbol("extra")] = 123;
        expect(areDiagnosticsEqual(baselineDiagProbe, diagSymbolExtra)).toBe(false);

        // Local self-probes for createImmutableDiagnosticSnapshot and validateSnapshotAgainstRaw
        const validFullSnapshot = createImmutableDiagnosticSnapshot(baselineDiagProbe);
        validateSnapshotAgainstRaw(validFullSnapshot, baselineDiagProbe);

        // 1. Frozen empty fails
        expect(() => validateSnapshotAgainstRaw(Object.freeze([] as any), baselineDiagProbe)).toThrow();

        // 2. Dropped key fails
        const droppedKeyDiag = { ...baselineDiagProbe };
        delete (droppedKeyDiag as any).isDisposed;
        expect(() => createImmutableDiagnosticSnapshot(droppedKeyDiag as any)).toThrow();

        // 3. Duplicate key replacing another at expected length fails
        const dupKeySnapshot = validFullSnapshot.map((entry, idx) =>
          idx === validFullSnapshot.length - 1 ? Object.freeze({ ...validFullSnapshot[0] }) : entry
        );
        let dupProbeErr: unknown = null;
        try {
          validateSnapshotAgainstRaw(Object.freeze(dupKeySnapshot), baselineDiagProbe);
        } catch (err: unknown) {
          dupProbeErr = err;
        }
        expect(dupProbeErr instanceof Error).toBe(true);
        expect((dupProbeErr as Error).message).toBe("duplicate snapshot key: " + validFullSnapshot[0].key);

        // 4. Wrong flag fails
        const wrongFlagSnapshot = validFullSnapshot.map((entry, idx) =>
          idx === 0 ? Object.freeze({ ...entry, writable: !entry.writable }) : entry
        );
        expect(() => validateSnapshotAgainstRaw(Object.freeze(wrongFlagSnapshot), baselineDiagProbe)).toThrow();

        // 5. Accessor on raw diag fails with 0 getter invocations
        let probeGetterCalls = 0;
        const accessorRawDiag = Object.defineProperties({}, Object.getOwnPropertyDescriptors(baselineDiagProbe));
        Object.defineProperty(accessorRawDiag, "isDisposed", {
          get: () => { probeGetterCalls++; return true; },
          enumerable: true,
          configurable: true,
        });
        expect(() => createImmutableDiagnosticSnapshot(accessorRawDiag as any)).toThrow();
        expect(probeGetterCalls).toBe(0);

        // 6. Symbol extra fails
        const symbolRawDiag = Object.defineProperties({}, Object.getOwnPropertyDescriptors(baselineDiagProbe));
        (symbolRawDiag as any)[Symbol("extraProbe")] = 1;
        expect(() => createImmutableDiagnosticSnapshot(symbolRawDiag as any)).toThrow();

        // 7. Mutable object / function value fails
        const mutableValDiag = { ...baselineDiagProbe, serverRoutesCount: { nested: 1 } as any };
        expect(() => createImmutableDiagnosticSnapshot(mutableValDiag)).toThrow();
        const funcValDiag = { ...baselineDiagProbe, serverRoutesCount: (() => 1) as any };
        expect(() => createImmutableDiagnosticSnapshot(funcValDiag)).toThrow();

        interface PageClosureObservationRecord {
          readonly pageClosedPreEmergency: boolean;
          readonly pageClosureObservationSucceeded: boolean;
          readonly pageClosureObservationFailureLabel: string | null;
        }

        function observePageClosure(target: { isClosed: () => boolean }): PageClosureObservationRecord {
          try {
            const isClosed = target.isClosed();
            return Object.freeze({
              pageClosedPreEmergency: Boolean(isClosed),
              pageClosureObservationSucceeded: true,
              pageClosureObservationFailureLabel: null,
            });
          } catch {
            return Object.freeze({
              pageClosedPreEmergency: false,
              pageClosureObservationSucceeded: false,
              pageClosureObservationFailureLabel: "page closure observation threw exception",
            });
          }
        }

        function determinePageObservationFirstFailure(
          currentFirstFailure: string | null,
          obs: PageClosureObservationRecord
        ): string | null {
          if (currentFirstFailure !== null) {
            return currentFirstFailure;
          }
          if (!obs.pageClosureObservationSucceeded) {
            return null;
          }
          if (!obs.pageClosedPreEmergency) {
            return "real context close must close registered page";
          }
          return null;
        }

        function validateRouteWitnessScalar(side: "server" | "client", value: unknown): number {
          if (typeof value !== "number") {
            throw new Error(`${side} route witness must be a number`);
          }
          if (!Number.isFinite(value)) {
            throw new Error(`${side} route witness must be finite`);
          }
          if (!Number.isInteger(value)) {
            throw new Error(`${side} route witness must be an integer`);
          }
          if (value <= 0) {
            throw new Error(`${side} route witness must be positive`);
          }
          return value;
        }

        interface EmergencyCleanupEvidence {
          readonly unrouteAttemptedCount: number;
          readonly unrouteSettledCount: number;
          readonly unrouteFulfilled: boolean;
          readonly closeAttemptedCount: number;
          readonly closeSettledCount: number;
          readonly closeFulfilled: boolean;
          readonly completed: boolean;
        }

        interface MutableEmergencyCleanupEvidence {
          unrouteAttemptedCount: number;
          unrouteSettledCount: number;
          unrouteFulfilled: boolean;
          closeAttemptedCount: number;
          closeSettledCount: number;
          closeFulfilled: boolean;
          completed: boolean;
        }

        function createFreshEmergencyCleanupEvidence(): MutableEmergencyCleanupEvidence {
          return {
            unrouteAttemptedCount: 0,
            unrouteSettledCount: 0,
            unrouteFulfilled: false,
            closeAttemptedCount: 0,
            closeSettledCount: 0,
            closeFulfilled: false,
            completed: false,
          };
        }

        async function runEmergencyCleanupWithEvidence(
          evidence: MutableEmergencyCleanupEvidence,
          emergencyUnroute: () => Promise<void>,
          emergencyClose: () => Promise<void>,
          timeoutMs: number
        ): Promise<EmergencyCleanupEvidence> {
          try {
            try {
              evidence.unrouteAttemptedCount++;
              await withWatchdog(emergencyUnroute(), timeoutMs, "emergency unrouteAll");
              evidence.unrouteFulfilled = true;
            } catch {
              // Binding-free catch: retain no thrown value
            } finally {
              evidence.unrouteSettledCount++;
            }
          } finally {
            try {
              evidence.closeAttemptedCount++;
              await withWatchdog(emergencyClose(), timeoutMs, "emergency close");
              evidence.closeFulfilled = true;
            } catch {
              // Binding-free catch: retain no thrown value
            } finally {
              evidence.closeSettledCount++;
              evidence.completed = true;
              Object.freeze(evidence);
            }
          }
          return evidence as EmergencyCleanupEvidence;
        }

        const EXPECTED_EMERGENCY_EVIDENCE_KEYS = Object.freeze([
          "unrouteAttemptedCount",
          "unrouteSettledCount",
          "unrouteFulfilled",
          "closeAttemptedCount",
          "closeSettledCount",
          "closeFulfilled",
          "completed",
        ] as const);

        function assertEmergencyCleanupEvidence(
          evidence: unknown,
          expectedUnrouteFulfilled: boolean,
          expectedCloseFulfilled: boolean = true
        ): void {
          if (typeof evidence !== "object" || evidence === null || !Object.isFrozen(evidence)) {
            throw new Error("emergency cleanup evidence must be frozen");
          }
          if (Object.getPrototypeOf(evidence) !== Object.prototype) {
            throw new Error("emergency cleanup evidence keys invalid");
          }
          const ownKeys = Reflect.ownKeys(evidence);
          if (ownKeys.length !== EXPECTED_EMERGENCY_EVIDENCE_KEYS.length) {
            throw new Error("emergency cleanup evidence keys invalid");
          }
          for (const k of EXPECTED_EMERGENCY_EVIDENCE_KEYS) {
            if (!Object.prototype.hasOwnProperty.call(evidence, k)) {
              throw new Error("emergency cleanup evidence keys invalid");
            }
            const descriptor = Object.getOwnPropertyDescriptor(evidence, k);
            if (
              !descriptor ||
              "get" in descriptor ||
              "set" in descriptor ||
              typeof k !== "string"
            ) {
              throw new Error("emergency cleanup evidence keys invalid");
            }
          }
          for (const k of ownKeys) {
            if (typeof k !== "string" || !EXPECTED_EMERGENCY_EVIDENCE_KEYS.includes(k as any)) {
              throw new Error("emergency cleanup evidence keys invalid");
            }
          }

          const rec = evidence as EmergencyCleanupEvidence;
          if (
            typeof rec.unrouteAttemptedCount !== "number" ||
            typeof rec.unrouteSettledCount !== "number" ||
            typeof rec.unrouteFulfilled !== "boolean" ||
            typeof rec.closeAttemptedCount !== "number" ||
            typeof rec.closeSettledCount !== "number" ||
            typeof rec.closeFulfilled !== "boolean" ||
            typeof rec.completed !== "boolean"
          ) {
            throw new Error("emergency cleanup evidence types invalid");
          }

          if (rec.unrouteAttemptedCount !== 1) {
            throw new Error("emergency unroute must be attempted exactly once");
          }
          if (rec.unrouteSettledCount !== 1) {
            throw new Error("emergency unroute must settle exactly once");
          }
          if (rec.unrouteFulfilled !== expectedUnrouteFulfilled) {
            throw new Error("emergency unroute fulfillment mismatch");
          }
          if (rec.closeAttemptedCount !== 1) {
            throw new Error("emergency close must be attempted exactly once");
          }
          if (rec.closeSettledCount !== 1) {
            throw new Error("emergency close must settle exactly once");
          }
          if (rec.closeFulfilled !== expectedCloseFulfilled) {
            throw new Error("emergency close fulfillment mismatch");
          }
          if (rec.completed !== true) {
            throw new Error("emergency cleanup must complete before outcome");
          }
        }

        function assertEmergencyCleanupRunnerAwaited(evidence: unknown): void {
          if (
            typeof evidence !== "object" ||
            evidence === null ||
            !Object.isFrozen(evidence) ||
            (evidence as any).completed !== true
          ) {
            throw new Error("emergency cleanup runner must be awaited");
          }
        }

        const EXPECTED_ROW_D_SCENARIO_NAMES = Object.freeze([
          "baseline",
          "guard_noop",
          "fallback_reject",
          "close_reject",
          "close_noop",
        ] as const);

        function assertExactRowDScenarioMatrix(matrix: readonly RowDScenarioConfig[]): void {
          if (!Array.isArray(matrix) || matrix.length !== EXPECTED_ROW_D_SCENARIO_NAMES.length) {
            throw new Error("Row D scenarios must match exact ordered matrix");
          }
          for (let i = 0; i < EXPECTED_ROW_D_SCENARIO_NAMES.length; i++) {
            if (matrix[i]?.name !== EXPECTED_ROW_D_SCENARIO_NAMES[i]) {
              throw new Error("Row D scenarios must match exact ordered matrix");
            }
          }
        }

        type SettlementRecord =
          | { readonly status: "fulfilled"; readonly value: unknown }
          | { readonly status: "rejected"; readonly reason: unknown };

        async function settleWithTag(promise: Promise<void>, timeoutMs: number, label: string): Promise<SettlementRecord> {
          try {
            const value = await withWatchdog(promise, timeoutMs, label);
            return { status: "fulfilled", value };
          } catch (err: unknown) {
            return { status: "rejected", reason: err };
          }
        }

        interface RowDScenarioConfig {
          readonly name: "baseline" | "guard_noop" | "fallback_reject" | "close_reject" | "close_noop";
          readonly guardBehavior: "reject" | "noop";
          readonly fallbackBehavior: "fulfill" | "reject";
          readonly closeBehavior: "fulfill" | "reject" | "noop";
          readonly expectedRemoved: boolean;
          readonly expectedP1Status: "fulfilled" | "rejected";
          readonly expectedP3Status: "fulfilled" | "rejected";
          readonly expectedRejectionReasonIdentical: boolean;
          readonly expectedFallbackAttempted: number;
          readonly expectedFallbackCompleted: number;
          readonly expectedFallbackFulfilled: boolean;
          readonly expectedPostProbeAttempted: boolean;
          readonly expectedPostProbeSettled: boolean;
          readonly expectedPostDelta: number;
          readonly expectedCloseAttempted: number;
          readonly expectedCloseCompleted: number;
          readonly expectedCloseFulfilled: boolean;
          readonly expectedEmergencyUnrouteFulfilled: boolean;
          readonly expectedPageClosedPreEmergency: boolean;
          readonly expectedFirstFailure: string | null;
        }

        interface RowDSnapshot {
          readonly name: "baseline" | "guard_noop" | "fallback_reject" | "close_reject" | "close_noop";
          readonly rowApiUrl: string;
          readonly p1p2Identical: boolean;
          readonly p3p1Identical: boolean;
          readonly p1Status: "fulfilled" | "rejected";
          readonly p3Status: "fulfilled" | "rejected";
          readonly p1p3RejectionReasonIdentical: boolean;
          readonly diagBeforeP3: ImmutableDiagnosticSnapshot;
          readonly diagAfterP3: ImmutableDiagnosticSnapshot;
          readonly diagnosticsDeltaZero: boolean;
          readonly httpRouteRemoved: boolean;
          readonly preProbeAttempted: boolean;
          readonly preProbeSettled: boolean;
          readonly preProbeRejected: boolean;
          readonly preProbeCountBefore: number;
          readonly preProbeCountAfter: number;
          readonly preDelta: number;
          readonly fallbackAttempted: number;
          readonly fallbackCompleted: number;
          readonly fallbackFulfilled: boolean;
          readonly postProbeAttempted: boolean;
          readonly postProbeSettled: boolean;
          readonly postProbeCountBefore: number;
          readonly postProbeCountAfter: number;
          readonly postDelta: number;
          readonly closeAttempted: number;
          readonly closeCompleted: number;
          readonly closeFulfilled: boolean;
          readonly emergencyEvidence: EmergencyCleanupEvidence;
          readonly pageClosedPreEmergency: boolean;
          readonly pageClosureObservationSucceeded: boolean;
          readonly pageClosureObservationFailureLabel: string | null;
          readonly serverRoutesWitness: number;
          readonly clientRoutesWitness: number;
          readonly firstFailure: string | null;
        }

        function assertSnapshotValuesAgainstContract(
          snapshot: ImmutableDiagnosticSnapshot,
          scenario: RowDScenarioConfig,
          serverWitness: number,
          clientWitness: number
        ): void {
          expect(Object.isFrozen(snapshot)).toBe(true);
          expect(snapshot.length).toBe(EXPECTED_DIAGNOSTIC_KEYS.length);

          const seenKeys = new Set<string>();
          const map = new Map<string, number | boolean>();

          for (const entry of snapshot) {
            expect(Object.isFrozen(entry)).toBe(true);
            expect(typeof entry.key).toBe("string");
            expect(EXPECTED_DIAGNOSTIC_KEYS_SET.has(entry.key)).toBe(true);
            expect(seenKeys.has(entry.key)).toBe(false);
            seenKeys.add(entry.key);

            expect(entry.isAccessor).toBe(false);
            expect(entry.writable).toBe(true);
            expect(entry.enumerable).toBe(true);
            expect(entry.configurable).toBe(true);

            const expectedType = EXPECTED_DIAGNOSTIC_SCHEMA[entry.key as keyof NetworkGuardDiagnostics];
            expect(typeof entry.value).toBe(expectedType);
            if (expectedType === "number") {
              expect(Number.isFinite(entry.value)).toBe(true);
              expect(Number.isInteger(entry.value)).toBe(true);
            }
            map.set(entry.key, entry.value);
          }

          expect(seenKeys.size).toBe(EXPECTED_DIAGNOSTIC_KEYS.length);
          expect(map.size).toBe(EXPECTED_DIAGNOSTIC_KEYS.length);
          for (const expKey of EXPECTED_DIAGNOSTIC_KEYS) {
            expect(seenKeys.has(expKey)).toBe(true);
          }

          expect(map.get("isDisposed")).toBe(true);
          expect(map.get("initialPageListenerDetached")).toBe(true);
          expect(map.get("contextListenersDetached")).toBe(true);
          expect(map.get("unrouteAttempted")).toBe(true);
          expect(map.get("retainedPageErrorHandlersCount")).toBe(0);

          expect(map.get("httpRouteRemoved")).toBe(scenario.expectedRemoved);

          expect(map.get("popupsCount")).toBe(0);
          expect(map.get("openPopupsCountAtDispose")).toBe(0);
          expect(map.get("popupCloseAttemptedCount")).toBe(0);
          expect(map.get("popupCloseFulfilledCount")).toBe(0);
          expect(map.get("popupCloseRejectedCount")).toBe(0);
          expect(map.get("closedPopupsCount")).toBe(0);
          expect(map.get("popupListenersDetachedCount")).toBe(0);

          expect(map.get("serverRoutesCount")).toBe(serverWitness);
          expect(map.get("clientRoutesCount")).toBe(clientWitness);

          expect(map.get("serverCloseAttemptedCount")).toBe(serverWitness);
          expect(map.get("serverCloseFulfilledCount")).toBe(serverWitness);
          expect(map.get("serverCloseRejectedCount")).toBe(0);
          expect(map.get("serverCloseTimedOutCount")).toBe(0);
          expect(map.get("serverObservedClosedCount")).toBe(serverWitness);

          expect(map.get("clientCloseAttemptedCount")).toBe(clientWitness);
          expect(map.get("clientCloseFulfilledCount")).toBe(clientWitness);
          expect(map.get("clientCloseRejectedCount")).toBe(0);
          expect(map.get("clientCloseTimedOutCount")).toBe(0);
          expect(map.get("clientObservedClosedCount")).toBe(clientWitness);
        }

        function assertRowDSnapshotContract(
          outcome: RowDSnapshot,
          scenario: RowDScenarioConfig,
          seenRowUrls: Set<string>
        ): void {
          expect(Object.isFrozen(outcome)).toBe(true);
          expect(outcome.name).toBe(scenario.name);

          assertEmergencyCleanupEvidence(
            outcome.emergencyEvidence,
            scenario.expectedEmergencyUnrouteFulfilled,
            true
          );

          if (!outcome.p1p2Identical) {
            throw new Error("p1 and p2 dispose promises must be identical");
          }
          if (!outcome.p3p1Identical) {
            throw new Error("p3 and p1 dispose promises must be identical");
          }

          if (outcome.pageClosureObservationSucceeded === false && outcome.pageClosureObservationFailureLabel === "page closure observation threw exception") {
            throw new Error(outcome.pageClosureObservationFailureLabel);
          } else if (outcome.pageClosureObservationSucceeded === true && outcome.pageClosureObservationFailureLabel === null) {
            // Valid success state: proceed
          } else {
            throw new Error("page closure observation state invalid");
          }

          expect(outcome.p1Status).toBe(scenario.expectedP1Status);
          expect(outcome.p3Status).toBe(scenario.expectedP3Status);
          expect(outcome.p1p3RejectionReasonIdentical).toBe(scenario.expectedRejectionReasonIdentical);

          expect(outcome.httpRouteRemoved).toBe(scenario.expectedRemoved);
          expect(outcome.firstFailure).toBe(scenario.expectedFirstFailure);
          expect(outcome.pageClosedPreEmergency).toBe(scenario.expectedPageClosedPreEmergency);

          validateRouteWitnessScalar("server", outcome.serverRoutesWitness);
          validateRouteWitnessScalar("client", outcome.clientRoutesWitness);

          const expectedUrlPattern = new RegExp(`^http://127\\.0\\.0\\.1:3105/api/task07-row-d-${scenario.name}-\\d+-[a-z0-9]+$`);
          expect(expectedUrlPattern.test(outcome.rowApiUrl)).toBe(true);
          expect(seenRowUrls.has(outcome.rowApiUrl)).toBe(false);
          seenRowUrls.add(outcome.rowApiUrl);

          expect(outcome.preProbeAttempted).toBe(true);
          expect(outcome.preProbeSettled).toBe(true);
          expect(outcome.preProbeRejected).toBe(true);
          expect(outcome.preProbeCountBefore).toBe(0);
          expect(outcome.preProbeCountAfter).toBe(1);
          expect(outcome.preDelta).toBe(1);

          expect(outcome.fallbackAttempted).toBe(scenario.expectedFallbackAttempted);
          expect(outcome.fallbackCompleted).toBe(scenario.expectedFallbackCompleted);
          expect(outcome.fallbackFulfilled).toBe(scenario.expectedFallbackFulfilled);

          expect(outcome.postProbeAttempted).toBe(scenario.expectedPostProbeAttempted);
          expect(outcome.postProbeSettled).toBe(scenario.expectedPostProbeSettled);
          expect(outcome.postProbeCountBefore).toBe(1);
          expect(outcome.postProbeCountAfter).toBe(1);
          expect(outcome.postDelta).toBe(0);

          expect(outcome.closeAttempted).toBe(scenario.expectedCloseAttempted);
          expect(outcome.closeCompleted).toBe(scenario.expectedCloseCompleted);
          expect(outcome.closeFulfilled).toBe(scenario.expectedCloseFulfilled);

          expect(Object.isFrozen(outcome.diagBeforeP3)).toBe(true);
          expect(Object.isFrozen(outcome.diagAfterP3)).toBe(true);
          expect(outcome.diagBeforeP3.length).toBe(EXPECTED_DIAGNOSTIC_KEYS.length);
          expect(outcome.diagAfterP3.length).toBe(EXPECTED_DIAGNOSTIC_KEYS.length);

          assertSnapshotValuesAgainstContract(outcome.diagBeforeP3, scenario, outcome.serverRoutesWitness, outcome.clientRoutesWitness);
          assertSnapshotValuesAgainstContract(outcome.diagAfterP3, scenario, outcome.serverRoutesWitness, outcome.clientRoutesWitness);

          expect(outcome.diagnosticsDeltaZero).toBe(true);
          expect(areImmutableDiagnosticsEqual(outcome.diagBeforeP3, outcome.diagAfterP3)).toBe(true);
        }

        async function executeRowDScenario(
          b: any,
          scenario: RowDScenarioConfig
        ): Promise<RowDSnapshot> {
          let pendingContextPromise: Promise<BrowserContext> | null = null;
          let ctx: BrowserContext | null = null;
          let contextAdopted = false;
          let boundEmergencyClose: (() => Promise<void>) | null = null;
          let boundEmergencyUnroute: (() => Promise<void>) | null = null;
          let emergencyCleanupCompleted = false;
          const emergencyEvidence = createFreshEmergencyCleanupEvidence();

          try {
            const contextPromise = b.newContext({ baseURL: "http://127.0.0.1:3105" });
            pendingContextPromise = contextPromise;
            ctx = await withWatchdog<BrowserContext>(contextPromise, 5000, "context creation");

            try {
              contextAdopted = true;

              boundEmergencyClose = ctx.close.bind(ctx);
              boundEmergencyUnroute = ctx.unrouteAll.bind(ctx, { behavior: "ignoreErrors" });
              const realEmergencyUnroute = boundEmergencyUnroute;
              const realEmergencyClose = boundEmergencyClose;

              const realUnderTestFallbackUnroute = ctx.unrouteAll.bind(ctx, { behavior: "ignoreErrors" });
              const realUnderTestClose = ctx.close.bind(ctx);

              trackedContexts.push(ctx);

              const countOccurrences = (arr: string[], target: string) => arr.filter((u) => u === target).length;

              let witnessServerRegistrations = 0;
              let witnessClientRegistrations = 0;
              const wsOnCloseDelegate = (ws: WebSocketRoute, cb: () => void, side?: "server" | "client") => {
                if (side === "server") {
                  witnessServerRegistrations++;
                } else if (side === "client") {
                  witnessClientRegistrations++;
                }
                ws.onClose(cb);
              };

              let fallbackAttemptedCount = 0;
              let fallbackCompletedCount = 0;
              let fallbackFulfilled = false;
              const underTestFallbackUnroute = async () => {
                fallbackAttemptedCount++;
                if (scenario.fallbackBehavior === "reject") {
                  throw new Error("FALLBACK_REJECT_SENTINEL");
                }
                await withWatchdog(
                  realUnderTestFallbackUnroute(),
                  5000,
                  "under-test fallback unrouteAll"
                );
                fallbackCompletedCount++;
                fallbackFulfilled = true;
              };

              let closeAttemptedCount = 0;
              let closeCompletedCount = 0;
              let closeFulfilled = false;
              const underTestClose = async () => {
                closeAttemptedCount++;
                if (scenario.closeBehavior === "reject") {
                  throw new Error("CLOSE_REJECT_SENTINEL");
                }
                if (scenario.closeBehavior === "noop") {
                  closeCompletedCount++;
                  closeFulfilled = true;
                  return;
                }
                await withWatchdog(realUnderTestClose(), 5000, "under-test ctx close");
                closeCompletedCount++;
                closeFulfilled = true;
              };

              const rowApiUrl = `http://127.0.0.1:3105/api/task07-row-d-${scenario.name}-${Date.now()}-${Math.random().toString(36).slice(2)}`;

              const unrouteAllDelegate = scenario.guardBehavior === "noop"
                ? async () => { /* resolved no-op */ }
                : async () => { throw new Error("UNROUTE_SENTINEL"); };

              let intentionalFirstFailure: string | null = null;
              let p1p2Identical = false;
              let p3p1Identical = false;
              let s1: SettlementRecord = { status: "rejected", reason: new Error("UNINITIALIZED") };
              let s3: SettlementRecord = { status: "rejected", reason: new Error("UNINITIALIZED") };
              let p1p3RejectionReasonIdentical = false;
              let diagBeforeP3: ImmutableDiagnosticSnapshot | null = null;
              let diagAfterP3: ImmutableDiagnosticSnapshot | null = null;
              let diagnosticsDeltaZero = false;
              let httpRouteRemoved = false;
              let preProbeAttempted = false;
              let preProbeSettled = false;
              let preProbeRejected = false;
              let preProbeCountBefore = 0;
              let preProbeCountAfter = 0;
              let preDelta = 0;
              let postProbeAttempted = false;
              let postProbeSettled = false;
              let postProbeCountBefore = 0;
              let postProbeCountAfter = 0;
              let postDelta = 0;
              let pageClosedPreEmergency = false;
              let pageClosureObservationSucceeded = true;
              let pageClosureObservationFailureLabel: string | null = null;

              let serverRoutesWitness = 0;
              let clientRoutesWitness = 0;

              let mainCompletedNormally = false;
              let earliestUnexpectedFailure: { tag: true; error: unknown } | null = null;

              let page: Page | null = null;
              const recorders: NetworkRecorders = {
                recordedRequests: [],
                failedRequests: [],
                finishedRequests: [],
                recordedWebSockets: [],
                recordedPopups: [],
                pageErrors: [],
              };

              try {
                // Main proof block
                try {
                  page = await withWatchdog<Page>(ctx.newPage(), 5000, "page creation");

                  const guard: NetworkGuardController = await withWatchdog<NetworkGuardController>(
                    installAuthSourceNetworkGuards(ctx, page, recorders, {
                      unrouteAll: unrouteAllDelegate,
                      wsOnClose: wsOnCloseDelegate,
                    }),
                    5000,
                    "guard installation"
                  );

                  await withWatchdog(page.goto("/", { waitUntil: "domcontentloaded" }), 10000, "page goto");
                  await withWatchdog(
                    expect.poll(() => guard.getDiagnostics().serverRoutesCount).toBeGreaterThan(0),
                    5000,
                    "serverRoutesCount poll"
                  );
                  await withWatchdog(
                    expect.poll(() => guard.getDiagnostics().clientRoutesCount).toBeGreaterThan(0),
                    5000,
                    "clientRoutesCount poll"
                  );

                  serverRoutesWitness = witnessServerRegistrations;
                  clientRoutesWitness = witnessClientRegistrations;

                  const p1 = guard.dispose();
                  const p2 = guard.dispose();
                  p1p2Identical = (p1 === p2);
                  expect(p1p2Identical).toBe(true);

                  s1 = await settleWithTag(p1, 5000, "p1 settlement");
                  const rawDiagBeforeP3 = guard.getDiagnostics();
                  diagBeforeP3 = createImmutableDiagnosticSnapshot(rawDiagBeforeP3);
                  validateSnapshotAgainstRaw(diagBeforeP3, rawDiagBeforeP3);

                  const p3 = guard.dispose();
                  p3p1Identical = (p3 === p1);
                  expect(p3p1Identical).toBe(true);

                  s3 = await settleWithTag(p3, 5000, "p3 settlement");
                  const rawDiagAfterP3 = guard.getDiagnostics();
                  diagAfterP3 = createImmutableDiagnosticSnapshot(rawDiagAfterP3);
                  validateSnapshotAgainstRaw(diagAfterP3, rawDiagAfterP3);

                  diagnosticsDeltaZero = areImmutableDiagnosticsEqual(diagBeforeP3, diagAfterP3);
                  expect(diagnosticsDeltaZero).toBe(true);

                  if (scenario.guardBehavior === "reject") {
                    expect(s1.status).toBe("rejected");
                    expect(s3.status).toBe("rejected");
                    if (s1.status === "rejected" && s3.status === "rejected") {
                      p1p3RejectionReasonIdentical = (s1.reason === s3.reason);
                      expect(p1p3RejectionReasonIdentical).toBe(true);
                      const liveStack = validateAndExtractLiveTeardownStackDescriptor(s1.reason);
                      assertExactNetworkGuardTeardownError(
                        s1.reason,
                        ["UNROUTE_SENTINEL"],
                        pristineNetworkGuardOwnKeys,
                        liveStack,
                        pristineMsgBaseline
                      );
                    }
                  } else {
                    expect(s1.status).toBe("fulfilled");
                    expect(s3.status).toBe("fulfilled");
                  }

                  const diag = rawDiagAfterP3;
                  httpRouteRemoved = diag.httpRouteRemoved;
                  expect(httpRouteRemoved).toBe(scenario.expectedRemoved);

                  assertCommonCleanupDiagnostics(diag, {
                    popupsCount: 0,
                    openPopupsCountAtDispose: 0,
                    popupCloseAttemptedCount: 0,
                    popupCloseFulfilledCount: 0,
                    popupCloseRejectedCount: 0,
                    closedPopupsCount: 0,
                    popupListenersDetachedCount: 0,
                    httpRouteRemoved: scenario.expectedRemoved,
                  });

                  const sR = diag.serverRoutesCount;
                  const cR = diag.clientRoutesCount;
                  const expectedVectors = {
                    server: {
                      routesCount: sR,
                      attemptedCount: sR,
                      fulfilledCount: sR,
                      rejectedCount: 0,
                      observedClosedCount: sR,
                      timedOutCount: 0,
                    },
                    client: {
                      routesCount: cR,
                      attemptedCount: cR,
                      fulfilledCount: cR,
                      rejectedCount: 0,
                      observedClosedCount: cR,
                      timedOutCount: 0,
                    },
                  };
                  assertSocketVector(diag, expectedVectors);

                  // Pre-fallback fetch probe: ALL 5 rows perform this probe and require rejection + exact delta +1
                  preProbeAttempted = true;
                  preProbeCountBefore = countOccurrences(recorders.recordedRequests, rowApiUrl);
                  expect(preProbeCountBefore).toBe(0);

                  const preFetchOutcome = await withWatchdog<{ ok: boolean; status?: number }>(
                    page.evaluate(async (url: string) => {
                      const controller = new AbortController();
                      const timer = setTimeout(() => controller.abort(), 2000);
                      try {
                        const res = await fetch(url, { cache: "no-store", signal: controller.signal });
                        return { ok: true, status: res.status };
                      } catch {
                        return { ok: false };
                      } finally {
                        clearTimeout(timer);
                      }
                    }, rowApiUrl),
                    4000,
                    "pre-fallback page evaluate fetch"
                  );
                  preProbeCountAfter = countOccurrences(recorders.recordedRequests, rowApiUrl);
                  expect(preProbeCountAfter).toBe(1);
                  preDelta = preProbeCountAfter - preProbeCountBefore;
                  preProbeRejected = !preFetchOutcome.ok;
                  preProbeSettled = preProbeRejected && preDelta === 1;
                  expect(preProbeSettled).toBe(true);
                  expect(preDelta).toBe(1);

                  // Intentional first failure tracking with fixed precedence:
                  if (scenario.guardBehavior === "noop") {
                    intentionalFirstFailure = "guard unroute must reject";
                  }

                  mainCompletedNormally = true;
                } catch (err: unknown) {
                  if (earliestUnexpectedFailure === null) {
                    earliestUnexpectedFailure = { tag: true, error: err };
                  }
                } finally {
                  // Recovery block 1: Under-test fallback unroute & post-probe
                  try {
                    try {
                      await underTestFallbackUnroute();
                    } catch {
                      if (intentionalFirstFailure === null) {
                        intentionalFirstFailure = "real fallback unroute must fulfill";
                      }
                    }

                    if (fallbackFulfilled && page !== null) {
                      postProbeAttempted = true;
                      try {
                        postProbeCountBefore = countOccurrences(recorders.recordedRequests, rowApiUrl);
                        if (mainCompletedNormally) {
                          expect(postProbeCountBefore).toBe(1);
                        }

                        const postFetchOutcome = await withWatchdog<{ ok: boolean; status?: number }>(
                          page.evaluate(async (url: string) => {
                            const controller = new AbortController();
                            const timer = setTimeout(() => controller.abort(), 2000);
                            try {
                              const res = await fetch(url, { cache: "no-store", signal: controller.signal });
                              return { ok: true, status: res.status };
                            } catch {
                              return { ok: false };
                            } finally {
                              clearTimeout(timer);
                            }
                          }, rowApiUrl),
                          4000,
                          "post-fallback page evaluate fetch"
                        );
                        postProbeCountAfter = countOccurrences(recorders.recordedRequests, rowApiUrl);
                        if (mainCompletedNormally) {
                          expect(postProbeCountAfter).toBe(1);
                        }
                        postDelta = postProbeCountAfter - postProbeCountBefore;
                        postProbeSettled = postFetchOutcome.ok && postDelta === 0;
                        if (mainCompletedNormally) {
                          expect(postProbeSettled).toBe(true);
                          expect(postDelta).toBe(0);
                        }
                      } catch {
                        postProbeSettled = false;
                      }
                    } else {
                      // Skipped post-probe: measure truthful current count once
                      const currentObservedCount = countOccurrences(recorders.recordedRequests, rowApiUrl);
                      postProbeCountBefore = currentObservedCount;
                      postProbeCountAfter = currentObservedCount;
                      postDelta = 0;
                      postProbeAttempted = false;
                      postProbeSettled = false;
                      if (mainCompletedNormally) {
                        expect(currentObservedCount).toBe(1);
                      }
                    }
                  } catch (fbErr: unknown) {
                    if (earliestUnexpectedFailure === null) {
                      earliestUnexpectedFailure = { tag: true, error: fbErr };
                    }
                  } finally {
                    // Recovery block 2: Under-test close & page closure observation
                    try {
                      try {
                        await underTestClose();
                      } catch {
                        if (intentionalFirstFailure === null) {
                          intentionalFirstFailure = "real context close must fulfill";
                        }
                      }

                      if (page !== null) {
                        const closureObs = observePageClosure(page);
                        pageClosedPreEmergency = closureObs.pageClosedPreEmergency;
                        pageClosureObservationSucceeded = closureObs.pageClosureObservationSucceeded;
                        pageClosureObservationFailureLabel = closureObs.pageClosureObservationFailureLabel;
                        intentionalFirstFailure = determinePageObservationFirstFailure(intentionalFirstFailure, closureObs);
                      }
                    } catch (closeErr: unknown) {
                      if (earliestUnexpectedFailure === null) {
                        earliestUnexpectedFailure = { tag: true, error: closeErr };
                      }
                    }
                  }
                }

                if (earliestUnexpectedFailure !== null) {
                  throw earliestUnexpectedFailure.error;
                }

                if (!mainCompletedNormally || diagBeforeP3 === null || diagAfterP3 === null) {
                  throw new Error(`Execution failed before snapshot creation for scenario ${scenario.name}`);
                }

                return Object.freeze({
                  name: scenario.name,
                  rowApiUrl,
                  p1p2Identical,
                  p3p1Identical,
                  p1Status: s1.status,
                  p3Status: s3.status,
                  p1p3RejectionReasonIdentical,
                  diagBeforeP3,
                  diagAfterP3,
                  diagnosticsDeltaZero,
                  httpRouteRemoved,
                  preProbeAttempted,
                  preProbeSettled,
                  preProbeRejected,
                  preProbeCountBefore,
                  preProbeCountAfter,
                  preDelta,
                  fallbackAttempted: fallbackAttemptedCount,
                  fallbackCompleted: fallbackCompletedCount,
                  fallbackFulfilled,
                  postProbeAttempted,
                  postProbeSettled,
                  postProbeCountBefore,
                  postProbeCountAfter,
                  postDelta,
                  closeAttempted: closeAttemptedCount,
                  closeCompleted: closeCompletedCount,
                  closeFulfilled,
                  emergencyEvidence: emergencyEvidence as EmergencyCleanupEvidence,
                  pageClosedPreEmergency,
                  pageClosureObservationSucceeded,
                  pageClosureObservationFailureLabel,
                  serverRoutesWitness,
                  clientRoutesWitness,
                  firstFailure: intentionalFirstFailure,
                });
              } finally {
                // Emergency cleanup runs structurally via shared runner with post-await guard
                await runEmergencyCleanupWithEvidence(
                  emergencyEvidence,
                  realEmergencyUnroute,
                  realEmergencyClose,
                  3000
                );
                assertEmergencyCleanupRunnerAwaited(emergencyEvidence);
                emergencyCleanupCompleted = (emergencyEvidence.completed === true);
              }
            } finally {
              if (!emergencyCleanupCompleted) {
                if (boundEmergencyClose !== null) {
                  try {
                    await withWatchdog(boundEmergencyClose(), 3000, "adopted context close fallback");
                  } catch {}
                } else {
                  try {
                    await withWatchdog(ctx.close(), 3000, "adopted context close raw fallback");
                  } catch {}
                }
                emergencyCleanupCompleted = true;
              }
            }
          } finally {
            if (!contextAdopted && pendingContextPromise !== null) {
              const closePromise = pendingContextPromise
                .then((c: any) => withWatchdog(c.close(), 3000, "late context close"))
                .catch(() => {});
              try {
                await withWatchdog(closePromise, 3000, "pending context close wait");
              } catch {}
            }
          }
        }

        const scenarios: RowDScenarioConfig[] = [
          {
            name: "baseline",
            guardBehavior: "reject",
            fallbackBehavior: "fulfill",
            closeBehavior: "fulfill",
            expectedRemoved: false,
            expectedP1Status: "rejected",
            expectedP3Status: "rejected",
            expectedRejectionReasonIdentical: true,
            expectedFallbackAttempted: 1,
            expectedFallbackCompleted: 1,
            expectedFallbackFulfilled: true,
            expectedPostProbeAttempted: true,
            expectedPostProbeSettled: true,
            expectedPostDelta: 0,
            expectedCloseAttempted: 1,
            expectedCloseCompleted: 1,
            expectedCloseFulfilled: true,
            expectedEmergencyUnrouteFulfilled: false,
            expectedPageClosedPreEmergency: true,
            expectedFirstFailure: null,
          },
          {
            name: "guard_noop",
            guardBehavior: "noop",
            fallbackBehavior: "fulfill",
            closeBehavior: "fulfill",
            expectedRemoved: true,
            expectedP1Status: "fulfilled",
            expectedP3Status: "fulfilled",
            expectedRejectionReasonIdentical: false,
            expectedFallbackAttempted: 1,
            expectedFallbackCompleted: 1,
            expectedFallbackFulfilled: true,
            expectedPostProbeAttempted: true,
            expectedPostProbeSettled: true,
            expectedPostDelta: 0,
            expectedCloseAttempted: 1,
            expectedCloseCompleted: 1,
            expectedCloseFulfilled: true,
            expectedEmergencyUnrouteFulfilled: false,
            expectedPageClosedPreEmergency: true,
            expectedFirstFailure: "guard unroute must reject",
          },
          {
            name: "fallback_reject",
            guardBehavior: "reject",
            fallbackBehavior: "reject",
            closeBehavior: "fulfill",
            expectedRemoved: false,
            expectedP1Status: "rejected",
            expectedP3Status: "rejected",
            expectedRejectionReasonIdentical: true,
            expectedFallbackAttempted: 1,
            expectedFallbackCompleted: 0,
            expectedFallbackFulfilled: false,
            expectedPostProbeAttempted: false,
            expectedPostProbeSettled: false,
            expectedPostDelta: 0,
            expectedCloseAttempted: 1,
            expectedCloseCompleted: 1,
            expectedCloseFulfilled: true,
            expectedEmergencyUnrouteFulfilled: false,
            expectedPageClosedPreEmergency: true,
            expectedFirstFailure: "real fallback unroute must fulfill",
          },
          {
            name: "close_reject",
            guardBehavior: "reject",
            fallbackBehavior: "fulfill",
            closeBehavior: "reject",
            expectedRemoved: false,
            expectedP1Status: "rejected",
            expectedP3Status: "rejected",
            expectedRejectionReasonIdentical: true,
            expectedFallbackAttempted: 1,
            expectedFallbackCompleted: 1,
            expectedFallbackFulfilled: true,
            expectedPostProbeAttempted: true,
            expectedPostProbeSettled: true,
            expectedPostDelta: 0,
            expectedCloseAttempted: 1,
            expectedCloseCompleted: 0,
            expectedCloseFulfilled: false,
            expectedEmergencyUnrouteFulfilled: true,
            expectedPageClosedPreEmergency: false,
            expectedFirstFailure: "real context close must fulfill",
          },
          {
            name: "close_noop",
            guardBehavior: "reject",
            fallbackBehavior: "fulfill",
            closeBehavior: "noop",
            expectedRemoved: false,
            expectedP1Status: "rejected",
            expectedP3Status: "rejected",
            expectedRejectionReasonIdentical: true,
            expectedFallbackAttempted: 1,
            expectedFallbackCompleted: 1,
            expectedFallbackFulfilled: true,
            expectedPostProbeAttempted: true,
            expectedPostProbeSettled: true,
            expectedPostDelta: 0,
            expectedCloseAttempted: 1,
            expectedCloseCompleted: 1,
            expectedCloseFulfilled: true,
            expectedEmergencyUnrouteFulfilled: true,
            expectedPageClosedPreEmergency: false,
            expectedFirstFailure: "real context close must close registered page",
          },
        ];

        assertExactRowDScenarioMatrix(scenarios);

        // Section A.3 isolated matrix probes
        const matrixProbeCases: Array<RowDScenarioConfig[]> = [
          [scenarios[0], scenarios[2], scenarios[3], scenarios[4]], // guard_noop omitted
          [scenarios[0], scenarios[0], scenarios[2], scenarios[3], scenarios[4]], // baseline duplicated into guard_noop slot
          [scenarios[0], scenarios[2], scenarios[1], scenarios[3], scenarios[4]], // guard_noop and fallback_reject swapped
          [...scenarios, scenarios[0]], // one extra baseline appended
        ];

        for (const probeMatrix of matrixProbeCases) {
          let matrixErr: unknown = null;
          try {
            assertExactRowDScenarioMatrix(probeMatrix);
          } catch (err: unknown) {
            matrixErr = err;
          }
          expect(matrixErr instanceof Error).toBe(true);
          expect((matrixErr as Error).message).toBe("Row D scenarios must match exact ordered matrix");
        }

        const seenRowUrls = new Set<string>();

        for (const scenario of scenarios) {
          const outcome = await executeRowDScenario(browser, scenario);
          assertRowDSnapshotContract(outcome, scenario, seenRowUrls);

          // Self-causal mutant for p1p2Identical
          const isolatedSeenUrls1 = new Set<string>();
          const mutantOutcomeP1P2 = Object.freeze({ ...outcome, p1p2Identical: false });
          let mutantP1P2Err: unknown = null;
          try {
            assertRowDSnapshotContract(mutantOutcomeP1P2, scenario, isolatedSeenUrls1);
          } catch (err: unknown) {
            mutantP1P2Err = err;
          }
          expect(mutantP1P2Err instanceof Error).toBe(true);
          expect((mutantP1P2Err as Error).message).toBe("p1 and p2 dispose promises must be identical");

          // Self-causal mutant for p3p1Identical
          const isolatedSeenUrls2 = new Set<string>();
          const mutantOutcomeP3P1 = Object.freeze({ ...outcome, p3p1Identical: false });
          let mutantP3P1Err: unknown = null;
          try {
            assertRowDSnapshotContract(mutantOutcomeP3P1, scenario, isolatedSeenUrls2);
          } catch (err: unknown) {
            mutantP3P1Err = err;
          }
          expect(mutantP3P1Err instanceof Error).toBe(true);
          expect((mutantP3P1Err as Error).message).toBe("p3 and p1 dispose promises must be identical");

          // Bounded local helper probes only for the baseline row after real executeRowDScenario has completed emergency cleanup
          if (scenario.name === "baseline") {
            // Item A.4: Directly exercise determinePageObservationFirstFailure
            let successCalls = 0;
            const successTarget = {
              isClosed: () => {
                successCalls++;
                return true;
              },
            };
            const successRecord = observePageClosure(successTarget);
            expect(successCalls).toBe(1);
            expect(Object.isFrozen(successRecord)).toBe(true);
            expect(successRecord.pageClosedPreEmergency).toBe(true);
            expect(successRecord.pageClosureObservationSucceeded).toBe(true);
            expect(successRecord.pageClosureObservationFailureLabel).toBeNull();

            let failureCalls = 0;
            const failureTarget = {
              isClosed: () => {
                failureCalls++;
                throw new Error("PROBE_IS_CLOSED_THREW");
              },
            };
            const failureRecord = observePageClosure(failureTarget);
            expect(failureCalls).toBe(1);
            expect(Object.isFrozen(failureRecord)).toBe(true);
            expect(failureRecord.pageClosedPreEmergency).toBe(false);
            expect(failureRecord.pageClosureObservationSucceeded).toBe(false);
            expect(failureRecord.pageClosureObservationFailureLabel).toBe("page closure observation threw exception");

            // Isolated determinePageObservationFirstFailure assertions:
            expect(determinePageObservationFirstFailure(null, failureRecord)).toBeNull();
            expect(determinePageObservationFirstFailure("guard unroute must reject", failureRecord)).toBe("guard unroute must reject");
            const falseSuccessRecord = Object.freeze({ pageClosedPreEmergency: false, pageClosureObservationSucceeded: true, pageClosureObservationFailureLabel: null });
            expect(determinePageObservationFirstFailure(null, falseSuccessRecord)).toBe("real context close must close registered page");
            expect(determinePageObservationFirstFailure(null, successRecord)).toBeNull();
            expect(determinePageObservationFirstFailure(null, failureRecord)).not.toBe(failureRecord.pageClosureObservationFailureLabel);

            // Valid failure control probe:
            const isolatedSeenUrlsClosure = new Set<string>();
            const mutantOutcomeClosure = Object.freeze({
              ...outcome,
              pageClosedPreEmergency: failureRecord.pageClosedPreEmergency,
              pageClosureObservationSucceeded: failureRecord.pageClosureObservationSucceeded,
              pageClosureObservationFailureLabel: failureRecord.pageClosureObservationFailureLabel,
            });
            let closureErr: unknown = null;
            try {
              assertRowDSnapshotContract(mutantOutcomeClosure, scenario, isolatedSeenUrlsClosure);
            } catch (err: unknown) {
              closureErr = err;
            }
            expect(closureErr instanceof Error).toBe(true);
            expect((closureErr as Error).message).toBe("page closure observation threw exception");

            // Item B.4: 6 separate frozen invalid-state caller mutants
            const invalidStateCases: Array<{ succeeded: boolean; label: string | null }> = [
              { succeeded: false, label: "WRONG_PAGE_CLOSURE_OBSERVATION_LABEL" },
              { succeeded: false, label: "prefix page closure observation threw exception" },
              { succeeded: false, label: "page closure observation threw exception suffix" },
              { succeeded: false, label: null },
              { succeeded: true, label: "page closure observation threw exception" },
              { succeeded: true, label: "WRONG_PAGE_CLOSURE_OBSERVATION_LABEL" },
            ];

            for (const invalidCase of invalidStateCases) {
              const isolatedInvalidUrls = new Set<string>();
              const mutantInvalidState = Object.freeze({
                ...outcome,
                pageClosedPreEmergency: false,
                pageClosureObservationSucceeded: invalidCase.succeeded,
                pageClosureObservationFailureLabel: invalidCase.label,
              });
              let invalidStateErr: unknown = null;
              try {
                assertRowDSnapshotContract(mutantInvalidState, scenario, isolatedInvalidUrls);
              } catch (err: unknown) {
                invalidStateErr = err;
              }
              expect(invalidStateErr instanceof Error).toBe(true);
              expect((invalidStateErr as Error).message).toBe("page closure observation state invalid");
            }

            // Item C: Route witness cloning helper & 10 baseline mutants
            function cloneSnapshotWithModifiedVectors(
              baseSnapshot: ImmutableDiagnosticSnapshot,
              overrides: { serverValue?: any; clientValue?: any }
            ): ImmutableDiagnosticSnapshot {
              const entries: DiagnosticPropertySnapshot[] = [];
              for (const entry of baseSnapshot) {
                let val: any = entry.value;
                if (overrides.serverValue !== undefined && (
                  entry.key === "serverRoutesCount" ||
                  entry.key === "serverCloseAttemptedCount" ||
                  entry.key === "serverCloseFulfilledCount" ||
                  entry.key === "serverObservedClosedCount"
                )) {
                  val = overrides.serverValue;
                } else if (overrides.clientValue !== undefined && (
                  entry.key === "clientRoutesCount" ||
                  entry.key === "clientCloseAttemptedCount" ||
                  entry.key === "clientCloseFulfilledCount" ||
                  entry.key === "clientObservedClosedCount"
                )) {
                  val = overrides.clientValue;
                }
                entries.push(
                  Object.freeze({
                    key: entry.key,
                    isAccessor: false,
                    value: val,
                    writable: entry.writable,
                    enumerable: entry.enumerable,
                    configurable: entry.configurable,
                  })
                );
              }
              return Object.freeze(entries);
            }

            const witnessMutantCases: Array<{
              serverWitness: any;
              clientWitness: any;
              cloneServerVal?: any;
              cloneClientVal?: any;
              expectedMessage: string;
            }> = [
              { serverWitness: "1", clientWitness: outcome.clientRoutesWitness, cloneServerVal: "1", expectedMessage: "server route witness must be a number" },
              { serverWitness: NaN, clientWitness: outcome.clientRoutesWitness, cloneServerVal: NaN, expectedMessage: "server route witness must be finite" },
              { serverWitness: Infinity, clientWitness: outcome.clientRoutesWitness, cloneServerVal: Infinity, expectedMessage: "server route witness must be finite" },
              { serverWitness: -Infinity, clientWitness: outcome.clientRoutesWitness, cloneServerVal: -Infinity, expectedMessage: "server route witness must be finite" },
              { serverWitness: 1.5, clientWitness: outcome.clientRoutesWitness, cloneServerVal: 1.5, expectedMessage: "server route witness must be an integer" },
              { serverWitness: -1.5, clientWitness: outcome.clientRoutesWitness, cloneServerVal: -1.5, expectedMessage: "server route witness must be an integer" },
              { serverWitness: 0, clientWitness: outcome.clientRoutesWitness, cloneServerVal: 0, expectedMessage: "server route witness must be positive" },
              { serverWitness: -1, clientWitness: outcome.clientRoutesWitness, cloneServerVal: -1, expectedMessage: "server route witness must be positive" },
              { serverWitness: outcome.serverRoutesWitness, clientWitness: 0, cloneClientVal: 0, expectedMessage: "client route witness must be positive" },
              { serverWitness: 0, clientWitness: 0, cloneServerVal: 0, cloneClientVal: 0, expectedMessage: "server route witness must be positive" },
            ];

            for (const witnessCase of witnessMutantCases) {
              const isolatedWitnessUrls = new Set<string>();
              const mutatedBefore = cloneSnapshotWithModifiedVectors(outcome.diagBeforeP3, {
                serverValue: witnessCase.cloneServerVal,
                clientValue: witnessCase.cloneClientVal,
              });
              const mutatedAfter = cloneSnapshotWithModifiedVectors(outcome.diagAfterP3, {
                serverValue: witnessCase.cloneServerVal,
                clientValue: witnessCase.cloneClientVal,
              });
              const mutantOutcomeWitness = Object.freeze({
                ...outcome,
                serverRoutesWitness: witnessCase.serverWitness,
                clientRoutesWitness: witnessCase.clientWitness,
                diagBeforeP3: mutatedBefore,
                diagAfterP3: mutatedAfter,
                diagnosticsDeltaZero: true,
              });
              let witnessErr: unknown = null;
              try {
                assertRowDSnapshotContract(mutantOutcomeWitness, scenario, isolatedWitnessUrls);
              } catch (err: unknown) {
                witnessErr = err;
              }
              expect(witnessErr instanceof Error).toBe(true);
              expect((witnessErr as Error).message).toBe(witnessCase.expectedMessage);
            }

            // Section C.2: Adversarial runner controls on fresh evidence records
            {
              // 1. Both delegates fulfill -> both fulfilled true
              const ev1 = createFreshEmergencyCleanupEvidence();
              const events1: string[] = [];
              await runEmergencyCleanupWithEvidence(
                ev1,
                async () => { events1.push("unroute"); },
                async () => { events1.push("close"); },
                1000
              );
              expect(events1).toEqual(["unroute", "close"]);
              expect(Object.isFrozen(ev1)).toBe(true);
              expect(ev1.unrouteAttemptedCount).toBe(1);
              expect(ev1.unrouteSettledCount).toBe(1);
              expect(ev1.unrouteFulfilled).toBe(true);
              expect(ev1.closeAttemptedCount).toBe(1);
              expect(ev1.closeSettledCount).toBe(1);
              expect(ev1.closeFulfilled).toBe(true);
              expect(ev1.completed).toBe(true);

              // Blocker C.4: assertEmergencyCleanupRunnerAwaited local controls
              expect(() => assertEmergencyCleanupRunnerAwaited(ev1)).not.toThrow();

              let nullAwaitErr: unknown = null;
              try {
                assertEmergencyCleanupRunnerAwaited(null);
              } catch (err: unknown) {
                nullAwaitErr = err;
              }
              expect(nullAwaitErr instanceof Error).toBe(true);
              expect((nullAwaitErr as Error).message).toBe("emergency cleanup runner must be awaited");

              const unfrozenEv = { ...ev1, completed: true };
              let unfrozenAwaitErr: unknown = null;
              try {
                assertEmergencyCleanupRunnerAwaited(unfrozenEv);
              } catch (err: unknown) {
                unfrozenAwaitErr = err;
              }
              expect(unfrozenAwaitErr instanceof Error).toBe(true);
              expect((unfrozenAwaitErr as Error).message).toBe("emergency cleanup runner must be awaited");

              const truthyNumberEv = Object.freeze({ ...ev1, completed: 1 as unknown as boolean });
              let truthyAwaitErr: unknown = null;
              try {
                assertEmergencyCleanupRunnerAwaited(truthyNumberEv);
              } catch (err: unknown) {
                truthyAwaitErr = err;
              }
              expect(truthyAwaitErr instanceof Error).toBe(true);
              expect((truthyAwaitErr as Error).message).toBe("emergency cleanup runner must be awaited");

              // 2. Unroute throws undefined (synchronously), close fulfills -> unroute false, close true
              const ev2 = createFreshEmergencyCleanupEvidence();
              const events2: string[] = [];
              await runEmergencyCleanupWithEvidence(
                ev2,
                () => {
                  events2.push("unroute");
                  throw undefined;
                },
                async () => { events2.push("close"); },
                1000
              );
              expect(events2).toEqual(["unroute", "close"]);
              expect(Object.isFrozen(ev2)).toBe(true);
              expect(ev2.unrouteAttemptedCount).toBe(1);
              expect(ev2.unrouteSettledCount).toBe(1);
              expect(ev2.unrouteFulfilled).toBe(false);
              expect(ev2.closeAttemptedCount).toBe(1);
              expect(ev2.closeSettledCount).toBe(1);
              expect(ev2.closeFulfilled).toBe(true);
              expect(ev2.completed).toBe(true);

              // 3. Unroute returns never-settling promise (watchdog timeout) and close fulfills
              const ev3 = createFreshEmergencyCleanupEvidence();
              const events3: string[] = [];
              let timerId3: NodeJS.Timeout | null = null;
              await runEmergencyCleanupWithEvidence(
                ev3,
                () => new Promise<void>((resolve) => {
                  events3.push("unroute");
                  timerId3 = setTimeout(resolve, 5000);
                }),
                async () => { events3.push("close"); },
                50
              );
              if (timerId3 !== null) clearTimeout(timerId3);
              expect(events3).toEqual(["unroute", "close"]);
              expect(Object.isFrozen(ev3)).toBe(true);
              expect(ev3.unrouteAttemptedCount).toBe(1);
              expect(ev3.unrouteSettledCount).toBe(1);
              expect(ev3.unrouteFulfilled).toBe(false);
              expect(ev3.closeAttemptedCount).toBe(1);
              expect(ev3.closeSettledCount).toBe(1);
              expect(ev3.closeFulfilled).toBe(true);
              expect(ev3.completed).toBe(true);

              // 4. Unroute fulfills and close throws null (synchronously) -> unroute true, close false
              const ev4 = createFreshEmergencyCleanupEvidence();
              const events4: string[] = [];
              await runEmergencyCleanupWithEvidence(
                ev4,
                async () => { events4.push("unroute"); },
                () => {
                  events4.push("close");
                  throw null;
                },
                1000
              );
              expect(events4).toEqual(["unroute", "close"]);
              expect(Object.isFrozen(ev4)).toBe(true);
              expect(ev4.unrouteAttemptedCount).toBe(1);
              expect(ev4.unrouteSettledCount).toBe(1);
              expect(ev4.unrouteFulfilled).toBe(true);
              expect(ev4.closeAttemptedCount).toBe(1);
              expect(ev4.closeSettledCount).toBe(1);
              expect(ev4.closeFulfilled).toBe(false);
              expect(ev4.completed).toBe(true);
            }

            // Section C.4: Direct evidence-assertion mutants passed through assertEmergencyCleanupEvidence
            {
              const validEv = outcome.emergencyEvidence;

              // Blocker A: blank accessor mutant
              const blankAccessorMutant = Object.create(Object.prototype);
              for (const k of EXPECTED_EMERGENCY_EVIDENCE_KEYS) {
                if (k === "unrouteAttemptedCount") {
                  Object.defineProperty(blankAccessorMutant, "unrouteAttemptedCount", {
                    get: undefined,
                    set: undefined,
                    enumerable: true,
                    configurable: true,
                  });
                } else {
                  const desc = Object.getOwnPropertyDescriptor(validEv, k)!;
                  Object.defineProperty(blankAccessorMutant, k, desc);
                }
              }
              Object.freeze(blankAccessorMutant);

              const evidenceMutantCases: Array<{ mutant: any; expectedMessage: string }> = [
                {
                  mutant: blankAccessorMutant,
                  expectedMessage: "emergency cleanup evidence keys invalid",
                },
                {
                  mutant: Object.freeze({ ...validEv, unrouteAttemptedCount: 0 }),
                  expectedMessage: "emergency unroute must be attempted exactly once",
                },
                {
                  mutant: Object.freeze({ ...validEv, closeAttemptedCount: 0 }),
                  expectedMessage: "emergency close must be attempted exactly once",
                },
                {
                  mutant: Object.freeze({ ...validEv, completed: false }),
                  expectedMessage: "emergency cleanup must complete before outcome",
                },
                {
                  mutant: Object.freeze({ ...validEv, extraKey: 1 }),
                  expectedMessage: "emergency cleanup evidence keys invalid",
                },
              ];

              for (const evCase of evidenceMutantCases) {
                let evErr: unknown = null;
                try {
                  assertEmergencyCleanupEvidence(evCase.mutant, scenario.expectedEmergencyUnrouteFulfilled, true);
                } catch (err: unknown) {
                  evErr = err;
                }
                expect(evErr instanceof Error).toBe(true);
                expect((evErr as Error).message).toBe(evCase.expectedMessage);
              }
            }

            // Combined caller-path emergency evidence priority mutant
            {
              const priorityEvidenceMutant = Object.freeze({
                ...outcome.emergencyEvidence,
                unrouteAttemptedCount: 0,
              });
              const priorityOutcomeMutant = Object.freeze({
                ...outcome,
                emergencyEvidence: priorityEvidenceMutant,
                p1p2Identical: false,
              });
              const isolatedPriorityUrls = new Set<string>();
              let callerPriorityErr: unknown = null;
              try {
                assertRowDSnapshotContract(priorityOutcomeMutant, scenario, isolatedPriorityUrls);
              } catch (err: unknown) {
                callerPriorityErr = err;
              }
              expect(callerPriorityErr instanceof Error).toBe(true);
              expect((callerPriorityErr as Error).message).toBe("emergency unroute must be attempted exactly once");
              expect(isolatedPriorityUrls.size).toBe(0);
            }
          }
        }
      }
    } finally {
      for (const ctx of trackedContexts) {
        await withWatchdog(ctx.close(), 3000, "final context close").catch(() => {});
      }
    }
  });

  test("assertExactNetworkGuardTeardownError rejects descriptor flag mutations, own name, and hostile getters without invocation", async () => {
    // Reuse pristineNetworkGuardError directly
    assertExactNetworkGuardTeardownError(pristineNetworkGuardError, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline);

    // 1. Mutant: own name property added (data)
    const mutantOwnName = new Error("Network guard teardown failure");
    Object.defineProperty(mutantOwnName, "stack", pristineStackBaseline);
    mutantOwnName.name = "Error";
    expect(() => assertExactNetworkGuardTeardownError(mutantOwnName, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    // 2. Mutant: hostile name getter & setter with invocation counters 0
    let nameGetterCalled = 0;
    const mutantNameGetter = new Error("Network guard teardown failure");
    Object.defineProperty(mutantNameGetter, "stack", pristineStackBaseline);
    Object.defineProperty(mutantNameGetter, "name", {
      get: () => {
        nameGetterCalled++;
        return "Error";
      },
      enumerable: false,
      configurable: true,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantNameGetter, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
    expect(nameGetterCalled).toBe(0);

    let nameSetterCalled = 0;
    const mutantNameSetter = new Error("Network guard teardown failure");
    Object.defineProperty(mutantNameSetter, "stack", pristineStackBaseline);
    Object.defineProperty(mutantNameSetter, "name", {
      set: () => {
        nameSetterCalled++;
      },
      enumerable: false,
      configurable: true,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantNameSetter, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
    expect(nameSetterCalled).toBe(0);

    // 3. Mutant: hostile message getter & setter with invocation counters 0
    let msgGetterCalled = 0;
    const mutantMsgGetter = new Error("Network guard teardown failure");
    Object.defineProperty(mutantMsgGetter, "stack", pristineStackBaseline);
    Object.defineProperty(mutantMsgGetter, "message", {
      get: () => {
        msgGetterCalled++;
        return "Network guard teardown failure";
      },
      enumerable: false,
      configurable: true,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantMsgGetter, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
    expect(msgGetterCalled).toBe(0);

    let msgSetterCalled = 0;
    const mutantMsgSetter = new Error("Network guard teardown failure");
    Object.defineProperty(mutantMsgSetter, "stack", pristineStackBaseline);
    Object.defineProperty(mutantMsgSetter, "message", {
      set: () => {
        msgSetterCalled++;
      },
      enumerable: false,
      configurable: true,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantMsgSetter, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
    expect(msgSetterCalled).toBe(0);

    // 4. Mutant: message flag mutations, value mutation, non-string, deletion
    const mutantMsgEnum = new Error("Network guard teardown failure");
    Object.defineProperty(mutantMsgEnum, "stack", pristineStackBaseline);
    Object.defineProperty(mutantMsgEnum, "message", {
      value: "Network guard teardown failure",
      writable: pristineMsgBaseline.writable,
      enumerable: !pristineMsgBaseline.enumerable,
      configurable: pristineMsgBaseline.configurable,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantMsgEnum, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    const mutantMsgWritable = new Error("Network guard teardown failure");
    Object.defineProperty(mutantMsgWritable, "stack", pristineStackBaseline);
    Object.defineProperty(mutantMsgWritable, "message", {
      value: "Network guard teardown failure",
      writable: !pristineMsgBaseline.writable,
      enumerable: pristineMsgBaseline.enumerable,
      configurable: pristineMsgBaseline.configurable,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantMsgWritable, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    const mutantMsgConfig = new Error("Network guard teardown failure");
    Object.defineProperty(mutantMsgConfig, "stack", pristineStackBaseline);
    Object.defineProperty(mutantMsgConfig, "message", {
      value: "Network guard teardown failure",
      writable: pristineMsgBaseline.writable,
      enumerable: pristineMsgBaseline.enumerable,
      configurable: !pristineMsgBaseline.configurable,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantMsgConfig, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    const mutantMsgVal = new Error("wrong message");
    Object.defineProperty(mutantMsgVal, "stack", pristineStackBaseline);
    expect(() => assertExactNetworkGuardTeardownError(mutantMsgVal, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    const mutantMsgKind = new Error("Network guard teardown failure");
    Object.defineProperty(mutantMsgKind, "stack", pristineStackBaseline);
    Object.defineProperty(mutantMsgKind, "message", {
      value: 12345,
      writable: true,
      enumerable: false,
      configurable: true,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantMsgKind, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    const mutantMsgDel = new Error("Network guard teardown failure");
    Object.defineProperty(mutantMsgDel, "stack", pristineStackBaseline);
    delete (mutantMsgDel as any).message;
    expect(() => assertExactNetworkGuardTeardownError(mutantMsgDel, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    // 5. Demonstrated passing synthetic accessor baseline and one-dimension hostile accessor mutants (runs on every runtime)
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

    const validSyntheticAccessor = new Error("Network guard teardown failure");
    Object.defineProperty(validSyntheticAccessor, "stack", syntheticAccessorPassingDesc);
    assertExactNetworkGuardTeardownError(
      validSyntheticAccessor,
      [],
      pristineNetworkGuardOwnKeys,
      syntheticAccessorPassingDesc,
      pristineMsgBaseline
    );
    expect(syntheticBaselineGetterCalls).toBe(0);
    expect(syntheticBaselineSetterCalls).toBe(0);

    let hostileGetterCalls = 0;
    const mutantHostileGetter = new Error("Network guard teardown failure");
    Object.defineProperty(mutantHostileGetter, "stack", {
      ...syntheticAccessorPassingDesc,
      get: () => { hostileGetterCalls++; return "HOSTILE_STACK"; },
    });
    expect(() =>
      assertExactNetworkGuardTeardownError(
        mutantHostileGetter,
        [],
        pristineNetworkGuardOwnKeys,
        syntheticAccessorPassingDesc,
        pristineMsgBaseline
      )
    ).toThrow();
    expect(hostileGetterCalls).toBe(0);

    let hostileSetterCalls = 0;
    const mutantHostileSetter = new Error("Network guard teardown failure");
    Object.defineProperty(mutantHostileSetter, "stack", {
      ...syntheticAccessorPassingDesc,
      set: (_val: any) => { hostileSetterCalls++; },
    });
    expect(() =>
      assertExactNetworkGuardTeardownError(
        mutantHostileSetter,
        [],
        pristineNetworkGuardOwnKeys,
        syntheticAccessorPassingDesc,
        pristineMsgBaseline
      )
    ).toThrow();
    expect(hostileSetterCalls).toBe(0);

    // 6. Synthetic setter-only passing control and wrong-setter-identity mutant (runs on every runtime)
    let syntheticSetterOnlyCalls = 0;
    const syntheticSetterOnly = (_val: any) => { syntheticSetterOnlyCalls++; };
    const syntheticSetterOnlyDesc: PropertyDescriptor = {
      get: undefined,
      set: syntheticSetterOnly,
      enumerable: false,
      configurable: true,
    };
    const validSetterOnly = new Error("Network guard teardown failure");
    Object.defineProperty(validSetterOnly, "stack", syntheticSetterOnlyDesc);
    assertExactNetworkGuardTeardownError(validSetterOnly, [], pristineNetworkGuardOwnKeys, syntheticSetterOnlyDesc, pristineMsgBaseline);
    expect(syntheticSetterOnlyCalls).toBe(0);

    let badSetterOnlyCalled = 0;
    const mutantAccessorSetterOnly = new Error("Network guard teardown failure");
    Object.defineProperty(mutantAccessorSetterOnly, "stack", {
      ...syntheticSetterOnlyDesc,
      set: () => { badSetterOnlyCalled++; },
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantAccessorSetterOnly, [], pristineNetworkGuardOwnKeys, syntheticSetterOnlyDesc, pristineMsgBaseline)).toThrow();
    expect(badSetterOnlyCalled).toBe(0);

    // 7. Accessor stack mutants if pristine stack is accessor
    const isRuntimeAccessor = "get" in pristineStackBaseline || "set" in pristineStackBaseline;

    if (isRuntimeAccessor) {
      // Passing control with captured exact accessor descriptor
      const validAccessorStack = new Error("Network guard teardown failure");
      Object.defineProperty(validAccessorStack, "stack", { ...pristineStackBaseline });
      assertExactNetworkGuardTeardownError(validAccessorStack, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline);

      let badGetterCalled = 0;
      const mutantAccessorGetId = new Error("Network guard teardown failure");
      Object.defineProperty(mutantAccessorGetId, "stack", {
        ...pristineStackBaseline,
        get: () => { badGetterCalled++; return "DIFFERENT_GETTER"; },
      });
      expect(() => assertExactNetworkGuardTeardownError(mutantAccessorGetId, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
      expect(badGetterCalled).toBe(0);

      let badSetterCalled = 0;
      const mutantAccessorSetId = new Error("Network guard teardown failure");
      Object.defineProperty(mutantAccessorSetId, "stack", {
        ...pristineStackBaseline,
        set: () => { badSetterCalled++; },
      });
      expect(() => assertExactNetworkGuardTeardownError(mutantAccessorSetId, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
      expect(badSetterCalled).toBe(0);

      const mutantAccessorEnum = new Error("Network guard teardown failure");
      Object.defineProperty(mutantAccessorEnum, "stack", {
        ...pristineStackBaseline,
        enumerable: !pristineStackBaseline.enumerable,
      });
      expect(() => assertExactNetworkGuardTeardownError(mutantAccessorEnum, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

      const mutantAccessorConfig = new Error("Network guard teardown failure");
      Object.defineProperty(mutantAccessorConfig, "stack", {
        ...pristineStackBaseline,
        configurable: !pristineStackBaseline.configurable,
      });
      expect(() => assertExactNetworkGuardTeardownError(mutantAccessorConfig, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

      const mutantDataOnAccessor = new Error("Network guard teardown failure");
      Object.defineProperty(mutantDataOnAccessor, "stack", {
        value: "data stack on accessor",
        writable: true,
        enumerable: false,
        configurable: true,
      });
      expect(() => assertExactNetworkGuardTeardownError(mutantDataOnAccessor, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
    } else {
      // 8. Stack data mutants: safe data string passes; non-string, object, sentinel, deletion fail; independent single-flag flips
      const safeDataStackErr = new Error("Network guard teardown failure");
      Object.defineProperty(safeDataStackErr, "stack", {
        value: "Error: Network guard teardown failure\n    at safe",
        writable: pristineStackBaseline.writable,
        enumerable: pristineStackBaseline.enumerable,
        configurable: pristineStackBaseline.configurable,
      });
      assertExactNetworkGuardTeardownError(safeDataStackErr, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline);

      const nonStringStackErr = new Error("Network guard teardown failure");
      Object.defineProperty(nonStringStackErr, "stack", {
        ...pristineStackBaseline,
        value: 12345,
      });
      expect(() => assertExactNetworkGuardTeardownError(nonStringStackErr, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

      const objectStackErr = new Error("Network guard teardown failure");
      Object.defineProperty(objectStackErr, "stack", {
        ...pristineStackBaseline,
        value: { raw: "error" },
      });
      expect(() => assertExactNetworkGuardTeardownError(objectStackErr, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

      const sentinelStackErr = new Error("Network guard teardown failure");
      Object.defineProperty(sentinelStackErr, "stack", {
        ...pristineStackBaseline,
        value: "Error with FORBIDDEN_SENTINEL",
      });
      expect(() => assertExactNetworkGuardTeardownError(sentinelStackErr, ["FORBIDDEN_SENTINEL"], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

      const deletedStackErr = new Error("Network guard teardown failure");
      delete (deletedStackErr as any).stack;
      expect(() => assertExactNetworkGuardTeardownError(deletedStackErr, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

      const mutantDataStackWritable = new Error("Network guard teardown failure");
      Object.defineProperty(mutantDataStackWritable, "stack", {
        ...pristineStackBaseline,
        writable: !pristineStackBaseline.writable,
      });
      expect(() => assertExactNetworkGuardTeardownError(mutantDataStackWritable, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

      const mutantDataStackEnum = new Error("Network guard teardown failure");
      Object.defineProperty(mutantDataStackEnum, "stack", {
        ...pristineStackBaseline,
        enumerable: !pristineStackBaseline.enumerable,
      });
      expect(() => assertExactNetworkGuardTeardownError(mutantDataStackEnum, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

      const mutantDataStackConfig = new Error("Network guard teardown failure");
      Object.defineProperty(mutantDataStackConfig, "stack", {
        ...pristineStackBaseline,
        configurable: !pristineStackBaseline.configurable,
      });
      expect(() => assertExactNetworkGuardTeardownError(mutantDataStackConfig, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
    }

    // 9. Cause mutants: data cause & hostile cause getter & hostile cause setter
    const mutantCause = new Error("Network guard teardown failure");
    Object.defineProperty(mutantCause, "stack", pristineStackBaseline);
    (mutantCause as any).cause = new Error("inner");
    expect(() => assertExactNetworkGuardTeardownError(mutantCause, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    let causeGetterCalled = 0;
    const mutantCauseGetter = new Error("Network guard teardown failure");
    Object.defineProperty(mutantCauseGetter, "stack", pristineStackBaseline);
    Object.defineProperty(mutantCauseGetter, "cause", {
      get: () => {
        causeGetterCalled++;
        return "HOSTILE_CAUSE";
      },
      enumerable: false,
      configurable: true,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantCauseGetter, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
    expect(causeGetterCalled).toBe(0);

    let causeSetterCalled = 0;
    const mutantCauseSetter = new Error("Network guard teardown failure");
    Object.defineProperty(mutantCauseSetter, "stack", pristineStackBaseline);
    Object.defineProperty(mutantCauseSetter, "cause", {
      set: () => {
        causeSetterCalled++;
      },
      enumerable: false,
      configurable: true,
    });
    expect(() => assertExactNetworkGuardTeardownError(mutantCauseSetter, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
    expect(causeSetterCalled).toBe(0);

    // 10. Details / metadata / extra object / Symbol property mutants
    const mutantDetails = new Error("Network guard teardown failure");
    Object.defineProperty(mutantDetails, "stack", pristineStackBaseline);
    (mutantDetails as any).details = { reason: "test" };
    expect(() => assertExactNetworkGuardTeardownError(mutantDetails, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    const mutantMetadata = new Error("Network guard teardown failure");
    Object.defineProperty(mutantMetadata, "stack", pristineStackBaseline);
    (mutantMetadata as any).metadata = { info: "test" };
    expect(() => assertExactNetworkGuardTeardownError(mutantMetadata, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    const mutantExtraProp = new Error("Network guard teardown failure");
    Object.defineProperty(mutantExtraProp, "stack", pristineStackBaseline);
    (mutantExtraProp as any).extraObj = { extra: "test" };
    expect(() => assertExactNetworkGuardTeardownError(mutantExtraProp, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();

    const mutantSymbol = new Error("Network guard teardown failure");
    Object.defineProperty(mutantSymbol, "stack", pristineStackBaseline);
    (mutantSymbol as any)[Symbol("guard")] = true;
    expect(() => assertExactNetworkGuardTeardownError(mutantSymbol, [], pristineNetworkGuardOwnKeys, pristineStackBaseline, pristineMsgBaseline)).toThrow();
  });
});
