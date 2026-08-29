"use client";

import {
  getApps,
  initializeApp,
  type FirebaseApp,
  type FirebaseOptions,
} from "firebase/app";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  type Auth,
  type User,
  type UserCredential,
} from "firebase/auth";

export const GOOGLE_PROVIDER_ID = "google.com";
const IAP_CALLBACK_ORIGIN = "https://iap.googleapis.com";

const IAP_OPERATION_MODES = [
  "login",
  "reauth",
  "signout",
  "selectAuthSession",
] as const;

export type IapOperationMode = (typeof IAP_OPERATION_MODES)[number];

export interface IapLoginRequest {
  mode: IapOperationMode;
  apiKey: string;
  redirectUri: string | null;
  state: string | null;
  tenantId: string | null;
}

export interface FirebaseWebConfig extends FirebaseOptions {
  apiKey: string;
  authDomain: string;
  projectId: string;
  storageBucket: string;
  messagingSenderId: string;
  appId: string;
}

export interface FirebaseWebEnvironment {
  NEXT_PUBLIC_FIREBASE_API_KEY?: string;
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN?: string;
  NEXT_PUBLIC_FIREBASE_PROJECT_ID?: string;
  NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET?: string;
  NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID?: string;
  NEXT_PUBLIC_FIREBASE_APP_ID?: string;
}

interface IapTenantInfo {
  email?: string;
  tenantId: string | null;
  providerIds?: string[];
}

interface GoogleOnlyAuthHandlerHooks {
  onSignInReady?: () => void;
  onProgressChange?: (visible: boolean) => void;
  onCompleteSignOut?: () => void;
  onError?: (error: unknown) => void;
}

export interface GoogleOnlyAuthenticationHandler {
  languageCode?: string | null;
  getAuth: (apiKey: string, tenantId: string | null) => Auth;
  startSignIn: (
    auth: Auth,
    match?: IapTenantInfo,
  ) => Promise<UserCredential>;
  completeSignOut: () => Promise<void>;
  processUser: (user: User) => Promise<User>;
  showProgressBar: () => void;
  hideProgressBar: () => void;
  handleError: (error: unknown) => void;
}

export interface GoogleOnlyAuthenticationController {
  handler: GoogleOnlyAuthenticationHandler;
  requestSignIn: () => boolean;
  dispose: () => void;
}

export type GoogleSignIn = (auth: Auth) => Promise<UserCredential>;

const MAX_REQUEST_VALUE_LENGTH = 4096;
const MAX_TENANT_ID_LENGTH = 128;
const IAP_CALLBACK_PATH_PREFIX = "/v1beta1/gcip/resources/";
const IAP_CALLBACK_PATH_SUFFIX = ":handleRedirect";
const INVALID_PUBLIC_VALUE = /^(?:\$\{|<|REPLACE_|YOUR_|TODO\b)/i;

function hasSafeScalar(value: unknown, maxLength = MAX_REQUEST_VALUE_LENGTH): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maxLength &&
    value === value.trim() &&
    !/[\u0000-\u001f\u007f]/.test(value) &&
    !INVALID_PUBLIC_VALUE.test(value)
  );
}

function hasSafeHostname(value: unknown): value is string {
  if (!hasSafeScalar(value, 253) || value.includes("/")) return false;
  try {
    const parsed = new URL(`https://${value}`);
    return (
      parsed.hostname === value.toLowerCase() &&
      parsed.username === "" &&
      parsed.password === "" &&
      parsed.pathname === "/" &&
      parsed.search === "" &&
      parsed.hash === ""
    );
  } catch {
    return false;
  }
}

function environmentFromProcess(): FirebaseWebEnvironment {
  return {
    NEXT_PUBLIC_FIREBASE_API_KEY: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID:
      process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
    NEXT_PUBLIC_FIREBASE_APP_ID: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
  };
}

/** Return a complete, structurally valid web config or null; never guess values. */
export function readFirebaseWebConfig(
  environment: FirebaseWebEnvironment = environmentFromProcess(),
): FirebaseWebConfig | null {
  const apiKey = environment.NEXT_PUBLIC_FIREBASE_API_KEY;
  const authDomain = environment.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN;
  const projectId = environment.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
  const storageBucket = environment.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET;
  const messagingSenderId = environment.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID;
  const appId = environment.NEXT_PUBLIC_FIREBASE_APP_ID;

  if (
    !hasSafeScalar(apiKey) ||
    !hasSafeHostname(authDomain) ||
    !hasSafeScalar(projectId, 128) ||
    !/^[a-zA-Z0-9][a-zA-Z0-9-]{2,127}$/.test(projectId) ||
    !hasSafeHostname(storageBucket) ||
    !hasSafeScalar(messagingSenderId, 32) ||
    !/^[0-9]+$/.test(messagingSenderId) ||
    !hasSafeScalar(appId, 256) ||
    !/^1:[0-9]+:[-a-zA-Z0-9]+:[-a-zA-Z0-9]+$/.test(appId)
  ) {
    return null;
  }

  return {
    apiKey,
    authDomain,
    projectId,
    storageBucket,
    messagingSenderId,
    appId,
  };
}

function getExactlyOne(searchParams: URLSearchParams, key: string): string | null {
  const values = searchParams.getAll(key);
  return values.length === 1 ? values[0] ?? null : null;
}

function getOptionalExactlyOne(
  searchParams: URLSearchParams,
  key: string,
): { valid: boolean; value: string | null } {
  const values = searchParams.getAll(key);
  if (values.length > 1) return { valid: false, value: null };
  return { valid: true, value: values[0] ?? null };
}

function parseRedirectUri(value: string): string | null {
  if (!hasSafeScalar(value) || value.includes("?") || value.includes("#")) return null;
  try {
    const parsed = new URL(value);
    if (value !== parsed.toString()) return null;
    if (
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash
    ) {
      return null;
    }
    // IAP supplies an opaque callback resource. Bind to the exact provider
    // origin and route; reject encoded path components to avoid ambiguity.
    const resourceSegment = parsed.pathname.slice(
      IAP_CALLBACK_PATH_PREFIX.length,
      -IAP_CALLBACK_PATH_SUFFIX.length,
    );
    if (
      parsed.protocol !== "https:" ||
      parsed.origin !== IAP_CALLBACK_ORIGIN ||
      parsed.pathname.includes("%") ||
      !parsed.pathname.startsWith(IAP_CALLBACK_PATH_PREFIX) ||
      !parsed.pathname.endsWith(IAP_CALLBACK_PATH_SUFFIX) ||
      resourceSegment.length === 0 ||
      resourceSegment.includes("/")
    ) {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

function isSupportedOperationMode(value: string | null): value is IapOperationMode {
  return value !== null && IAP_OPERATION_MODES.includes(value as IapOperationMode);
}

/** Parse only the request shape required by gcip-iap; malformed requests stop before SDK startup. */
export function parseIapLoginRequest(input: string | URL): IapLoginRequest | null {
  let url: URL;
  try {
    url = typeof input === "string" ? new URL(input) : input;
  } catch {
    return null;
  }

  const modeValue = getExactlyOne(url.searchParams, "mode");
  const apiKey = getExactlyOne(url.searchParams, "apiKey");
  if (!isSupportedOperationMode(modeValue) || !hasSafeScalar(apiKey)) return null;

  const redirectParam = getOptionalExactlyOne(url.searchParams, "redirect_uri");
  const stateParam = getOptionalExactlyOne(url.searchParams, "state");
  const tenantParam = getOptionalExactlyOne(url.searchParams, "tid");
  if (!redirectParam.valid || !stateParam.valid || !tenantParam.valid) return null;
  const redirectValue = redirectParam.value;
  const state = stateParam.value;
  const redirectUri = redirectValue === null ? null : parseRedirectUri(redirectValue);
  if (redirectValue !== null && redirectUri === null) return null;
  if (
    (modeValue === "login" ||
      modeValue === "reauth" ||
      modeValue === "selectAuthSession") &&
    (redirectUri === null || !hasSafeScalar(state))
  ) {
    return null;
  }
  if (
    modeValue === "signout" &&
    ((redirectUri === null) !== (state === null))
  ) {
    return null;
  }

  const tenantId = tenantParam.value;
  if (
    tenantId !== null &&
    (!hasSafeScalar(tenantId, MAX_TENANT_ID_LENGTH) ||
      !/^[a-zA-Z0-9_-]+$/.test(tenantId))
  ) {
    return null;
  }

  return {
    mode: modeValue,
    apiKey,
    redirectUri,
    state,
    tenantId,
  };
}

export function isGoogleOnlyProviderSelection(providerIds: unknown): boolean {
  return (
    Array.isArray(providerIds) &&
    providerIds.length === 1 &&
    providerIds[0] === GOOGLE_PROVIDER_ID
  );
}

/** Reject provider hints that would allow gcip-iap to select another identity provider. */
export function assertGoogleOnlyProviderSelection(
  match: IapTenantInfo | undefined,
): void {
  if (match === undefined) return;
  if (
    (match.tenantId !== null &&
      (!hasSafeScalar(match.tenantId, MAX_TENANT_ID_LENGTH) ||
        !/^[a-zA-Z0-9_-]+$/.test(match.tenantId))) ||
    (match.providerIds !== undefined &&
      !(
        Array.isArray(match.providerIds) &&
        match.providerIds.length === 0
      ) &&
      !isGoogleOnlyProviderSelection(match.providerIds))
  ) {
    throw new Error("Only the Google identity provider is supported.");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasGoogleActiveProvider(tokenResult: unknown): boolean {
  if (!isRecord(tokenResult) || !isRecord(tokenResult.claims)) return false;
  const firebaseClaims = tokenResult.claims.firebase;
  return (
    isRecord(firebaseClaims) &&
    firebaseClaims.sign_in_provider === GOOGLE_PROVIDER_ID
  );
}

function assertAppMatchesConfig(app: FirebaseApp, config: FirebaseWebConfig): void {
  const options = app.options;
  for (const key of [
    "apiKey",
    "authDomain",
    "projectId",
    "storageBucket",
    "messagingSenderId",
    "appId",
  ] as const) {
    if (options[key] !== config[key]) {
      throw new Error("Firebase configuration does not match the requested project.");
    }
  }
}

function tenantAppName(tenantId: string | null): string {
  return tenantId === null ? "[DEFAULT]" : `tars-iap-${tenantId}`;
}

function getOrInitializeTenantApp(
  config: FirebaseWebConfig,
  tenantId: string | null,
): FirebaseApp {
  const name = tenantAppName(tenantId);
  const existing = getApps().find((candidate) => candidate.name === name);
  if (existing) {
    assertAppMatchesConfig(existing, config);
    return existing;
  }
  const app = name === "[DEFAULT]"
    ? initializeApp(config)
    : initializeApp(config, name);
  assertAppMatchesConfig(app, config);
  return app;
}

function createConfiguredAuth(
  config: FirebaseWebConfig,
  apiKey: string,
  tenantId: string | null,
): Auth {
  if (apiKey !== config.apiKey) {
    throw new Error("The IAP project does not match the configured Firebase project.");
  }
  if (
    tenantId !== null &&
    (!hasSafeScalar(tenantId, MAX_TENANT_ID_LENGTH) ||
      !/^[a-zA-Z0-9_-]+$/.test(tenantId))
  ) {
    throw new Error("The IAP tenant identifier is malformed.");
  }
  const auth = getAuth(getOrInitializeTenantApp(config, tenantId));
  auth.tenantId = tenantId;
  return auth;
}

export async function signInWithGoogle(auth: Auth): Promise<UserCredential> {
  const provider = new GoogleAuthProvider();
  return signInWithPopup(auth, provider);
}

function safeErrorMessage(error: unknown): string {
  void error;
  return "Não foi possível concluir a autenticação Google.";
}

export function createGoogleOnlyAuthenticationHandler(
  config: FirebaseWebConfig,
  hooks: GoogleOnlyAuthHandlerHooks = {},
  signIn: GoogleSignIn = signInWithGoogle,
): GoogleOnlyAuthenticationController {
  if (readFirebaseWebConfig({
    NEXT_PUBLIC_FIREBASE_API_KEY: config.apiKey,
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: config.authDomain,
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: config.projectId,
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: config.storageBucket,
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: config.messagingSenderId,
    NEXT_PUBLIC_FIREBASE_APP_ID: config.appId,
  }) === null) {
    throw new Error("Firebase web configuration is absent or malformed.");
  }

  type PendingSignIn = {
    auth: Auth;
    resolve: (credential: UserCredential) => void;
    reject: (error: unknown) => void;
    started: boolean;
  };
  let pending: PendingSignIn | null = null;
  let disposed = false;

  const assertActive = (): void => {
    if (disposed) throw new Error("The authentication handler is disposed.");
  };

  const handler: GoogleOnlyAuthenticationHandler = {
    languageCode: null,
    getAuth: (apiKey, tenantId) => {
      assertActive();
      return createConfiguredAuth(config, apiKey, tenantId);
    },
    startSignIn: (auth, match) => {
      assertActive();
      assertGoogleOnlyProviderSelection(match);
      if (pending !== null) {
        return Promise.reject(new Error("A Google sign-in attempt is already active."));
      }
      hooks.onSignInReady?.();
      return new Promise<UserCredential>((resolve, reject) => {
        pending = { auth, resolve, reject, started: false };
      });
    },
    completeSignOut: async () => {
      if (disposed) return;
      pending = null;
      hooks.onCompleteSignOut?.();
    },
    processUser: async (user) => {
      assertActive();
      const tokenResult = await user.getIdTokenResult();
      assertActive();
      if (!hasGoogleActiveProvider(tokenResult)) {
        throw new Error("The authenticated account did not use Google.");
      }
      return user;
    },
    showProgressBar: () => {
      if (!disposed) hooks.onProgressChange?.(true);
    },
    hideProgressBar: () => {
      if (!disposed) hooks.onProgressChange?.(false);
    },
    handleError: (error) => {
      if (!disposed) hooks.onError?.(error);
    },
  };

  return {
    handler,
    requestSignIn: () => {
      if (disposed) return false;
      const current = pending;
      if (current === null || current.started) return false;
      current.started = true;
      try {
        void signIn(current.auth).then(
          (credential) => {
            if (disposed || pending !== current) return;
            pending = null;
            current.resolve(credential);
          },
          (error: unknown) => {
            if (disposed || pending !== current) return;
            pending = null;
            current.reject(error);
          },
        );
      } catch (error) {
        if (disposed || pending !== current) return false;
        pending = null;
        current.reject(error);
      }
      return true;
    },
    dispose: () => {
      if (disposed) return;
      disposed = true;
      const current = pending;
      pending = null;
      current?.reject(new Error("The authentication page was closed."));
    },
  };
}

export { safeErrorMessage };
