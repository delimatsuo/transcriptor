"use client";

import { formatSourceHealth } from "@/lib/companionHealth";
import type { PhysicalCaptureState, SourceHealthState } from "@/types/ws";

interface Props {
  captureState?: PhysicalCaptureState;
  micHealth?: SourceHealthState;
  systemAudioHealth?: SourceHealthState;
  message?: string | null;
}

export default function CaptureSourceStatus({
  captureState = "active",
  micHealth = "healthy",
  systemAudioHealth = "healthy",
  message,
}: Props) {
  const mic = formatSourceHealth("mic", micHealth);
  const sys = formatSourceHealth("system", systemAudioHealth);

  return (
    <div
      role="group"
      aria-label="Status das fontes de áudio do Companion"
      style={{
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: 6,
      }}
    >
      <div
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

      {message && (
        <div
          role="status"
          aria-live="polite"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "3px 8px",
            borderRadius: 6,
            backgroundColor: "rgba(255, 149, 0, 0.12)",
            color: "#c97000",
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          <span aria-hidden="true">⚠</span>
          <span>{message}</span>
        </div>
      )}
    </div>
  );
}
