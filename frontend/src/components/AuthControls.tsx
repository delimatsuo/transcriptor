"use client";

import type { AuthStatus, AuthUserInfo } from "@/lib/auth";

interface Props {
  status: AuthStatus;
  user: AuthUserInfo | null;
  error: string | null;
  busy?: boolean;
  onSignIn: () => void;
  onSignOut: () => void;
  onUseAnotherAccount: () => void;
  onRetry: () => void;
  disabled?: boolean;
}

export default function AuthControls({
  status,
  user,
  error,
  busy = false,
  onSignIn,
  onSignOut,
  onUseAnotherAccount,
  onRetry,
  disabled = false,
}: Props) {
  if (status === "initializing" || status === "checking_access") {
    return (
      <span role="status" aria-live="polite" aria-busy="true" style={{ fontSize: 12, color: "#86868b" }}>
        Verificando acesso…
      </span>
    );
  }

  if (status === "opening_popup") {
    return (
      <span role="status" aria-live="polite" aria-busy="true" style={{ fontSize: 12, color: "#86868b" }}>
        Conectando…
      </span>
    );
  }

  if (busy && status === "signed_out") {
    return (
      <span role="status" aria-live="polite" aria-busy="true" style={{ fontSize: 12, color: "#86868b" }}>
        Desconectando…
      </span>
    );
  }

  if (status === "denied_account") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span role="alert" style={{ color: "#ff3b30", fontSize: 12 }}>
          {error || "Esta conta Google não está autorizada para o T.A.R.S. Use uma conta Ella autorizada."}
        </span>
        <button
          type="button"
          onClick={onUseAnotherAccount}
          disabled={disabled || busy}
          style={buttonStyle}
        >
          Usar outra conta
        </button>
      </div>
    );
  }

  if (status === "retryable_error") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span role="alert" style={{ color: "#ff3b30", fontSize: 12 }}>
          {error || "Não foi possível validar o acesso com o backend. Verifique a conexão e tente novamente."}
        </span>
        <button
          type="button"
          onClick={onRetry}
          disabled={disabled || busy}
          style={buttonStyle}
        >
          Tentar novamente
        </button>
      </div>
    );
  }

  if (status === "config_error") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span role="alert" style={{ color: "#ff3b30", fontSize: 12 }}>
          {error || "Configuração de autenticação inválida ou ausente."}
        </span>
      </div>
    );
  }

  if (status === "sign_out_error") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span role="alert" style={{ color: "#ff3b30", fontSize: 12 }}>
          {error || "Falha ao encerrar a sessão."}
        </span>
        <button
          type="button"
          onClick={onSignOut}
          disabled={disabled || busy}
          style={buttonStyle}
        >
          Tentar novamente
        </button>
      </div>
    );
  }

  if (!user || status === "signed_out") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {error && <span role="alert" style={{ color: "#ff3b30", fontSize: 12 }}>{error}</span>}
        <button
          type="button"
          onClick={onSignIn}
          disabled={disabled || busy}
          style={buttonStyle}
        >
          Entrar com Google
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span
        aria-label={`Conta autenticada: ${user.email}`}
        style={{ fontSize: 12, color: "#515154" }}
      >
        {user.displayName || user.email}
      </span>
      <button
        type="button"
        onClick={onSignOut}
        disabled={disabled || busy}
        style={buttonStyle}
      >
        Sair
      </button>
    </div>
  );
}

const buttonStyle = {
  padding: "7px 13px",
  border: "1px solid #d2d2d7",
  borderRadius: 100,
  background: "white",
  color: "#1d1d1f",
  fontSize: 12,
  cursor: "pointer",
};
