"use client";

import { useEffect, useRef, useState } from "react";
import {
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithPopup,
  signOut as firebaseSignOut,
  type User,
} from "firebase/auth";
import { auth, firebaseConfigured } from "@/lib/firebase";
import { admissionIsCurrent } from "@/lib/authAdmission";
import { getRuntimeConfig } from "@/lib/runtimeConfig";
import {
  emitIapHttpTerminalIfNeeded,
  iapAdmissionAttemptIsCurrent,
} from "@/lib/iapLifecycle";
import {
  buildIapBootstrapUrl,
  emitIapTerminalAuthEvent,
  fetchIapAdmission,
  resetIapTerminalAuthEvent,
  runIapLogoutLifecycle,
} from "@/lib/iapSession";

export const runtimeConfig = getRuntimeConfig();
const bypassEnabled = runtimeConfig.authBypass && process.env.NODE_ENV !== "production";
const API_BASE_URL = runtimeConfig.apiOrigin;

export type AuthStatus =
  | "initializing"
  | "signed_out"
  | "signed_in"
  | "revoked"
  | "error";
export const authBypassEnabled = bypassEnabled;
export const authIapEnabled = runtimeConfig.iap;
export interface AuthUser {
  uid: string;
  email: string;
  displayName: string;
  native: User | null;
}

export function toAuthUser(user: User): AuthUser {
  return {
    uid: user.uid,
    email: user.email ?? "",
    displayName: user.displayName ?? user.email ?? "Conta Google",
    native: user,
  };
}

export function syntheticAuthUser(): AuthUser {
  return {
    uid: "playwright-recruiter-a",
    email: "recruiter-a@example.test",
    displayName: "Recruiter A (teste)",
    native: null,
  };
}

export async function getIdToken(forceRefresh = false): Promise<string | null> {
  if (bypassEnabled || runtimeConfig.iap) return null;
  if (!auth?.currentUser) return null;
  return auth.currentUser.getIdToken(forceRefresh);
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  let target = input;
  if (runtimeConfig.iap) {
    // Keep every application call on the direct approved API origin even
    // when a legacy component still supplies its development URL constant.
    const requested = new URL(
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url,
      API_BASE_URL,
    );
    target = `${API_BASE_URL}${requested.pathname}${requested.search}`;
  }
  const token = await getIdToken();
  const headers = new Headers(init.headers);
  if (runtimeConfig.iap) {
    // IAP's signed request assertion is the only authority.  Never send a
    // stale Firebase bearer alongside it or let callers override credentials.
    headers.delete("Authorization");
  } else if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  let response = await fetch(target, {
    ...init,
    headers,
    ...(runtimeConfig.iap ? { credentials: "include" as RequestCredentials } : {}),
  });
  // Every authenticated IAP boundary is terminal on auth denial, including
  // ordinary REST calls that are not ticket endpoints.
  emitIapHttpTerminalIfNeeded(
    response.status,
    runtimeConfig.iap,
    emitIapTerminalAuthEvent,
  );
  if (response.status === 401 && token && !bypassEnabled && !runtimeConfig.iap) {
    const refreshed = await getIdToken(true);
    if (refreshed) {
      headers.set("Authorization", `Bearer ${refreshed}`);
      response = await fetch(target, { ...init, headers });
    }
  }
  return response;
}

export function useAuth() {
  const [status, setStatus] = useState<AuthStatus>("initializing");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const admissionGenerationRef = useRef(0);
  const admissionAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (bypassEnabled) {
      setUser(syntheticAuthUser());
      setStatus("signed_in");
      return;
    }
    if (runtimeConfig.iap) {
      const generation = admissionGenerationRef.current + 1;
      admissionGenerationRef.current = generation;
      const controller = new AbortController();
      admissionAbortRef.current = controller;
      void (async () => {
        try {
          const admitted = await fetchIapAdmission(
            runtimeConfig,
            fetch,
            controller.signal,
          );
          if (
            !iapAdmissionAttemptIsCurrent(
              generation,
              admissionGenerationRef.current,
              controller.signal,
            )
          ) {
            return;
          }
          if (!admitted) {
            setUser(null);
            setStatus("signed_out");
            return;
          }
          setUser({
            uid: admitted.uid,
            email: admitted.email,
            displayName: admitted.email,
            native: null,
          });
          resetIapTerminalAuthEvent();
          setStatus("signed_in");
          setError(null);
        } catch {
          if (!iapAdmissionAttemptIsCurrent(
            generation,
            admissionGenerationRef.current,
            controller.signal,
          )) {
            return;
          }
          setUser(null);
          setStatus("error");
          setError("Não foi possível validar o acesso com o backend. Verifique a conexão e tente novamente.");
        }
      })();
      return () => {
        admissionGenerationRef.current += 1;
        controller.abort();
        admissionAbortRef.current = null;
      };
    }
    if (!firebaseConfigured || !auth) {
      setStatus("signed_out");
      return;
    }
    const firebaseAuth = auth;
    const unsubscribe = onAuthStateChanged(firebaseAuth, (next) => {
      const generation = admissionGenerationRef.current + 1;
      admissionGenerationRef.current = generation;
      admissionAbortRef.current?.abort();
      admissionAbortRef.current = null;
      if (!next) {
        setUser(null);
        setStatus("signed_out");
        setError(null);
        return;
      }
      setUser(null);
      setStatus("initializing");
      // Admission is a backend decision, not a client-side Firebase claim.
      // Keep the app/data tree hidden until the allowlist/org check passes.
      const controller = new AbortController();
      admissionAbortRef.current = controller;
      void (async () => {
        try {
          const token = await next.getIdToken();
          const response = await fetch(`${API_BASE_URL}/api/me`, {
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
          });
          if (
            !admissionIsCurrent(
              controller.signal,
              generation,
              admissionGenerationRef.current,
              firebaseAuth.currentUser?.uid,
              next.uid,
            )
          ) {
            return;
          }
          if (!response.ok) {
            setUser(null);
            setStatus("error");
            setError("Esta conta Google não está autorizada para o T.A.R.S. Use uma conta Ella autorizada.");
            return;
          }
          setUser(toAuthUser(next));
          setStatus("signed_in");
          setError(null);
        } catch {
          if (
            !admissionIsCurrent(
              controller.signal,
              generation,
              admissionGenerationRef.current,
              firebaseAuth.currentUser?.uid,
              next.uid,
            )
          ) {
            return;
          }
          setUser(null);
          setStatus("error");
          setError("Não foi possível validar o acesso com o backend. Verifique a conexão e tente novamente.");
        }
      })();
    });
    return () => {
      admissionGenerationRef.current += 1;
      admissionAbortRef.current?.abort();
      admissionAbortRef.current = null;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (!runtimeConfig.iap || typeof window === "undefined") return;
    const onTerminalAuth = () => {
      admissionGenerationRef.current += 1;
      admissionAbortRef.current?.abort();
      admissionAbortRef.current = null;
      setUser(null);
      setError(null);
      setStatus("revoked");
    };
    window.addEventListener("tars:iap-auth-terminal", onTerminalAuth);
    return () => {
      window.removeEventListener("tars:iap-auth-terminal", onTerminalAuth);
      admissionGenerationRef.current += 1;
      admissionAbortRef.current?.abort();
      admissionAbortRef.current = null;
    };
  }, []);

  const signIn = async () => {
    if (runtimeConfig.iap) {
      if (typeof window !== "undefined") {
        window.location.assign(buildIapBootstrapUrl(runtimeConfig));
      }
      return;
    }
    if (bypassEnabled) return;
    if (!auth) {
      setError("A configuração do Google ainda não está disponível nesta máquina.");
      return;
    }
    try {
      setError(null);
      await signInWithPopup(auth, new GoogleAuthProvider());
    } catch {
      setError("Não foi possível concluir o login Google. Tente novamente.");
      setStatus("error");
    }
  };

  const signOut = async () => {
    if (runtimeConfig.iap) {
      await runIapLogoutLifecycle(runtimeConfig, {
        cleanup: () => {
          admissionGenerationRef.current += 1;
          admissionAbortRef.current?.abort();
          admissionAbortRef.current = null;
          // This event is terminal and synchronous from the UI's perspective:
          // capture and socket hooks stop before the backend/provider round-trip.
          emitIapTerminalAuthEvent();
          setUser(null);
          setStatus("signed_out");
          setError(null);
        },
      });
      return;
    }
    if (bypassEnabled || !auth) return;
    // Clear the local principal before the provider round-trip so the prior
    // account's interview state cannot remain visible during sign-out.
    admissionGenerationRef.current += 1;
    admissionAbortRef.current?.abort();
    admissionAbortRef.current = null;
    setUser(null);
    setStatus("signed_out");
    await firebaseSignOut(auth);
  };

  return { status, user, error, signIn, signOut };
}
