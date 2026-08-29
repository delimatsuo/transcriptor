"use client";

import { useEffect, useRef, useState } from "react";

type LoginPhase = "checking" | "ready" | "signing_in" | "complete" | "error";

const genericError =
  "Não foi possível iniciar o login seguro. Verifique o link de acesso e tente novamente.";

export default function IapLoginPage() {
  const [phase, setPhase] = useState<LoginPhase>("checking");
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<import("@/lib/iapLogin").GoogleOnlyAuthenticationController | null>(null);

  useEffect(() => {
    let disposed = false;

    if (typeof window === "undefined") return;

    void (async () => {
      try {
        // Keep all Firebase/gcip-iap imports out of the server render and load
        // them only after a real browser request is available to validate.
        const {
          createGoogleOnlyAuthenticationHandler,
          parseIapLoginRequest,
          readFirebaseWebConfig,
          safeErrorMessage,
        } = await import("@/lib/iapLogin");
        if (disposed) return;
        const request = parseIapLoginRequest(window.location.href);
        const config = readFirebaseWebConfig();
        if (request === null || config === null || request.apiKey !== config.apiKey) {
          throw new Error(genericError);
        }
        if (disposed) return;

        const controller = createGoogleOnlyAuthenticationHandler(config, {
          onSignInReady: () => {
            if (!disposed) setPhase("ready");
          },
          onProgressChange: (visible) => {
            if (!disposed && visible) setPhase("checking");
          },
          onCompleteSignOut: () => {
            if (!disposed) setPhase("complete");
          },
          onError: (handlerError) => {
            if (!disposed) {
              setError(safeErrorMessage(handlerError));
              setPhase("error");
            }
          },
        });
        if (disposed) {
          controller.dispose();
          return;
        }
        controllerRef.current = controller;

        const module = await import("../../../vendor/gcip-iap/2.0.1/index.mjs");
        if (disposed) {
          controller.dispose();
          if (controllerRef.current === controller) controllerRef.current = null;
          return;
        }
        if (typeof module.Authentication !== "function") throw new Error(genericError);
        if (disposed) {
          controller.dispose();
          if (controllerRef.current === controller) controllerRef.current = null;
          return;
        }
        const authentication = new module.Authentication(controller.handler);
        if (disposed) {
          controller.dispose();
          if (controllerRef.current === controller) controllerRef.current = null;
          return;
        }
        await authentication.start();
        if (disposed) {
          controller.dispose();
          if (controllerRef.current === controller) controllerRef.current = null;
          return;
        }
      } catch (caught) {
        if (!disposed) {
          setError(caught instanceof Error && caught.message !== genericError ? safeMessage(caught) : genericError);
          setPhase("error");
        }
      }
    })();

    return () => {
      disposed = true;
      controllerRef.current?.dispose();
      controllerRef.current = null;
    };
  }, []);

  const requestSignIn = () => {
    setPhase("signing_in");
    if (!controllerRef.current?.requestSignIn()) {
      setError(genericError);
      setPhase("error");
    }
  };

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 24,
        background: "#f5f5f7",
        color: "#1d1d1f",
      }}
    >
      <section
        aria-labelledby="iap-login-title"
        style={{
          width: "min(100%, 420px)",
          padding: "38px 34px",
          borderRadius: 24,
          background: "#fff",
          boxShadow: "0 18px 60px rgba(0, 0, 0, 0.10)",
          textAlign: "center",
        }}
      >
        <div aria-hidden="true" style={{ fontSize: 14, letterSpacing: 2, fontWeight: 700, color: "#6e6e73" }}>
          T.A.R.S.
        </div>
        <h1 id="iap-login-title" style={{ margin: "18px 0 10px", fontSize: 28, letterSpacing: -0.6 }}>
          Acesso seguro
        </h1>
        <p style={{ margin: "0 auto 26px", color: "#6e6e73", lineHeight: 1.5 }}>
          Use sua conta Google autorizada para continuar.
        </p>

        {phase === "checking" && (
          <p role="status" aria-live="polite">Preparando o login…</p>
        )}
        {phase === "complete" && (
          <p role="status" aria-live="polite">Sessão encerrada.</p>
        )}
        {phase === "error" && (
          <p role="alert" style={{ color: "#b42318", lineHeight: 1.5 }}>{error ?? genericError}</p>
        )}

        {(phase === "ready" || phase === "signing_in") && (
          <button
            type="button"
            onClick={requestSignIn}
            disabled={phase === "signing_in"}
            style={{
              width: "100%",
              padding: "13px 18px",
              border: 0,
              borderRadius: 999,
              background: "#1d1d1f",
              color: "white",
              fontSize: 15,
              fontWeight: 600,
              cursor: phase === "signing_in" ? "wait" : "pointer",
              opacity: phase === "signing_in" ? 0.7 : 1,
            }}
          >
            {phase === "signing_in" ? "Abrindo o Google…" : "Continuar com Google"}
          </button>
        )}
      </section>
    </main>
  );
}

function safeMessage(error: Error): string {
  void error;
  return genericError;
}
