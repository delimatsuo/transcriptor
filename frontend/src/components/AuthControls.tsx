"use client";

import type { AuthStatus, AuthUser } from "@/lib/auth";

interface Props {
  status: AuthStatus;
  user: AuthUser | null;
  error: string | null;
  onSignIn: () => void;
  onSignOut: () => void;
  disabled?: boolean;
}

export default function AuthControls({
  status,
  user,
  error,
  onSignIn,
  onSignOut,
  disabled = false,
}: Props) {
  if (status === "initializing") {
    return <span role="status" aria-live="polite">Verificando acesso…</span>;
  }
  if (!user) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {error && <span role="alert" style={{ color: "#ff3b30", fontSize: 12 }}>{error}</span>}
        <button type="button" onClick={onSignIn} style={buttonStyle}>
          Entrar com Google
        </button>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span aria-label={`Conta autenticada: ${user.email}`} style={{ fontSize: 12, color: "#515154" }}>
        {user.displayName}
      </span>
      <button type="button" onClick={onSignOut} disabled={disabled} style={buttonStyle}>
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
