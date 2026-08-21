"use client";

import { useCallback, useState } from "react";

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

  return (
    <div
      role="group"
      aria-label="Comando de inicialização do companion"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "6px 10px",
        borderRadius: 8,
        backgroundColor: "rgba(0, 122, 255, 0.06)",
        border: "1px solid rgba(0, 122, 255, 0.15)",
      }}
    >
      <span style={{ fontSize: 11, fontWeight: 600, color: "#515154" }}>
        Canal do Candidato — execute o companion:
      </span>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
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
    </div>
  );
}
