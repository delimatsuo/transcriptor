"use client";

import { useCallback, useState } from "react";

import { buildJoinLink } from "@/lib/joinLink";

interface Props {
  sessionId: string;
  streamKey?: string;
}

export default function CompanionCommand({ sessionId, streamKey }: Props) {
  const [copied, setCopied] = useState(false);
  const command = `./tars-companion --session-id ${sessionId} --stream-key ${streamKey ?? ""} --sources system_audio`;

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API unavailable or permission denied; the command is
      // still visible in the block for manual copy.
    }
  }, [command]);

  if (!streamKey) {
    return null;
  }

  const joinHref = buildJoinLink(sessionId, streamKey);

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
      <details style={{ marginTop: 2 }}>
        <summary
          style={{
            fontSize: 11,
            color: "#515154",
            cursor: "pointer",
            userSelect: "none",
          }}
        >
          Método alternativo (terminal)
        </summary>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
            marginTop: 6,
          }}
        >
          <code
            style={{
              padding: "3px 8px",
              borderRadius: 6,
              backgroundColor: "rgba(142, 142, 147, 0.12)",
              color: "#1d1d1f",
              fontSize: 11,
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              wordBreak: "break-all",
            }}
          >
            {command}
          </code>
          <button
            type="button"
            onClick={() => void handleCopy()}
            aria-label="Copiar comando do companion"
            style={{
              padding: "3px 10px",
              borderRadius: 6,
              border: "1px solid #d2d2d7",
              backgroundColor: copied ? "#34c759" : "white",
              color: copied ? "white" : "#1d1d1f",
              fontSize: 11,
              fontWeight: 600,
              cursor: "pointer",
              transition: "all 0.2s ease",
              flexShrink: 0,
            }}
          >
            {copied ? "Copiado!" : "Copiar"}
          </button>
        </div>
      </details>
    </div>
  );
}
