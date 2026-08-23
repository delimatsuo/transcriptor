"use client";

import { buildJoinLink } from "@/lib/joinLink";

interface Props {
  sessionId: string;
  streamKey?: string;
}

export default function CompanionCommand({ sessionId, streamKey }: Props) {
  if (!streamKey) {
    return null;
  }

  const gatewayBase =
    process.env.NEXT_PUBLIC_WS_STREAM_URL ||
    "ws://127.0.0.1:8000/api/stream/native";
  const joinHref = buildJoinLink(sessionId, streamKey, gatewayBase);

  return (
    <div
      role="group"
      aria-label="Comando de inicialização do companion"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "8px 12px",
        borderRadius: 8,
        backgroundColor: "rgba(0, 122, 255, 0.06)",
        border: "1px solid rgba(0, 122, 255, 0.15)",
      }}
    >
      <span style={{ fontSize: 11, fontWeight: 600, color: "#515154" }}>
        Canal do Candidato:
      </span>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <a
          href={joinHref}
          title="Abre o app TarsCompanion e inicia a captura"
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "4px 12px",
            borderRadius: 6,
            backgroundColor: "#0a84ff",
            color: "white",
            fontSize: 12,
            fontWeight: 600,
            textDecoration: "none",
            cursor: "pointer",
            transition: "all 0.2s ease",
          }}
        >
          Conectar companion
        </a>
      </div>
      <span style={{ fontSize: 11, color: "#515154" }}>
        Não tem o app? Veja o guia de onboarding.
      </span>
    </div>
  );
}
