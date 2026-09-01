"use client";

import { initializeApp, getApps, type FirebaseApp, type FirebaseOptions } from "firebase/app";
import { getAuth, GoogleAuthProvider, type Auth } from "firebase/auth";
import {
  publicRuntimeConfigResult,
  requirePublicRuntimeConfig,
  type FirebasePublicConfig,
  type PublicRuntimeConfig,
} from "./runtimeConfig";

export const APP_NAME = "tars-frontend";

export class FirebaseInitializationError extends Error {
  constructor(message = "Firebase initialization failed") {
    super(message);
    this.name = "FirebaseInitializationError";
  }
}

let appInstance: FirebaseApp | null = null;
let authInstance: Auth | null = null;
let googleProviderInstance: GoogleAuthProvider | null = null;

interface CleanFirebaseSnapshot {
  authBypassEnabled: boolean;
  firebase?: Readonly<{
    apiKey: string;
    authDomain: string;
    projectId: string;
    storageBucket: string;
    messagingSenderId: string;
    appId: string;
  }>;
}

function resolveFirebaseConfigSnapshot(
  overrideConfig?: PublicRuntimeConfig
): CleanFirebaseSnapshot | null {
  let threw = false;
  let rawSource: unknown = null;
  try {
    if (overrideConfig !== undefined) {
      rawSource = overrideConfig;
    } else {
      if (!publicRuntimeConfigResult.ok) {
        return null;
      }
      rawSource = publicRuntimeConfigResult.value;
    }

    if (!rawSource || typeof rawSource !== "object" || Array.isArray(rawSource)) {
      threw = true;
    } else {
      const proto = Object.getPrototypeOf(rawSource);
      if (proto !== Object.prototype && proto !== null) {
        threw = true;
      }
    }
  } catch {
    threw = true;
  }

  if (threw) {
    throw new FirebaseInitializationError("Invalid runtime configuration for Firebase");
  }

  let isBypass = false;
  let fbSnapshot: CleanFirebaseSnapshot["firebase"] = undefined;

  try {
    const src = rawSource as Record<string, unknown>;
    const rawBypass = src.authBypassEnabled;
    if (typeof rawBypass !== "boolean") {
      throw new Error();
    }
    isBypass = rawBypass;

    const rawFb = src.firebase;
    if (rawFb !== undefined) {
      if (!rawFb || typeof rawFb !== "object" || Array.isArray(rawFb)) {
        throw new Error();
      }
      const fbProto = Object.getPrototypeOf(rawFb);
      if (fbProto !== Object.prototype && fbProto !== null) {
        throw new Error();
      }
      const fbRec = rawFb as Record<string, unknown>;
      const apiKey = fbRec.apiKey;
      const authDomain = fbRec.authDomain;
      const projectId = fbRec.projectId;
      const storageBucket = fbRec.storageBucket;
      const messagingSenderId = fbRec.messagingSenderId;
      const appId = fbRec.appId;

      if (
        typeof apiKey !== "string" ||
        typeof authDomain !== "string" ||
        typeof projectId !== "string" ||
        typeof storageBucket !== "string" ||
        typeof messagingSenderId !== "string" ||
        typeof appId !== "string"
      ) {
        throw new Error();
      }

      fbSnapshot = Object.freeze({
        apiKey,
        authDomain,
        projectId,
        storageBucket,
        messagingSenderId,
        appId,
      });
    }
  } catch {
    threw = true;
  }

  if (threw) {
    throw new FirebaseInitializationError("Invalid runtime configuration for Firebase");
  }

  return Object.freeze({
    authBypassEnabled: isBypass,
    firebase: fbSnapshot,
  });
}

export function isFirebaseConfigured(): boolean {
  try {
    const config = resolveFirebaseConfigSnapshot();
    return Boolean(config && !config.authBypassEnabled && config.firebase);
  } catch {
    return false;
  }
}

export interface FirebaseAppDependencies {
  getApps?: () => Array<{ name: string; options: FirebaseOptions }>;
  initializeApp?: (options: FirebaseOptions, name: string) => FirebaseApp;
}

function validateAppMatchesConfig(
  app: unknown,
  expectedFb: CleanFirebaseSnapshot["firebase"],
  mode: "existing_list" | "created" | "auth"
): boolean {
  if (!app || typeof app !== "object") {
    if (mode === "created") throw new FirebaseInitializationError("Firebase app initialization failed");
    if (mode === "auth") throw new FirebaseInitializationError("Existing Firebase app options mismatch");
    throw new FirebaseInitializationError("Firebase app shape invalid");
  }

  // Two-snapshot stability check on name first
  let name1: unknown = undefined;
  let name2: unknown = undefined;
  let nameReadFailed = false;
  try {
    name1 = (app as { name?: unknown }).name;
    name2 = (app as { name?: unknown }).name;
  } catch {
    nameReadFailed = true;
  }

  if (nameReadFailed || typeof name1 !== "string" || typeof name2 !== "string" || name1 !== name2) {
    if (mode === "created") throw new FirebaseInitializationError("Firebase app initialization failed");
    if (mode === "auth") throw new FirebaseInitializationError("Existing Firebase app options mismatch");
    throw new FirebaseInitializationError("Firebase app shape invalid");
  }

  // Stable unrelated apps in existing list are skipped immediately without touching options
  if (name1 !== APP_NAME) {
    if (mode === "existing_list") {
      return false;
    }
    if (mode === "created") {
      throw new FirebaseInitializationError("Firebase app initialization failed");
    }
    if (mode === "auth") {
      throw new FirebaseInitializationError("Existing Firebase app options mismatch");
    }
  }

  // Two-snapshot stability check on options object identity
  let options1: unknown = undefined;
  let options2: unknown = undefined;
  let optionsReadFailed = false;
  try {
    options1 = (app as { options?: unknown }).options;
    options2 = (app as { options?: unknown }).options;
  } catch {
    optionsReadFailed = true;
  }

  if (
    optionsReadFailed ||
    !options1 ||
    typeof options1 !== "object" ||
    !options2 ||
    typeof options2 !== "object" ||
    options1 !== options2
  ) {
    if (mode === "created") throw new FirebaseInitializationError("Firebase app initialization failed");
    throw new FirebaseInitializationError("Existing Firebase app options mismatch");
  }

  if (!expectedFb) {
    if (mode === "created") throw new FirebaseInitializationError("Firebase app initialization failed");
    throw new FirebaseInitializationError("Existing Firebase app options mismatch");
  }

  const optRec1 = options1 as Record<string, unknown>;
  const optRec2 = options2 as Record<string, unknown>;

  const requiredKeys = [
    "apiKey",
    "authDomain",
    "projectId",
    "storageBucket",
    "messagingSenderId",
    "appId",
  ] as const;

  for (const key of requiredKeys) {
    let val1: unknown = undefined;
    let val2: unknown = undefined;
    let fieldReadFailed = false;
    try {
      val1 = optRec1[key];
      val2 = optRec2[key];
    } catch {
      fieldReadFailed = true;
    }

    if (
      fieldReadFailed ||
      typeof val1 !== "string" ||
      typeof val2 !== "string" ||
      val1 !== val2 ||
      val1 !== expectedFb[key]
    ) {
      if (mode === "created") throw new FirebaseInitializationError("Firebase app initialization failed");
      throw new FirebaseInitializationError("Existing Firebase app options mismatch");
    }
  }

  return true;
}

export function getFirebaseApp(
  deps?: FirebaseAppDependencies,
  overrideConfig?: PublicRuntimeConfig
): FirebaseApp | null {
  if (appInstance && !deps && !overrideConfig) {
    return appInstance;
  }

  const config = resolveFirebaseConfigSnapshot(overrideConfig);
  if (!config || config.authBypassEnabled || !config.firebase) {
    return null;
  }

  let getAppsFn: (() => unknown) | undefined;
  let initializeAppFn: ((options: any, name: string) => unknown) | undefined;
  try {
    getAppsFn = (deps?.getApps as any) ?? getApps;
    initializeAppFn = (deps?.initializeApp as any) ?? initializeApp;
  } catch {
    throw new FirebaseInitializationError("Firebase initialization failed");
  }

  let existingList: unknown = null;
  let getAppsThrew = false;
  try {
    if (getAppsFn) {
      existingList = getAppsFn();
    }
  } catch {
    getAppsThrew = true;
  }
  if (getAppsThrew) {
    throw new FirebaseInitializationError("Firebase app lookup failed");
  }

  if (!Array.isArray(existingList)) {
    throw new FirebaseInitializationError("Firebase app lookup failed");
  }

  let foundApp: unknown = null;

  for (const item of existingList) {
    if (!item || typeof item !== "object") {
      throw new FirebaseInitializationError("Firebase app shape invalid");
    }
    const isMatch = validateAppMatchesConfig(item, config.firebase, "existing_list");
    if (isMatch) {
      foundApp = item;
      break;
    }
  }

  if (foundApp) {
    if (!deps && !overrideConfig) {
      appInstance = foundApp as FirebaseApp;
    }
    return foundApp as FirebaseApp;
  }

  let createdApp: unknown = null;
  let initThrew = false;
  try {
    if (initializeAppFn) {
      createdApp = initializeAppFn(config.firebase as FirebaseOptions, APP_NAME);
    }
  } catch {
    initThrew = true;
  }

  if (initThrew || !createdApp || typeof createdApp !== "object") {
    throw new FirebaseInitializationError("Firebase app initialization failed");
  }

  validateAppMatchesConfig(createdApp, config.firebase, "created");

  if (!deps && !overrideConfig) {
    appInstance = createdApp as FirebaseApp;
  }
  return createdApp as FirebaseApp;
}

export interface FirebaseAuthDependencies {
  getApp?: typeof getFirebaseApp;
  getAuth?: (app: FirebaseApp) => Auth;
}

export function getFirebaseAuth(
  deps?: FirebaseAuthDependencies,
  overrideConfig?: PublicRuntimeConfig
): Auth | null {
  if (authInstance && !deps && !overrideConfig) {
    return authInstance;
  }

  const config = resolveFirebaseConfigSnapshot(overrideConfig);
  if (!config || config.authBypassEnabled || !config.firebase) {
    return null;
  }

  let getAppFn: ((deps?: unknown, conf?: unknown) => unknown) | undefined;
  let getAuthFn: ((app: unknown) => unknown) | undefined;
  try {
    getAppFn = (deps?.getApp as any) ?? getFirebaseApp;
    getAuthFn = (deps?.getAuth as any) ?? getAuth;
  } catch {
    throw new FirebaseInitializationError("Firebase initialization failed");
  }

  let app: unknown = null;
  let appLookupThrew = false;
  try {
    if (getAppFn) {
      app = getAppFn(deps as unknown as FirebaseAppDependencies, overrideConfig);
    }
  } catch {
    appLookupThrew = true;
  }
  if (appLookupThrew || !app) {
    throw new FirebaseInitializationError("Firebase app lookup failed");
  }

  validateAppMatchesConfig(app, config.firebase, "auth");

  let auth: unknown = null;
  let authThrew = false;
  try {
    if (getAuthFn) {
      auth = getAuthFn(app as FirebaseApp);
    }
  } catch {
    authThrew = true;
  }

  if (authThrew || !auth || typeof auth !== "object") {
    throw new FirebaseInitializationError("Firebase auth initialization failed");
  }

  if (!deps && !overrideConfig) {
    authInstance = auth as Auth;
  }
  return auth as Auth;
}

export function getGoogleAuthProvider(): GoogleAuthProvider {
  if (!googleProviderInstance) {
    googleProviderInstance = new GoogleAuthProvider();
    googleProviderInstance.setCustomParameters({
      prompt: "select_account",
    });
  }
  return googleProviderInstance;
}
