"use client";

import { useEffect, useState } from "react";
import {
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithPopup,
  signOut as firebaseSignOut,
  type User,
} from "firebase/auth";
import { auth, firebaseConfigured } from "@/lib/firebase";

const bypassEnabled =
  process.env.NEXT_PUBLIC_AUTH_BYPASS === "1" && process.env.NODE_ENV !== "production";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type AuthStatus = "initializing" | "signed_out" | "signed_in" | "error";
export const authBypassEnabled = bypassEnabled;
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
  if (bypassEnabled) return null;
  if (!auth?.currentUser) return null;
  return auth.currentUser.getIdToken(forceRefresh);
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const token = await getIdToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response = await fetch(input, { ...init, headers });
  if (response.status === 401 && token && !bypassEnabled) {
    const refreshed = await getIdToken(true);
    if (refreshed) {
      headers.set("Authorization", `Bearer ${refreshed}`);
      response = await fetch(input, { ...init, headers });
    }
  }
  return response;
}

export function useAuth() {
  const [status, setStatus] = useState<AuthStatus>("initializing");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (bypassEnabled) {
      setUser(syntheticAuthUser());
      setStatus("signed_in");
      return;
    }
    if (!firebaseConfigured || !auth) {
      setStatus("signed_out");
      return;
    }
    return onAuthStateChanged(auth, (next) => {
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
      void (async () => {
        try {
          const token = await next.getIdToken();
          const response = await fetch(`${API_BASE_URL}/api/me`, {
            headers: { Authorization: `Bearer ${token}` },
          });
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
          setUser(null);
          setStatus("error");
          setError("Não foi possível validar o acesso com o backend. Verifique a conexão e tente novamente.");
        }
      })();
    });
  }, []);

  const signIn = async () => {
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
    if (bypassEnabled || !auth) return;
    // Clear the local principal before the provider round-trip so the prior
    // account's interview state cannot remain visible during sign-out.
    setUser(null);
    setStatus("signed_out");
    await firebaseSignOut(auth);
  };

  return { status, user, error, signIn, signOut };
}
