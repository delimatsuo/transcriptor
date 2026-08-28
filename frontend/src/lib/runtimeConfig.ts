"use client";

/** Pure, single authority for frontend auth/API/WebSocket runtime settings. */

export type AuthMode = "firebase" | "iap";

export const IAP_FRONTEND_ORIGIN = "https://tars.ellaexecutivesearch.com";
export const IAP_API_ORIGIN = "https://api.tars.ellaexecutivesearch.com";
export const IAP_WS_URL = `wss://api.tars.ellaexecutivesearch.com/ws`;
export const IAP_STREAM_WS_URL = `wss://api.tars.ellaexecutivesearch.com/api/stream/native`;

export interface RuntimeEnvironment {
  NODE_ENV?: string;
  NEXT_PUBLIC_AUTH_MODE?: string;
  NEXT_PUBLIC_AUTH_BYPASS?: string;
  NEXT_PUBLIC_API_URL?: string;
  NEXT_PUBLIC_WS_URL?: string;
  NEXT_PUBLIC_WS_STREAM_URL?: string;
  NEXT_PUBLIC_FRONTEND_ORIGIN?: string;
}

export interface RuntimeConfig {
  authMode: AuthMode;
  authBypass: boolean;
  apiOrigin: string;
  wsUrl: string;
  streamWsUrl: string;
  frontendOrigin: string;
  credentials: RequestCredentials;
  iap: boolean;
}

const LOCAL_API_ORIGIN = "http://127.0.0.1:8000";
const LOCAL_WS_URL = "ws://127.0.0.1:8000/ws";
const LOCAL_STREAM_WS_URL = "ws://127.0.0.1:8000/api/stream/native";

function parseOrigin(value: string, label: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${label} must be an absolute URL.`);
  }
  if (
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    parsed.pathname !== "/"
  ) {
    throw new Error(`${label} must be an origin without credentials, path, query, or fragment.`);
  }
  return parsed;
}

function parseSocketUrl(value: string, label: string, expectedPath?: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${label} must be an absolute WebSocket URL.`);
  }
  if (
    parsed.protocol !== "ws:" &&
    parsed.protocol !== "wss:"
  ) {
    throw new Error(`${label} must use ws or wss.`);
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(`${label} must not include credentials, query, or fragment.`);
  }
  if (expectedPath && parsed.pathname !== expectedPath) {
    throw new Error(`${label} must use the direct ${expectedPath} path.`);
  }
  return parsed;
}

function isTruthy(value: string | undefined): boolean {
  return value === "1" || value === "true";
}

export function parseRuntimeConfig(
  environment: RuntimeEnvironment = {},
): RuntimeConfig {
  const authMode = environment.NEXT_PUBLIC_AUTH_MODE || "firebase";
  if (authMode !== "firebase" && authMode !== "iap") {
    throw new Error("NEXT_PUBLIC_AUTH_MODE must be exactly firebase or iap.");
  }
  const authBypass = isTruthy(environment.NEXT_PUBLIC_AUTH_BYPASS);
  const apiOrigin = environment.NEXT_PUBLIC_API_URL || LOCAL_API_ORIGIN;
  const wsUrl = environment.NEXT_PUBLIC_WS_URL || LOCAL_WS_URL;
  const streamWsUrl = environment.NEXT_PUBLIC_WS_STREAM_URL || LOCAL_STREAM_WS_URL;
  const frontendOrigin =
    environment.NEXT_PUBLIC_FRONTEND_ORIGIN ||
    (authMode === "firebase" && environment.NODE_ENV === "production"
      ? IAP_FRONTEND_ORIGIN
      : "http://localhost:3000");

  const api = parseOrigin(apiOrigin, "NEXT_PUBLIC_API_URL");
  const ws = parseSocketUrl(wsUrl, "NEXT_PUBLIC_WS_URL");
  const stream = parseSocketUrl(streamWsUrl, "NEXT_PUBLIC_WS_STREAM_URL");
  const frontend = parseOrigin(frontendOrigin, "NEXT_PUBLIC_FRONTEND_ORIGIN");

  if (authMode === "iap") {
    if (authBypass) {
      throw new Error("Authentication bypass is prohibited in IAP mode.");
    }
    if (
      apiOrigin !== IAP_API_ORIGIN ||
      wsUrl !== IAP_WS_URL ||
      streamWsUrl !== IAP_STREAM_WS_URL ||
      frontendOrigin !== IAP_FRONTEND_ORIGIN
    ) {
      throw new Error("IAP mode requires the approved direct production origins.");
    }
    if (
      api.protocol !== "https:" ||
      ws.protocol !== "wss:" ||
      stream.protocol !== "wss:" ||
      api.hostname !== "api.tars.ellaexecutivesearch.com" ||
      ws.hostname !== api.hostname ||
      stream.hostname !== api.hostname ||
      frontend.hostname !== "tars.ellaexecutivesearch.com" ||
      ws.pathname !== "/ws" ||
      stream.pathname !== "/api/stream/native"
    ) {
      throw new Error("IAP mode requires same-site HTTPS/WSS direct paths.");
    }
  }

  return {
    authMode,
    authBypass,
    apiOrigin: api.origin,
    wsUrl: ws.toString().replace(/\/$/, ""),
    streamWsUrl: stream.toString().replace(/\/$/, ""),
    frontendOrigin: frontend.origin,
    credentials: "include",
    iap: authMode === "iap",
  };
}

export function getRuntimeConfig(): RuntimeConfig {
  return parseRuntimeConfig({
    NODE_ENV: process.env.NODE_ENV,
    NEXT_PUBLIC_AUTH_MODE: process.env.NEXT_PUBLIC_AUTH_MODE,
    NEXT_PUBLIC_AUTH_BYPASS: process.env.NEXT_PUBLIC_AUTH_BYPASS,
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL,
    NEXT_PUBLIC_WS_STREAM_URL: process.env.NEXT_PUBLIC_WS_STREAM_URL,
    NEXT_PUBLIC_FRONTEND_ORIGIN: process.env.NEXT_PUBLIC_FRONTEND_ORIGIN,
  });
}
