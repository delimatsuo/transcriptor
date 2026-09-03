import type { Auth, GoogleAuthProvider, User } from "firebase/auth";
import { executeAdmissionRequest, type AuthenticatedPrincipal } from "./authAdmission";
import {
  getFirebaseAuth,
  getGoogleAuthProvider,
  isFirebaseConfigured,
} from "./firebase";
import {
  apiUrl,
  publicRuntimeConfigResult,
  type FirebasePublicConfig,
  type PublicRuntimeConfig,
  type Result,
} from "./runtimeConfig";

export type AuthStatus =
  | "initializing"
  | "signed_out"
  | "opening_popup"
  | "checking_access"
  | "signed_in"
  | "denied_account"
  | "retryable_error"
  | "config_error"
  | "sign_out_error";

export interface AuthUserInfo {
  uid: string;
  email: string;
  displayName: string;
  org_id?: string;
  native?: unknown;
}

export interface AuthState {
  status: AuthStatus;
  user: AuthUserInfo | null;
  error: string | null;
  busy: boolean;
}

export function syntheticAuthUser(): AuthUserInfo {
  return {
    uid: "local-recruiter-dev",
    email: "recruiter-pilot@example.com",
    displayName: "Recruiter Local",
    org_id: "ella-internal",
    native: null,
  };
}

export function toAuthUser(user: { uid: string; email?: string | null; displayName?: string | null }, org_id = "ella-internal"): AuthUserInfo {
  return {
    uid: user.uid,
    email: user.email ?? "",
    displayName: user.displayName ?? user.email ?? "Conta Google",
    org_id,
    native: user,
  };
}

export interface AuthControllerDependencies {
  getRuntimeConfig?: () => Result<PublicRuntimeConfig, string>;
  getAuth?: () => Auth | null;
  subscribeAuthState?: (
    auth: Auth,
    next: (user: User | null) => void,
    error?: (err: Error) => void
  ) => () => void;
  signInWithPopup?: (auth: Auth, provider: GoogleAuthProvider) => Promise<unknown>;
  signOutProvider?: (auth: Auth) => Promise<void>;
  getGoogleProvider?: () => GoogleAuthProvider;
  executeAdmission?: typeof executeAdmissionRequest;
  fetch?: typeof fetch;
  apiUrl?: (path: string) => string;
  syntheticUser?: () => AuthUserInfo;
}

export interface AuthController {
  getState(): AuthState;
  subscribe(listener: (state: AuthState) => void): () => void;
  start(): void;
  signIn(): Promise<void>;
  signOut(): Promise<void>;
  useAnotherAccount(): Promise<void>;
  retry(): Promise<void>;
  dispose(): void;
}

export function createAuthController(deps: AuthControllerDependencies = {}): AuthController {
  const getRuntimeConfigFn = deps.getRuntimeConfig ?? (() => publicRuntimeConfigResult);
  const getAuthFn = deps.getAuth ?? getFirebaseAuth;
  const executeAdmissionFn = deps.executeAdmission ?? executeAdmissionRequest;
  const apiUrlFn =
    deps.apiUrl ??
    ((path: string) => {
      const res = getRuntimeConfigFn();
      return apiUrl(path, res.ok ? res.value : undefined);
    });
  const syntheticUserFn = deps.syntheticUser ?? syntheticAuthUser;
  const getGoogleProviderFn = deps.getGoogleProvider ?? getGoogleAuthProvider;

  function safeResolveRuntimeConfig(): Result<PublicRuntimeConfig, string> {
    try {
      const res = getRuntimeConfigFn();
      if (!res || typeof res !== "object" || typeof (res as { ok?: unknown }).ok !== "boolean") {
        return { ok: false, error: "Invalid runtime configuration result" };
      }
      if (!res.ok) {
        return { ok: false, error: typeof res.error === "string" ? res.error : "Runtime configuration resolution failed" };
      }
      const val = res.value;
      if (!val || typeof val !== "object" || Array.isArray(val)) {
        return { ok: false, error: "Invalid runtime configuration value" };
      }
      const valProto = Object.getPrototypeOf(val);
      if (valProto !== Object.prototype && valProto !== null) {
        return { ok: false, error: "Runtime configuration value must be a plain object" };
      }

      const rawBypass = val.authBypassEnabled;
      if (typeof rawBypass !== "boolean") {
        return { ok: false, error: "authBypassEnabled must be an exact boolean primitive" };
      }

      const rawApiUrl = val.apiUrl;
      if (typeof rawApiUrl !== "string") {
        return { ok: false, error: "apiUrl must be an exact string" };
      }

      const rawWsUrl = val.wsUrl;
      if (typeof rawWsUrl !== "string") {
        return { ok: false, error: "wsUrl must be an exact string" };
      }

      const rawWsStreamUrl = val.wsStreamUrl;
      if (typeof rawWsStreamUrl !== "string") {
        return { ok: false, error: "wsStreamUrl must be an exact string" };
      }

      let copiedFirebase: FirebasePublicConfig | undefined = undefined;
      const rawFirebase = val.firebase;
      if (rawFirebase !== undefined) {
        if (!rawFirebase || typeof rawFirebase !== "object" || Array.isArray(rawFirebase)) {
          return { ok: false, error: "firebase config must be a plain object" };
        }
        const fbProto = Object.getPrototypeOf(rawFirebase);
        if (fbProto !== Object.prototype && fbProto !== null) {
          return { ok: false, error: "firebase config must be a plain object" };
        }
        const apiKey = rawFirebase.apiKey;
        const authDomain = rawFirebase.authDomain;
        const projectId = rawFirebase.projectId;
        const storageBucket = rawFirebase.storageBucket;
        const messagingSenderId = rawFirebase.messagingSenderId;
        const appId = rawFirebase.appId;

        if (
          typeof apiKey !== "string" ||
          typeof authDomain !== "string" ||
          typeof projectId !== "string" ||
          typeof storageBucket !== "string" ||
          typeof messagingSenderId !== "string" ||
          typeof appId !== "string"
        ) {
          return { ok: false, error: "firebase config fields must be exact strings" };
        }

        copiedFirebase = Object.freeze({
          apiKey,
          authDomain,
          projectId,
          storageBucket,
          messagingSenderId,
          appId,
        });
      }

      const copied: PublicRuntimeConfig = Object.freeze({
        authBypassEnabled: rawBypass,
        apiUrl: rawApiUrl,
        wsUrl: rawWsUrl,
        wsStreamUrl: rawWsStreamUrl,
        firebase: copiedFirebase,
      });

      return { ok: true, value: copied };
    } catch {
      return { ok: false, error: "Runtime configuration resolution threw" };
    }
  }

  function computeInitialState(): AuthState {
    const cfg = safeResolveRuntimeConfig();
    if (!cfg.ok) {
      return {
        status: "config_error",
        user: null,
        error: "Configuração de autenticação inválida ou ausente.",
        busy: false,
      };
    }
    if (cfg.value.authBypassEnabled) {
      let synUser: AuthUserInfo;
      try {
        synUser = syntheticUserFn();
      } catch {
        return {
          status: "config_error",
          user: null,
          error: "Configuração de autenticação inválida ou ausente.",
          busy: false,
        };
      }
      return {
        status: "signed_in",
        user: synUser,
        error: null,
        busy: false,
      };
    }
    return {
      status: "initializing",
      user: null,
      error: null,
      busy: false,
    };
  }

  let state: AuthState = computeInitialState();

  const listeners = new Set<(state: AuthState) => void>();
  let currentGeneration = 0;
  let currentOperationToken = 0;
  let currentAbortController: AbortController | null = null;
  let currentPrincipal: User | null = null;
  let unsubscribeAuth: (() => void) | null = null;
  let isDisposed = false;
  type OperationPhase =
    | "idle"
    | "signing_in"
    | "signing_out"
    | "switching_signout"
    | "switching_chooser"
    | "retrying";
  let currentOperationPhase: OperationPhase = "idle";

  const retiredPrincipalObjects = new WeakSet<object>();

  function retirePrincipal(u: User | AuthUserInfo | null | undefined) {
    if (!u) return;
    if (typeof u === "object" && u !== null) {
      try {
        retiredPrincipalObjects.add(u as object);
      } catch {}
    }
  }

  function isPrincipalRetired(u: User | null | undefined): boolean {
    if (!u) return false;
    if (typeof u === "object" && u !== null) {
      try {
        return retiredPrincipalObjects.has(u as object);
      } catch {
        return false;
      }
    }
    return false;
  }

  function isUserCancelError(err: unknown): boolean {
    if (!err) return false;
    try {
      if (typeof (err as { code?: string }).code === "string") {
        const code = (err as { code: string }).code;
        if (code.includes("popup-closed") || code.includes("cancelled") || code.includes("canceled")) {
          return true;
        }
      }
    } catch {}
    try {
      if (typeof (err as { message?: string }).message === "string") {
        const msg = (err as { message: string }).message;
        if (msg.includes("popup-closed") || msg.includes("cancelled") || msg.includes("canceled")) {
          return true;
        }
      }
    } catch {}
    return false;
  }

  function updateState(newState: AuthState) {
    state = newState;
    for (const listener of listeners) {
      try {
        listener(state);
      } catch {
        // listener error ignored
      }
    }
  }

  async function runAdmissionForPrincipal(user: User, generation: number): Promise<void> {
    if (isPrincipalRetired(user)) {
      return;
    }

    const controller = new AbortController();
    currentAbortController = controller;

    let targetUrl: string;
    try {
      targetUrl = apiUrlFn("/api/me");
    } catch {
      if (generation === currentGeneration && !isDisposed) {
        updateState({
          status: "config_error",
          user: null,
          error: "Configuração de autenticação inválida ou ausente.",
          busy: false,
        });
      }
      return;
    }

    let userUid = "";
    let userEmail = "";
    let userSnapshot: AuthUserInfo | null = null;
    try {
      const rawUid = user.uid;
      const rawEmail = user.email;
      const rawDisplayName = user.displayName;
      const rawPhotoURL = user.photoURL;
      if (
        typeof rawUid !== "string" ||
        rawUid.length < 1 ||
        rawUid.length > 128 ||
        rawUid !== rawUid.trim() ||
        /[\u0000-\u001F\u007F-\uFFFF\s]/.test(rawUid)
      ) {
        throw new Error("Invalid principal UID");
      }
      if (
        typeof rawEmail !== "string" ||
        rawEmail.length < 3 ||
        rawEmail.length > 254 ||
        rawEmail !== rawEmail.trim() ||
        /[\u0000-\u001F\u007F-\uFFFF\s]/.test(rawEmail)
      ) {
        throw new Error("Invalid principal email");
      }
      const emailParts = rawEmail.split("@");
      if (emailParts.length !== 2) {
        throw new Error("Invalid email format");
      }
      const [local, domain] = emailParts;
      const LOCAL_PART_ALLOWED = /^[a-zA-Z0-9!#$%&'+=?^_`{|}~.-]+$/;
      if (!local || local.length > 64 || local.startsWith(".") || local.endsWith(".") || local.includes("..") || !LOCAL_PART_ALLOWED.test(local)) {
        throw new Error("Invalid email local part");
      }
      if (!domain || domain.length > 253 || !domain.includes(".")) {
        throw new Error("Invalid email domain");
      }
      const domainParts = domain.split(".");
      if (domainParts.some((p) => !p || p.length > 63 || p.startsWith("-") || p.endsWith("-") || !/^[a-zA-Z0-9-]+$/.test(p))) {
        throw new Error("Invalid email domain label");
      }
      userUid = rawUid;
      userEmail = rawEmail;
      userSnapshot = Object.freeze({
        uid: rawUid,
        email: rawEmail,
        displayName: typeof rawDisplayName === "string" && rawDisplayName.trim() ? rawDisplayName : rawEmail,
        org_id: "",
        native: user,
      });
    } catch {
      if (generation === currentGeneration && !isDisposed) {
        updateState({
          status: "retryable_error",
          user: null,
          error: "Não foi possível validar o acesso com o backend. Verifique sua conexão e tente novamente.",
          busy: false,
        });
      }
      return;
    }

    try {
      const result = await executeAdmissionFn({
        apiUrl: targetUrl,
        tokenProvider: async () => {
          if (controller.signal.aborted || isDisposed || generation !== currentGeneration) {
            return null;
          }
          if (typeof user.getIdToken !== "function") {
            return null;
          }
          return await user.getIdToken();
        },
        expectedUid: userUid,
        expectedEmail: userEmail,
        externalSignal: controller.signal,
        fetchFn: (input: RequestInfo | URL, init?: RequestInit) => {
          if (controller.signal.aborted || isDisposed || generation !== currentGeneration) {
            throw new Error("Aborted");
          }
          return (deps.fetch ?? fetch)(input as any, init);
        },
      });

      if (isDisposed || generation !== currentGeneration || controller.signal.aborted) {
        return;
      }

      if (result.status === "admitted") {
        const admittedPrincipal = result.principal;
        const admittedSnapshot: AuthUserInfo = Object.freeze({
          uid: admittedPrincipal.uid,
          email: admittedPrincipal.email,
          displayName: userSnapshot?.displayName || admittedPrincipal.email,
          org_id: admittedPrincipal.org_id,
          native: user,
        });
        updateState({
          status: "signed_in",
          user: admittedSnapshot,
          error: null,
          busy: false,
        });
      } else if (result.status === "denied") {
        updateState({
          status: "denied_account",
          user: null,
          error: "Esta conta Google não está autorizada para o T.A.R.S. Use uma conta Ella autorizada.",
          busy: false,
        });
      } else if (result.status === "cancelled") {
        updateState({
          status: "signed_out",
          user: null,
          error: null,
          busy: false,
        });
      } else {
        updateState({
          status: "retryable_error",
          user: null,
          error: "Não foi possível validar o acesso com o backend. Verifique sua conexão e tente novamente.",
          busy: false,
        });
      }
    } catch {
      if (generation === currentGeneration && !isDisposed) {
        updateState({
          status: "retryable_error",
          user: null,
          error: "Não foi possível validar o acesso com o backend. Verifique sua conexão e tente novamente.",
          busy: false,
        });
      }
    }
  }

  function start(): void {
    if (isDisposed) return;
    const cfgRes = safeResolveRuntimeConfig();
    if (!cfgRes.ok) {
      updateState({
        status: "config_error",
        user: null,
        error: "Configuração de autenticação inválida ou ausente.",
        busy: false,
      });
      return;
    }

    const cfg = cfgRes.value;
    if (cfg.authBypassEnabled) {
      let synUser: AuthUserInfo;
      try {
        synUser = syntheticUserFn();
      } catch {
        updateState({
          status: "config_error",
          user: null,
          error: "Configuração de autenticação inválida ou ausente.",
          busy: false,
        });
        return;
      }
      updateState({
        status: "signed_in",
        user: synUser,
        error: null,
        busy: false,
      });
      return;
    }

    let auth: Auth | null = null;
    try {
      auth = getAuthFn();
    } catch {
      updateState({
        status: "config_error",
        user: null,
        error: "Configuração de autenticação inválida ou ausente.",
        busy: false,
      });
      return;
    }

    if (!auth) {
      updateState({
        status: "config_error",
        user: null,
        error: "Configuração de autenticação inválida ou ausente.",
        busy: false,
      });
      return;
    }

    if (deps.subscribeAuthState) {
      try {
        unsubscribeAuth = deps.subscribeAuthState(
          auth,
          (nextUser) => {
            if (isDisposed) return;

            if (currentOperationPhase === "signing_out" || currentOperationPhase === "switching_signout") {
              return;
            }

            if (!nextUser) {
              const prior = currentPrincipal;
              retirePrincipal(prior);
              retirePrincipal(state.user as any);
              currentAbortController?.abort();
              currentAbortController = null;
              currentPrincipal = null;
              currentOperationPhase = "idle";
              currentGeneration++;
              currentOperationToken++;
              updateState({
                status: "signed_out",
                user: null,
                error: null,
                busy: false,
              });
              return;
            }

            if (isPrincipalRetired(nextUser)) {
              return;
            }

            const prior = currentPrincipal;
            if (prior && prior !== nextUser) {
              retirePrincipal(prior);
            }
            currentAbortController?.abort();
            currentAbortController = null;
            currentPrincipal = nextUser;
            currentOperationPhase = "idle";
            const gen = ++currentGeneration;
            ++currentOperationToken;
            updateState({
              status: "checking_access",
              user: null,
              error: null,
              busy: true,
            });

            void runAdmissionForPrincipal(nextUser, gen);
          },
          () => {
            if (isDisposed) return;
            if (currentOperationPhase === "signing_out" || currentOperationPhase === "switching_signout") {
              return;
            }
            const prior = currentPrincipal;
            retirePrincipal(prior);
            retirePrincipal(state.user as any);
            currentGeneration++;
            currentOperationToken++;
            currentAbortController?.abort();
            currentAbortController = null;
            currentPrincipal = null;
            currentOperationPhase = "idle";
            updateState({
              status: "signed_out",
              user: null,
              error: "Falha na conexão de autenticação.",
              busy: false,
            });
          }
        );
      } catch {
        updateState({
          status: "config_error",
          user: null,
          error: "Configuração de autenticação inválida ou ausente.",
          busy: false,
        });
      }
    }
  }

  async function signIn(): Promise<void> {
    if (isDisposed || state.busy || currentOperationPhase !== "idle") return;
    const opToken = ++currentOperationToken;
    currentOperationPhase = "signing_in";

    const cfgRes = safeResolveRuntimeConfig();
    if (!cfgRes.ok) {
      updateState({
        status: "config_error",
        user: null,
        error: "Configuração de autenticação inválida ou ausente.",
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    if (cfgRes.value.authBypassEnabled) {
      let synUser: AuthUserInfo;
      try {
        synUser = syntheticUserFn();
      } catch {
        updateState({
          status: "config_error",
          user: null,
          error: "Configuração de autenticação inválida ou ausente.",
          busy: false,
        });
        if (currentOperationToken === opToken) currentOperationPhase = "idle";
        return;
      }
      updateState({
        status: "signed_in",
        user: synUser,
        error: null,
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    let auth: Auth | null = null;
    try {
      auth = getAuthFn();
    } catch {
      updateState({
        status: "config_error",
        user: null,
        error: "Configuração de autenticação inválida ou ausente.",
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    if (!auth) {
      updateState({
        status: "config_error",
        user: null,
        error: "Configuração de autenticação inválida ou ausente.",
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    updateState({
      ...state,
      status: "opening_popup",
      busy: true,
      error: null,
    });

    try {
      let provider: any;
      try {
        provider = getGoogleProviderFn();
      } catch {
        updateState({
          status: "signed_out",
          user: null,
          error: "Falha ao conectar com o Google. Tente novamente.",
          busy: false,
        });
        return;
      }
      if (deps.signInWithPopup) {
        await deps.signInWithPopup(auth, provider);
      }
    } catch (err: unknown) {
      if (!isDisposed && !currentPrincipal && opToken === currentOperationToken) {
        const isUserCancel = isUserCancelError(err);
        updateState({
          status: "signed_out",
          user: null,
          error: isUserCancel ? null : "Falha ao conectar com o Google. Tente novamente.",
          busy: false,
        });
      }
    } finally {
      if (currentOperationToken === opToken) {
        currentOperationPhase = "idle";
      }
    }
  }

  async function signOut(): Promise<void> {
    if (isDisposed || currentOperationPhase !== "idle") return;
    const opToken = ++currentOperationToken;
    currentOperationPhase = "signing_out";
    currentGeneration++;
    currentAbortController?.abort();
    currentAbortController = null;
    retirePrincipal(currentPrincipal);
    retirePrincipal(state.user as any);
    currentPrincipal = null;

    const cfgRes = safeResolveRuntimeConfig();
    if (!cfgRes.ok) {
      updateState({
        status: "config_error",
        user: null,
        error: "Configuração de autenticação inválida ou ausente.",
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    if (cfgRes.value.authBypassEnabled) {
      updateState({
        status: "signed_out",
        user: null,
        error: null,
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    updateState({
      status: "signed_out",
      user: null,
      error: null,
      busy: true,
    });

    let auth: Auth | null = null;
    try {
      auth = getAuthFn();
    } catch {
      updateState({
        status: "sign_out_error",
        user: null,
        error: "Erro ao desconectar da conta Google. Tente novamente.",
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    if (!auth) {
      updateState({
        status: "sign_out_error",
        user: null,
        error: "Erro ao desconectar da conta Google. Tente novamente.",
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    try {
      if (deps.signOutProvider) {
        await deps.signOutProvider(auth);
      }
      if (!isDisposed && opToken === currentOperationToken) {
        updateState({
          status: "signed_out",
          user: null,
          error: null,
          busy: false,
        });
      }
    } catch {
      if (!isDisposed && opToken === currentOperationToken) {
        updateState({
          status: "sign_out_error",
          user: null,
          error: "Erro ao desconectar da conta Google. Tente novamente.",
          busy: false,
        });
      }
    } finally {
      if (currentOperationToken === opToken) {
        currentOperationPhase = "idle";
      }
    }
  }

  async function useAnotherAccount(): Promise<void> {
    if (isDisposed || currentOperationPhase !== "idle") return;
    const opToken = ++currentOperationToken;
    currentOperationPhase = "switching_signout";
    const epoch = ++currentGeneration;
    currentAbortController?.abort();
    currentAbortController = null;
    retirePrincipal(currentPrincipal);
    retirePrincipal(state.user as any);
    currentPrincipal = null;

    const cfgRes = safeResolveRuntimeConfig();
    if (!cfgRes.ok) {
      updateState({
        status: "config_error",
        user: null,
        error: "Configuração de autenticação inválida ou ausente.",
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    if (cfgRes.value.authBypassEnabled) {
      let synUser: AuthUserInfo;
      try {
        synUser = syntheticUserFn();
      } catch {
        updateState({
          status: "config_error",
          user: null,
          error: "Configuração de autenticação inválida ou ausente.",
          busy: false,
        });
        if (currentOperationToken === opToken) currentOperationPhase = "idle";
        return;
      }
      updateState({
        status: "signed_in",
        user: synUser,
        error: null,
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    updateState({
      status: "opening_popup",
      user: null,
      error: null,
      busy: true,
    });

    let auth: Auth | null = null;
    try {
      auth = getAuthFn();
    } catch {
      updateState({
        status: "sign_out_error",
        user: null,
        error: "Erro ao desconectar da conta atual. Tente novamente.",
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    if (!auth) {
      updateState({
        status: "sign_out_error",
        user: null,
        error: "Erro ao desconectar da conta atual. Tente novamente.",
        busy: false,
      });
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    // Step 1: Sign out provider
    try {
      if (deps.signOutProvider) {
        await deps.signOutProvider(auth);
      }
    } catch {
      if (!isDisposed && epoch === currentGeneration && opToken === currentOperationToken) {
        updateState({
          status: "sign_out_error",
          user: null,
          error: "Erro ao desconectar da conta atual. Tente novamente.",
          busy: false,
        });
      }
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    if (isDisposed || epoch !== currentGeneration || opToken !== currentOperationToken) {
      if (currentOperationToken === opToken) currentOperationPhase = "idle";
      return;
    }

    // Step 2: Chooser subphase (only after sign-out succeeds)
    currentOperationPhase = "switching_chooser";

    try {
      let provider: any;
      try {
        provider = getGoogleProviderFn();
      } catch {
        updateState({
          status: "signed_out",
          user: null,
          error: "Falha ao conectar com o Google. Tente novamente.",
          busy: false,
        });
        return;
      }
      if (deps.signInWithPopup) {
        await deps.signInWithPopup(auth, provider);
      }
    } catch (err: unknown) {
      if (!isDisposed && epoch === currentGeneration && opToken === currentOperationToken && !currentPrincipal) {
        const isUserCancel = isUserCancelError(err);
        updateState({
          status: "signed_out",
          user: null,
          error: isUserCancel ? null : "Falha ao conectar com o Google. Tente novamente.",
          busy: false,
        });
      }
    } finally {
      if (currentOperationToken === opToken) {
        currentOperationPhase = "idle";
      }
    }
  }

  async function retry(): Promise<void> {
    if (isDisposed || !currentPrincipal || state.status !== "retryable_error" || currentOperationPhase !== "idle") return;

    let auth: Auth | null = null;
    try {
      auth = getAuthFn();
    } catch {
      return;
    }
    if (!auth) return;

    let currentUser: User | null = null;
    try {
      currentUser = auth.currentUser;
    } catch {
      return;
    }
    if (!currentUser || currentUser !== currentPrincipal || isPrincipalRetired(currentUser)) {
      return;
    }

    const opToken = ++currentOperationToken;
    const user = currentPrincipal;
    const gen = ++currentGeneration;
    currentOperationPhase = "retrying";
    currentAbortController?.abort();
    currentAbortController = null;
    updateState({
      status: "checking_access",
      user: null,
      error: null,
      busy: true,
    });
    try {
      await runAdmissionForPrincipal(user, gen);
    } finally {
      if (currentOperationToken === opToken) {
        currentOperationPhase = "idle";
      }
    }
  }

  function dispose() {
    isDisposed = true;
    currentOperationPhase = "idle";
    currentGeneration++;
    currentAbortController?.abort();
    currentAbortController = null;
    currentPrincipal = null;
    if (unsubscribeAuth) {
      try {
        unsubscribeAuth();
      } catch {}
      unsubscribeAuth = null;
    }
    listeners.clear();
  }

  return {
    getState: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    start,
    signIn,
    signOut,
    useAnotherAccount,
    retry,
    dispose,
  };
}

export function getInitialAuthState(): AuthState {
  if (!publicRuntimeConfigResult.ok) {
    return {
      status: "config_error",
      user: null,
      error: "Configuração de autenticação inválida ou ausente.",
      busy: false,
    };
  }
  if (publicRuntimeConfigResult.value.authBypassEnabled) {
    return {
      status: "signed_in",
      user: syntheticAuthUser(),
      error: null,
      busy: false,
    };
  }
  return {
    status: "initializing",
    user: null,
    error: null,
    busy: false,
  };
}

export interface AuthLifecycleAdapter {
  getInitialState(): AuthState;
  subscribe(listener: (state: AuthState) => void): () => void;
  start(): void;
  signIn(): Promise<void>;
  signOut(): Promise<void>;
  useAnotherAccount(): Promise<void>;
  retry(): Promise<void>;
  dispose(): void;
}

export function createAuthLifecycleAdapter(
  createController: () => AuthController = createProductionAuthController
): AuthLifecycleAdapter {
  const controller = createController();
  return {
    getInitialState: () => controller.getState(),
    subscribe: (listener) => controller.subscribe(listener),
    start: () => controller.start(),
    signIn: () => controller.signIn(),
    signOut: () => controller.signOut(),
    useAnotherAccount: () => controller.useAnotherAccount(),
    retry: () => controller.retry(),
    dispose: () => controller.dispose(),
  };
}

export function createProductionAuthController(): AuthController {
  return createAuthController({
    getRuntimeConfig: () => publicRuntimeConfigResult,
    getAuth: getFirebaseAuth,
    subscribeAuthState: (auth, next, error) => {
      const { onAuthStateChanged } = require("firebase/auth");
      return onAuthStateChanged(auth, next, error);
    },
    signInWithPopup: async (auth, provider) => {
      const { signInWithPopup } = require("firebase/auth");
      return await signInWithPopup(auth, provider);
    },
    signOutProvider: async (auth) => {
      const { signOut } = require("firebase/auth");
      await signOut(auth);
    },
    getGoogleProvider: getGoogleAuthProvider,
    executeAdmission: executeAdmissionRequest,
    apiUrl,
    syntheticUser: syntheticAuthUser,
  });
}
