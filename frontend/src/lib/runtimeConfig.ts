/**
 * Runtime configuration validation and URL destination helpers.
 */

export interface FirebasePublicConfig {
  apiKey: string;
  authDomain: string;
  projectId: string;
  storageBucket: string;
  messagingSenderId: string;
  appId: string;
}

export interface PublicRuntimeConfig {
  authBypassEnabled: boolean;
  firebase?: FirebasePublicConfig;
  apiUrl: string;
  wsUrl: string;
  wsStreamUrl: string;
}

export type Result<T, E = string> =
  | { ok: true; value: T }
  | { ok: false; error: E };

export class PublicRuntimeConfigError extends Error {
  constructor(message = "Runtime configuration invalid or missing") {
    super(message);
    this.name = "PublicRuntimeConfigError";
  }
}

const API_KEY_PATTERN = /^AIza[A-Za-z0-9_-]{35}$/;
const PROJECT_ID_PATTERN = /^[a-z][a-z0-9-]{4,28}[a-z0-9]$/;
const SENDER_ID_PATTERN = /^[0-9]{6,20}$/;
const APP_ID_PATTERN = /^1:[0-9]{6,20}:web:[A-Za-z0-9_-]{8,128}$/;

function hasPlaceholder(val: string): boolean {
  const lower = val.toLowerCase();
  return (
    lower.includes("<") ||
    lower.includes(">") ||
    lower.includes("your-") ||
    lower.includes("example")
  );
}

function isValidDnsHostname(hostname: string): boolean {
  if (!hostname || hostname.length > 253 || hostname !== hostname.trim()) {
    return false;
  }
  if (/[\u0000-\u001F\u007F-\uFFFF]/.test(hostname) || hostname.includes("_") || hostname.includes(":") || hostname.includes("/")) {
    return false;
  }
  const labels = hostname.split(".");
  if (labels.length < 2) {
    return false;
  }
  for (const label of labels) {
    if (!label || label.length > 63) {
      return false;
    }
    if (label.startsWith("-") || label.endsWith("-")) {
      return false;
    }
    if (!/^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$/.test(label)) {
      return false;
    }
  }
  return true;
}

function isValidStorageBucket(bucket: string): boolean {
  if (!bucket || bucket.length > 253 || bucket !== bucket.trim()) {
    return false;
  }
  if (/[\u0000-\u001F\u007F-\uFFFF]/.test(bucket) || bucket.includes("_") || bucket.includes(":") || bucket.includes("/")) {
    return false;
  }
  const labels = bucket.split(".");
  if (labels.length < 2) {
    return false;
  }
  for (const label of labels) {
    if (!label || label.length > 63) {
      return false;
    }
    if (label.startsWith("-") || label.endsWith("-")) {
      return false;
    }
    if (!/^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$/.test(label)) {
      return false;
    }
  }
  return true;
}

function extractRawSchemeAndAuthority(raw: string): { scheme: string; rawHost: string; rawPort: string } | null {
  const match = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/([^/?#]+)/.exec(raw);
  if (!match) return null;
  const scheme = match[1];
  const authority = match[2];
  if (authority.includes("@")) return null;
  if (authority.startsWith("[")) {
    const endBracket = authority.indexOf("]");
    if (endBracket === -1) return null;
    const rawHost = authority.slice(0, endBracket + 1);
    const rest = authority.slice(endBracket + 1);
    if (rest.startsWith(":")) {
      return { scheme, rawHost, rawPort: rest.slice(1) };
    } else if (rest === "") {
      return { scheme, rawHost, rawPort: "" };
    }
    return null;
  }
  const parts = authority.split(":");
  if (parts.length === 1) {
    return { scheme, rawHost: parts[0], rawPort: "" };
  }
  if (parts.length === 2) {
    return { scheme, rawHost: parts[0], rawPort: parts[1] };
  }
  return null;
}

const EXACT_ALLOWED_DEV_LOOPBACK_RAW_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function isExactAllowedDevLoopback(rawUrl: string, parsed: URL): boolean {
  const info = extractRawSchemeAndAuthority(rawUrl);
  if (!info) return false;
  if (info.scheme !== "http" && info.scheme !== "ws") return false;
  if (!EXACT_ALLOWED_DEV_LOOPBACK_RAW_HOSTS.has(info.rawHost)) return false;
  if (info.rawPort && !/^[0-9]{1,5}$/.test(info.rawPort)) return false;
  return parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1" || parsed.hostname === "::1" || parsed.hostname === "[::1]";
}

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

function isLoopback(hostname: string): boolean {
  const norm = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return LOOPBACK_HOSTS.has(hostname.toLowerCase()) || LOOPBACK_HOSTS.has(norm);
}

function validateRawUrlString(raw: string): string | null {
  if (!raw || typeof raw !== "string") {
    return "URL must be a non-empty string";
  }
  if (raw !== raw.trim()) {
    return "URL must not contain surrounding whitespace";
  }
  if (/[\u0000-\u001F\u007F-\uFFFF]/.test(raw)) {
    return "URL must contain only ASCII characters without controls";
  }
  if (raw.includes("\\")) {
    return "Backslash is prohibited in base URL configuration";
  }
  if (raw.includes("%")) {
    return "Percent encoding is prohibited in base URL configuration";
  }
  if (raw.startsWith("//")) {
    return "Protocol-relative URL is prohibited";
  }
  if (raw.includes("@")) {
    return "Userinfo/credentials are prohibited in base URL configuration";
  }
  if (raw.includes("?") || raw.includes("#")) {
    return "Query and fragment delimiters are prohibited in base URL configuration";
  }
  if (raw.includes("0.0.0.0")) {
    return "0.0.0.0 is prohibited in URL configuration";
  }
  return null;
}

function extractRawPath(raw: string): string {
  const match = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/[^/?#]+(.*)/.exec(raw);
  if (!match) return "";
  const afterAuth = match[2];
  const qIdx = afterAuth.search(/[?#]/);
  return qIdx === -1 ? afterAuth : afterAuth.slice(0, qIdx);
}

/**
 * Pure parser for frontend public runtime configuration.
 */
export function parsePublicRuntimeConfig(
  rawEnv?: Record<string, string | undefined>
): Result<PublicRuntimeConfig, string> {
  const env = rawEnv || {
    NODE_ENV: process.env.NODE_ENV,
    NEXT_PUBLIC_AUTH_BYPASS: process.env.NEXT_PUBLIC_AUTH_BYPASS,
    NEXT_PUBLIC_FIREBASE_API_KEY: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
    NEXT_PUBLIC_FIREBASE_APP_ID: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL,
    NEXT_PUBLIC_WS_STREAM_URL: process.env.NEXT_PUBLIC_WS_STREAM_URL,
  };

  const isProd = env.NODE_ENV === "production";
  const rawBypass = env.NEXT_PUBLIC_AUTH_BYPASS;

  // In production, bypass is strictly false regardless of variable.
  // In dev/test, bypass is true if and only if exact "1".
  const authBypassEnabled = !isProd && rawBypass === "1";

  let firebaseConfig: FirebasePublicConfig | undefined;

  if (!authBypassEnabled) {
    const rawApiKey = env.NEXT_PUBLIC_FIREBASE_API_KEY;
    const rawAuthDomain = env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN;
    const rawProjectId = env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
    const rawBucket = env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET;
    const rawSenderId = env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID;
    const rawAppId = env.NEXT_PUBLIC_FIREBASE_APP_ID;

    if (!rawApiKey || rawApiKey !== rawApiKey.trim() || !API_KEY_PATTERN.test(rawApiKey) || hasPlaceholder(rawApiKey)) {
      return { ok: false, error: "Invalid or missing NEXT_PUBLIC_FIREBASE_API_KEY" };
    }
    if (!rawAuthDomain || !isValidDnsHostname(rawAuthDomain) || hasPlaceholder(rawAuthDomain)) {
      return { ok: false, error: "Invalid or missing NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN" };
    }
    if (!rawProjectId || rawProjectId !== rawProjectId.trim() || !PROJECT_ID_PATTERN.test(rawProjectId) || hasPlaceholder(rawProjectId)) {
      return { ok: false, error: "Invalid or missing NEXT_PUBLIC_FIREBASE_PROJECT_ID" };
    }
    if (!rawBucket || !isValidStorageBucket(rawBucket) || hasPlaceholder(rawBucket)) {
      return { ok: false, error: "Invalid or missing NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET" };
    }
    if (!rawSenderId || rawSenderId !== rawSenderId.trim() || !SENDER_ID_PATTERN.test(rawSenderId) || hasPlaceholder(rawSenderId)) {
      return { ok: false, error: "Invalid or missing NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID" };
    }
    if (!rawAppId || rawAppId !== rawAppId.trim() || !APP_ID_PATTERN.test(rawAppId) || hasPlaceholder(rawAppId)) {
      return { ok: false, error: "Invalid or missing NEXT_PUBLIC_FIREBASE_APP_ID" };
    }

    firebaseConfig = {
      apiKey: rawApiKey,
      authDomain: rawAuthDomain,
      projectId: rawProjectId,
      storageBucket: rawBucket,
      messagingSenderId: rawSenderId,
      appId: rawAppId,
    };
  }

  // URLs
  const rawApiUrl = env.NEXT_PUBLIC_API_URL ?? (isProd ? "" : "http://127.0.0.1:8000");
  const rawWsUrl = env.NEXT_PUBLIC_WS_URL ?? (isProd ? "" : "ws://127.0.0.1:8000/ws");
  const rawWsStreamUrl =
    env.NEXT_PUBLIC_WS_STREAM_URL ?? (isProd ? "" : "ws://127.0.0.1:8000/api/stream/native");

  const errApi = validateRawUrlString(rawApiUrl);
  if (errApi) return { ok: false, error: `NEXT_PUBLIC_API_URL: ${errApi}` };
  if (!rawApiUrl.startsWith("http://") && !rawApiUrl.startsWith("https://")) {
    return { ok: false, error: "NEXT_PUBLIC_API_URL: Scheme must be exact lowercase http or https" };
  }

  const errWs = validateRawUrlString(rawWsUrl);
  if (errWs) return { ok: false, error: `NEXT_PUBLIC_WS_URL: ${errWs}` };
  if (!rawWsUrl.startsWith("ws://") && !rawWsUrl.startsWith("wss://")) {
    return { ok: false, error: "NEXT_PUBLIC_WS_URL: Scheme must be exact lowercase ws or wss" };
  }

  const errWsStream = validateRawUrlString(rawWsStreamUrl);
  if (errWsStream) return { ok: false, error: `NEXT_PUBLIC_WS_STREAM_URL: ${errWsStream}` };
  if (!rawWsStreamUrl.startsWith("ws://") && !rawWsStreamUrl.startsWith("wss://")) {
    return { ok: false, error: "NEXT_PUBLIC_WS_STREAM_URL: Scheme must be exact lowercase ws or wss" };
  }

  const rawApiPath = extractRawPath(rawApiUrl);
  if (rawApiPath !== "" && rawApiPath !== "/") {
    return { ok: false, error: "API URL must have root path only" };
  }

  const rawWsPath = extractRawPath(rawWsUrl);
  if (rawWsPath !== "/ws") {
    return { ok: false, error: "WS URL pathname must be exactly /ws" };
  }

  const rawWsStreamPath = extractRawPath(rawWsStreamUrl);
  if (rawWsStreamPath !== "/api/stream/native") {
    return { ok: false, error: "WS stream URL pathname must be exactly /api/stream/native" };
  }

  let parsedApi: URL;
  let parsedWs: URL;
  let parsedWsStream: URL;

  try {
    parsedApi = new URL(rawApiUrl);
    parsedWs = new URL(rawWsUrl);
    parsedWsStream = new URL(rawWsStreamUrl);
  } catch {
    return { ok: false, error: "Malformed URL in API or WS configuration" };
  }

  if (rawApiUrl.includes("://") && rawApiUrl.split("/")[2]?.endsWith(":")) {
    return { ok: false, error: "Empty port delimiter in NEXT_PUBLIC_API_URL" };
  }
  if (rawWsUrl.includes("://") && rawWsUrl.split("/")[2]?.endsWith(":")) {
    return { ok: false, error: "Empty port delimiter in NEXT_PUBLIC_WS_URL" };
  }
  if (rawWsStreamUrl.includes("://") && rawWsStreamUrl.split("/")[2]?.endsWith(":")) {
    return { ok: false, error: "Empty port delimiter in NEXT_PUBLIC_WS_STREAM_URL" };
  }

  // Scheme validation
  if (parsedApi.protocol !== "http:" && parsedApi.protocol !== "https:") {
    return { ok: false, error: "NEXT_PUBLIC_API_URL scheme must be http: or https:" };
  }
  if (parsedWs.protocol !== "ws:" && parsedWs.protocol !== "wss:") {
    return { ok: false, error: "NEXT_PUBLIC_WS_URL scheme must be ws: or wss:" };
  }
  if (parsedWsStream.protocol !== "ws:" && parsedWsStream.protocol !== "wss:") {
    return { ok: false, error: "NEXT_PUBLIC_WS_STREAM_URL scheme must be ws: or wss:" };
  }

  if (isProd) {
    if (parsedApi.protocol !== "https:") {
      return { ok: false, error: "Production NEXT_PUBLIC_API_URL must use https:" };
    }
    if (parsedWs.protocol !== "wss:") {
      return { ok: false, error: "Production NEXT_PUBLIC_WS_URL must use wss:" };
    }
    if (parsedWsStream.protocol !== "wss:") {
      return { ok: false, error: "Production NEXT_PUBLIC_WS_STREAM_URL must use wss:" };
    }
    if (isLoopback(parsedApi.hostname) || isLoopback(parsedWs.hostname) || isLoopback(parsedWsStream.hostname)) {
      return { ok: false, error: "Production URLs cannot use loopback hostnames" };
    }
  } else {
    // Non-production: http/ws permitted ONLY on loopback; https/wss permitted if consistent
    const isApiInsecure = parsedApi.protocol === "http:";
    const isWsInsecure = parsedWs.protocol === "ws:";
    const isWsStreamInsecure = parsedWsStream.protocol === "ws:";

    if (isApiInsecure && !isExactAllowedDevLoopback(rawApiUrl, parsedApi)) {
      return { ok: false, error: "Insecure http: API URL permitted only on exact literal loopback hosts" };
    }
    if (isWsInsecure && !isExactAllowedDevLoopback(rawWsUrl, parsedWs)) {
      return { ok: false, error: "Insecure ws: WebSocket URL permitted only on exact literal loopback hosts" };
    }
    if (isWsStreamInsecure && !isExactAllowedDevLoopback(rawWsStreamUrl, parsedWsStream)) {
      return { ok: false, error: "Insecure ws: stream URL permitted only on exact literal loopback hosts" };
    }
  }

  // Path validation
  // API URL must be root only
  if (parsedApi.pathname !== "/" && parsedApi.pathname !== "") {
    return { ok: false, error: "API URL must have root path only" };
  }
  // WS path must be exact /ws
  if (parsedWs.pathname !== "/ws") {
    return { ok: false, error: "WS URL pathname must be exactly /ws" };
  }
  // WS stream path must be exact /api/stream/native
  if (parsedWsStream.pathname !== "/api/stream/native") {
    return { ok: false, error: "WS stream URL pathname must be exactly /api/stream/native" };
  }

  // Consistency across host and effective port
  const apiEffectivePort = parsedApi.port || (parsedApi.protocol === "https:" ? "443" : "80");
  const wsEffectivePort = parsedWs.port || (parsedWs.protocol === "wss:" ? "443" : "80");
  const wsStreamEffectivePort = parsedWsStream.port || (parsedWsStream.protocol === "wss:" ? "443" : "80");

  if (
    parsedApi.hostname.toLowerCase() !== parsedWs.hostname.toLowerCase() ||
    parsedApi.hostname.toLowerCase() !== parsedWsStream.hostname.toLowerCase() ||
    apiEffectivePort !== wsEffectivePort ||
    apiEffectivePort !== wsStreamEffectivePort
  ) {
    return { ok: false, error: "API and WebSocket URLs must have consistent hostname and effective port" };
  }

  // Normalize only single trailing slash on apiUrl
  const cleanApiUrl = `${parsedApi.protocol}//${parsedApi.host}`;
  const cleanWsUrl = `${parsedWs.protocol}//${parsedWs.host}/ws`;
  const cleanWsStreamUrl = `${parsedWsStream.protocol}//${parsedWsStream.host}/api/stream/native`;

  return {
    ok: true,
    value: {
      authBypassEnabled,
      firebase: firebaseConfig,
      apiUrl: cleanApiUrl,
      wsUrl: cleanWsUrl,
      wsStreamUrl: cleanWsStreamUrl,
    },
  };
}

export const publicRuntimeConfigResult: Result<PublicRuntimeConfig, string> =
  parsePublicRuntimeConfig();

/**
 * Require validated public runtime configuration or throw fixed content-free error.
 */
export function requirePublicRuntimeConfig(): PublicRuntimeConfig {
  if (!publicRuntimeConfigResult.ok) {
    throw new PublicRuntimeConfigError();
  }
  return publicRuntimeConfigResult.value;
}

/**
 * Get validated public runtime config or throw if invalid.
 */
export function getPublicRuntimeConfig(): PublicRuntimeConfig {
  return requirePublicRuntimeConfig();
}

/**
 * Format a trusted backend API URL for a given relative /api path.
 */
export function apiUrl(path: string, config?: PublicRuntimeConfig): string {
  if (typeof path !== "string") {
    throw new PublicRuntimeConfigError("Invalid API path argument");
  }
  if (path !== path.trim()) {
    throw new PublicRuntimeConfigError("Whitespace prohibited in API path");
  }
  if (path.includes("\\") || /%5[cC]/i.test(path)) {
    throw new PublicRuntimeConfigError("Backslash prohibited in API path");
  }
  if (path.includes("#") || /%23/i.test(path)) {
    throw new PublicRuntimeConfigError("Fragment prohibited in API path");
  }
  if (path.includes("@")) {
    throw new PublicRuntimeConfigError("Userinfo prohibited in API path");
  }
  if (path.startsWith("//") || path.includes("//")) {
    throw new PublicRuntimeConfigError("Protocol-relative or consecutive slashes prohibited in API path");
  }
  const [pathPortion] = path.split("?");
  if (/%2[fF]/i.test(pathPortion)) {
    throw new PublicRuntimeConfigError("Encoded slash prohibited in API path");
  }
  if (/%2[eE]/i.test(pathPortion) || /\/\.\.(?:\/|$)/.test(pathPortion) || /\/\.(?:\/|$)/.test(pathPortion)) {
    throw new PublicRuntimeConfigError("Path traversal or dot segments prohibited in API path");
  }
  if (!path.startsWith("/api")) {
    throw new PublicRuntimeConfigError("API path must start with /api");
  }
  if (path !== "/api" && !path.startsWith("/api/")) {
    throw new PublicRuntimeConfigError("API path must start with /api/");
  }

  const cfg = config || requirePublicRuntimeConfig();
  const base = new URL(cfg.apiUrl);
  let resolved: URL;
  try {
    resolved = new URL(path, base);
  } catch {
    throw new PublicRuntimeConfigError("Malformed API path argument");
  }

  if (resolved.origin !== base.origin) {
    throw new PublicRuntimeConfigError("API path resolves to different origin");
  }
  if (resolved.username || resolved.password) {
    throw new PublicRuntimeConfigError("Userinfo in API path");
  }
  if (resolved.hash) {
    throw new PublicRuntimeConfigError("Fragment in API path");
  }

  const pathname = resolved.pathname;
  if (pathname !== "/api" && !pathname.startsWith("/api/")) {
    throw new PublicRuntimeConfigError("API path resolves outside /api");
  }

  return `${cfg.apiUrl}${path}`;
}

/**
 * Verify if a target destination matches the configured backend origin and /api route tree.
 */
export function isTrustedApiDestination(
  destination: RequestInfo | URL | string,
  config?: PublicRuntimeConfig
): boolean {
  try {
    const cfg = config || requirePublicRuntimeConfig();
    const base = new URL(cfg.apiUrl);

    let rawString = "";
    if (typeof destination === "string") {
      rawString = destination;
    } else if (destination instanceof URL) {
      rawString = destination.toString();
    } else if (typeof Request !== "undefined" && destination instanceof Request) {
      rawString = destination.url;
    } else if (destination && typeof (destination as { url?: string }).url === "string") {
      rawString = (destination as { url: string }).url;
    } else {
      return false;
    }

    if (rawString !== rawString.trim()) {
      return false;
    }
    if (!/^https?:\/\//i.test(rawString)) {
      return false;
    }
    if (rawString.includes("#") || /%23/i.test(rawString)) {
      return false;
    }
    if (rawString.includes("\\") || /%5[cC]/i.test(rawString)) {
      return false;
    }
    if (rawString.includes("@")) {
      return false;
    }
    if (rawString.startsWith("//")) {
      return false;
    }

    const [pathPortion] = rawString.split("?");
    if (/%2[fF]/i.test(pathPortion)) {
      return false;
    }
    if (/%2[eE]/i.test(pathPortion) || /\/\.\.(?:\/|$)/.test(pathPortion) || /\/\.(?:\/|$)/.test(pathPortion)) {
      return false;
    }

    const destUrl = new URL(rawString);
    if (destUrl.origin !== base.origin) {
      return false;
    }
    if (destUrl.username || destUrl.password) {
      return false;
    }
    if (destUrl.hash) {
      return false;
    }

    const pathname = destUrl.pathname;
    return pathname === "/api" || pathname.startsWith("/api/");
  } catch {
    return false;
  }
}
