"use client";

import type { PhysicalCaptureState, SourceHealthState } from "@/types/ws";

interface Props {
  captureState?: PhysicalCaptureState;
  micHealth?: SourceHealthState;
  systemAudioHealth?: SourceHealthState;
}

function formatSourceHealth(source: "mic" | "system", health: SourceHealthState): {
  label: string;
  badgeBg: string;
  badgeColor: string;
  icon: string;
} {
  const prefix = source === "mic" ? "Microfone" : "Áudio do Sistema";

  switch (health) {
    case "healthy":
      return {
        label: `${prefix}: Ativo`,
        badgeBg: "rgba(52, 199, 89, 0.12)",
        badgeColor: "#248a3d",
        icon: "●",
      };
    case "permission_missing":
    case "permission_revoked":
      return {
        label: `${prefix}: Permissão necessária`,
        badgeBg: "rgba(255, 59, 48, 0.12)",
        badgeColor: "#d70015",
        icon: "✕",
      };
    case "device_unavailable":
      return {
        label: `${prefix}: Dispositivo desconectado`,
        badgeBg: "rgba(255, 149, 0, 0.12)",
        badgeColor: "#c97000",
        icon: "⚠",
      };
    case "overflow":
      return {
        label: `${prefix}: Buffer saturado`,
        badgeBg: "rgba(255, 149, 0, 0.12)",
        badgeColor: "#c97000",
        icon: "⚠",
      };
    case "failed":
      return {
        label: `${prefix}: Falha de captura`,
        badgeBg: "rgba(255, 59, 48, 0.12)",
        badgeColor: "#d70015",
        icon: "✕",
      };
    case "unknown":
    default:
      return {
        label: `${prefix}: Aguardando companion`,
        badgeBg: "rgba(142, 142, 147, 0.12)",
        badgeColor: "#636366",
        icon: "○",
      };
  }
}

export default function CaptureSourceStatus({
  captureState = "active",
  micHealth = "healthy",
  systemAudioHealth = "healthy",
}: Props) {
  const mic = formatSourceHealth("mic", micHealth);
  const sys = formatSourceHealth("system", systemAudioHealth);

  return (
    <div
      role="group"
      aria-label="Status das fontes de áudio do Companion"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        flexWrap: "wrap",
      }}
    >
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "3px 8px",
          borderRadius: 6,
          backgroundColor: mic.badgeBg,
          color: mic.badgeColor,
          fontSize: 11,
          fontWeight: 600,
        }}
        title="Canal do Entrevistador (AVAudioEngine)"
      >
        <span aria-hidden="true" style={{ fontSize: 10 }}>{mic.icon}</span>
        <span>{mic.label}</span>
      </div>

      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "3px 8px",
          borderRadius: 6,
          backgroundColor: sys.badgeBg,
          color: sys.badgeColor,
          fontSize: 11,
          fontWeight: 600,
        }}
        title="Canal do Candidato (ScreenCaptureKit)"
      >
        <span aria-hidden="true" style={{ fontSize: 10 }}>{sys.icon}</span>
        <span>{sys.label}</span>
      </div>
    </div>
  );
}
