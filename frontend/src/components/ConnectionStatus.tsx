"use client";

import type { ConnectionHealth } from "@/types/ws";

const STATUS_CONFIG: Record<
  ConnectionHealth,
  { color: string; bg: string; label: string }
> = {
  healthy: { color: "#34c759", bg: "rgba(52, 199, 89, 0.1)", label: "Conectado" },
  degraded: { color: "#ff9500", bg: "rgba(255, 149, 0, 0.1)", label: "Instável" },
  disconnected: { color: "#ff3b30", bg: "rgba(255, 59, 48, 0.1)", label: "Desconectado" },
};

interface Props {
  health: ConnectionHealth;
}

export default function ConnectionStatus({ health }: Props) {
  const config = STATUS_CONFIG[health];

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
        borderRadius: 100,
        backgroundColor: config.bg,
        fontSize: 12,
        fontWeight: 500,
        color: config.color,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          backgroundColor: config.color,
          display: "inline-block",
          animation: health === "healthy" ? undefined : "pulse 2s infinite",
        }}
      />
      {config.label}
    </div>
  );
}
