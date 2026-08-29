import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  IAP_EXPIRY_CLOSE_CODE,
  IAP_LOGOUT_MAX_WAIT_MS,
  cancelLifecycleRetry,
  commitIfCurrent,
  iapAdmissionAttemptIsCurrent,
  emitIapHttpTerminalIfNeeded,
  iapHttpResponseDisposition,
  boundedRetryDelay,
  commitAsyncResource,
  createLifecycleGenerationController,
  isIapAttemptCurrent,
  isIapReconnectableClose,
  isIapTerminalClose,
  isIapTerminalHttpStatus,
  scheduleLifecycleRetry,
  type LifecycleTimerApi,
} from "./iapLifecycle.ts";
import { runIapLogoutLifecycle } from "./iapSession.ts";
import { IAP_API_ORIGIN } from "./runtimeConfig.ts";

test("IAP terminal policy is distinct from reconnectable expiry", () => {
  assert.equal(isIapTerminalHttpStatus(401), true);
  assert.equal(isIapTerminalHttpStatus(403), true);
  assert.equal(isIapTerminalHttpStatus(500), false);
  assert.equal(iapHttpResponseDisposition(401), "terminal");
  assert.equal(iapHttpResponseDisposition(403), "terminal");
  assert.equal(iapHttpResponseDisposition(500), "retryable");
  assert.equal(iapHttpResponseDisposition(0), "retryable");
  let terminalEvents = 0;
  assert.equal(emitIapHttpTerminalIfNeeded(401, true, () => terminalEvents++), true);
  assert.equal(emitIapHttpTerminalIfNeeded(403, true, () => terminalEvents++), true);
  assert.equal(emitIapHttpTerminalIfNeeded(500, true, () => terminalEvents++), false);
  assert.equal(emitIapHttpTerminalIfNeeded(401, false, () => terminalEvents++), false);
  assert.equal(terminalEvents, 2);
  assert.equal(isIapTerminalClose(1008, "kill_switch"), true);
  assert.equal(isIapTerminalClose(4003, "auth_revoked"), true);
  assert.equal(isIapTerminalClose(1006, "policy violation"), true);
  assert.equal(isIapReconnectableClose(IAP_EXPIRY_CLOSE_CODE, "auth_expired"), true);
  assert.equal(isIapReconnectableClose(1006, "network reset"), true);
});

test("lifecycle generation rejects stale async completions", () => {
  assert.equal(isIapAttemptCurrent(4, 4, "s1", "s1"), true);
  assert.equal(isIapAttemptCurrent(4, 5, "s1", "s1"), false);
  assert.equal(isIapAttemptCurrent(4, 4, "s1", "s2"), false);
  const controller = new AbortController();
  assert.equal(iapAdmissionAttemptIsCurrent(4, 4, controller.signal), true);
  controller.abort();
  assert.equal(iapAdmissionAttemptIsCurrent(4, 4, controller.signal), false);
});

test("generation controller and resource commit dispose stale completions", async () => {
  const controller = createLifecycleGenerationController();
  const first = controller.begin();
  const second = controller.begin();
  assert.equal(controller.isCurrent(first), false);
  assert.equal(controller.isCurrent(second), true);
  const disposed: string[] = [];
  const committed = await commitAsyncResource(
    "first",
    () => controller.isCurrent(first),
    (resource) => disposed.push(resource),
  );
  assert.equal(committed, null);
  assert.deepEqual(disposed, ["first"]);
  assert.equal(boundedRetryDelay(1000, 30000), 2000);
  assert.equal(boundedRetryDelay(30000, 30000), 30000);
});

test("deferred terminal socket completion is disposed and never committed", async () => {
  const controller = createLifecycleGenerationController();
  const attempt = controller.begin();
  let resolveSocket!: (socket: { close: () => void }) => void;
  const pendingSocket = new Promise<{ close: () => void }>((resolve) => {
    resolveSocket = resolve;
  });
  controller.invalidate();
  const socket = { closeCalls: 0, close() { this.closeCalls += 1; } };
  resolveSocket(socket);
  const committed = await pendingSocket.then((lateSocket) =>
    commitIfCurrent(lateSocket, () => controller.isCurrent(attempt), (stale) => stale.close()),
  );
  assert.equal(committed, null);
  assert.equal(socket.closeCalls, 1);
});

test("deferred terminal media completion disposes tracks and graph context", async () => {
  const controller = createLifecycleGenerationController();
  const attempt = controller.begin();
  let resolveMedia!: (media: { tracks: { stop: () => void }[]; close: () => void }) => void;
  const pendingMedia = new Promise<{ tracks: { stop: () => void }[]; close: () => void }>((resolve) => {
    resolveMedia = resolve;
  });
  controller.invalidate();
  let stopped = 0;
  let closed = 0;
  const media = {
    tracks: [{ stop: () => { stopped += 1; } }],
    close: () => { closed += 1; },
  };
  resolveMedia(media);
  const committed = await pendingMedia.then((lateMedia) =>
    commitIfCurrent(lateMedia, () => controller.isCurrent(attempt), (stale) => {
      stale.tracks.forEach((track) => track.stop());
      stale.close();
    }),
  );
  assert.equal(committed, null);
  assert.equal(stopped, 1);
  assert.equal(closed, 1);
});

test("terminal invalidation makes a scheduled retry timer inert", () => {
  const controller = createLifecycleGenerationController();
  const attempt = controller.begin();
  const pending = new Map<number, () => void>();
  let nextTimer = 0;
  const timerApi: LifecycleTimerApi<number> = {
    setTimeout: (callback) => {
      const id = ++nextTimer;
      pending.set(id, callback);
      return id;
    },
    clearTimeout: (id) => pending.delete(id),
  };
  let retries = 0;
  const timer = scheduleLifecycleRetry(
    () => controller.isCurrent(attempt),
    100,
    () => { retries += 1; },
    timerApi,
  );
  assert.equal(timer, 1);
  controller.invalidate();
  pending.get(timer!)?.();
  assert.equal(retries, 0);
  cancelLifecycleRetry(timer, timerApi);
  assert.equal(pending.has(timer!), false);
});

test("terminal fencing disposes an overlapping late resource and cancels retry admission", async () => {
  const controller = createLifecycleGenerationController();
  const firstGeneration = controller.begin();
  const firstResource = { tracksStopped: 0 };
  const secondGeneration = controller.begin();
  const disposed: object[] = [];
  const committed = await commitAsyncResource(
    firstResource,
    () => controller.isCurrent(firstGeneration),
    (resource) => {
      resource.tracksStopped += 1;
      disposed.push(resource);
    },
  );
  assert.equal(committed, null);
  assert.equal(firstResource.tracksStopped, 1);
  assert.deepEqual(disposed, [firstResource]);

  const admission = new AbortController();
  assert.equal(
    iapAdmissionAttemptIsCurrent(
      secondGeneration,
      controller.current(),
      admission.signal,
    ),
    true,
  );
  admission.abort();
  assert.equal(
    iapAdmissionAttemptIsCurrent(
      secondGeneration,
      controller.current(),
      admission.signal,
    ),
    false,
  );
  assert.equal(isIapReconnectableClose(4003, "kill_switch"), false);
  assert.equal(isIapReconnectableClose(4001, "auth_expired"), true);
});

test("logout navigates after its bounded wait when the backend hangs", async () => {
  const events: string[] = [];
  const started = runIapLogoutLifecycle(
    { apiOrigin: IAP_API_ORIGIN },
    {
      cleanup: () => events.push("cleanup"),
      navigate: () => events.push("navigate"),
      maxWaitMs: 1,
    },
    async (_input, init) => {
      events.push("request");
      assert.equal(init?.signal?.aborted, false);
      return new Promise<Response>(() => {});
    },
  );
  await started;
  assert.deepEqual(events, ["cleanup", "request", "navigate"]);
  assert.ok(IAP_LOGOUT_MAX_WAIT_MS > 0);
});

test("production hooks and auth/page are wired to terminal lifecycle primitives", () => {
  const root = dirname(dirname(fileURLToPath(import.meta.url)));
  const read = (...parts: string[]) => readFileSync(join(root, ...parts), "utf8");
  const websocket = read("hooks", "useWebSocket.ts");
  const audio = read("hooks", "useBrowserAudioCapture.ts");
  const auth = read("lib", "auth.ts");
  const page = read("app", "page.tsx");
  const attemptGuard = websocket.slice(
    websocket.indexOf("const attemptIsCurrent"),
    websocket.indexOf("// Clear previous state"),
  );
  const graphGuard = audio.slice(
    audio.indexOf("const graphIsCurrent"),
    audio.indexOf("let stream: MediaStream | null"),
  );
  const apiFetchBody = auth.slice(
    auth.indexOf("export async function apiFetch"),
    auth.indexOf("export function useAuth"),
  );
  const terminalAuthBody = auth.slice(
    auth.indexOf("const onTerminalAuth = () => {"),
    auth.indexOf("window.addEventListener(\"tars:iap-auth-terminal\""),
  );
  assert.match(websocket, /isIapTerminalHttpStatus/);
  assert.match(websocket, /lifecycleAttemptIsCurrent/);
  assert.match(websocket, /emitIapTerminalAuthEvent/);
  assert.match(websocket, /createLifecycleGenerationController/);
  assert.match(websocket, /boundedRetryDelay/);
  assert.match(websocket, /scheduleLifecycleRetry\(/);
  assert.match(websocket, /commitIfCurrent\(/);
  assert.match(attemptGuard, /lifecycleAttemptIsCurrent/);
  assert.doesNotMatch(attemptGuard, /=>\s*true\s*;?/);
  assert.match(audio, /isIapTerminalClose/);
  assert.match(audio, /audioGraphGenerationRef/);
  assert.match(audio, /emitIapTerminalAuthEvent/);
  assert.match(audio, /commitAsyncResource\(/);
  assert.match(audio, /scheduleLifecycleRetry\(/);
  assert.match(audio, /commitIfCurrent\(/);
  assert.match(audio, /graphAttemptGeneration/);
  assert.match(graphGuard, /lifecycleAttemptIsCurrent/);
  assert.doesNotMatch(graphGuard, /=>\s*true\s*;?/);
  assert.match(auth, /tars:iap-auth-terminal/);
  assert.match(auth, /iapAdmissionAttemptIsCurrent/);
  assert.match(
    apiFetchBody,
    /emitIapHttpTerminalIfNeeded\([\s\S]*response\.status[\s\S]*runtimeConfig\.iap[\s\S]*emitIapTerminalAuthEvent/,
  );
  assert.match(terminalAuthBody, /admissionGenerationRef\.current\s*\+=\s*1/);
  assert.match(terminalAuthBody, /admissionAbortRef\.current\?\.abort\(\)/);
  assert.match(terminalAuthBody, /setUser\(null\)/);
  assert.match(terminalAuthBody, /setStatus\("revoked"\)/);
  assert.match(audio, /const onTerminalAuth = \(\) => stopStreaming\(\)/);
  const websocketTerminalBody = websocket.slice(
    websocket.indexOf("const onTerminalAuth = () => {"),
    websocket.indexOf("window.addEventListener(IAP_AUTH_TERMINAL_EVENT"),
  );
  assert.match(websocketTerminalBody, /lifecycleControllerRef\.current\.invalidate/);
  assert.match(websocketTerminalBody, /connectAbortRef\.current\?\.abort/);
  assert.match(auth, /runIapLogoutLifecycle/);
  assert.match(page, /audioCapture\.stopStreaming\(\)/);
  assert.match(page, /useAuth\(\)/);
  assert.match(page, /const browserStreamKey = authIapEnabled \? undefined : sessionStreamKey/);
  assert.match(page, /startStreaming\(id, browserStreamKey\)/);
  assert.match(page, /streamKey=\{authIapEnabled \? undefined : streamKey/);
});
