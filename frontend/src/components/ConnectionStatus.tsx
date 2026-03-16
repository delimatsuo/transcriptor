"use client";

import type { ConnectionHealth } from "@/types/ws";

const STATUS_CONFIG: Record<
  ConnectionHealth,
  { color: string; bg: string; label: string }
> = {
  healthy: { color: "#22c55e", bg: "#f0fdf4", label: "Connected" },
  degraded: { color: "#eab308", bg: "#fefce8", label: "Degraded" },
  disconnected: { color: "#ef4444", bg: "#fef2f2", label: "Disconnected" },
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
        borderRadius: 9999,
        backgroundColor: config.bg,
        fontSize: 13,
        fontWeight: 500,
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
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
