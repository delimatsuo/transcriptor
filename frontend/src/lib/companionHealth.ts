import type { SourceHealthState } from "@/types/ws";

export interface FormattedSourceHealth {
  label: string;
  badgeBg: string;
  badgeColor: string;
  icon: string;
}

export function formatSourceHealth(
  source: "mic" | "system",
  health: SourceHealthState,
): FormattedSourceHealth {
  const prefix = source === "mic" ? "Microfone" : "Áudio do Sistema";

  switch (health) {
    case "healthy":
      return {
        label: `${prefix}: Ativo`,
        badgeBg: "rgba(52, 199, 89, 0.12)",
        badgeColor: "#248a3d",
        icon: "●",
      };
    case "reconnecting":
      return {
        label: `${prefix}: Reconectando…`,
        badgeBg: "rgba(255, 149, 0, 0.12)",
        badgeColor: "#c97000",
        icon: "↻",
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
