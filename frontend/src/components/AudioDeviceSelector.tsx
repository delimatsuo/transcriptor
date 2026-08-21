"use client";

import React, { useState } from "react";
import type { AudioInputDevice, PermissionState } from "@/hooks/useBrowserAudioCapture";

interface Props {
  devices: AudioInputDevice[];
  selectedDeviceId: string;
  onSelectDevice: (deviceId: string) => void;
  audioLevel?: number; // 0.0 to 1.0
  isStreaming?: boolean;
  permissionState: PermissionState;
  onRequestPermission: () => Promise<boolean>;
  compact?: boolean;
}

export default function AudioDeviceSelector({
  devices,
  selectedDeviceId,
  onSelectDevice,
  audioLevel = 0,
  isStreaming = false,
  permissionState,
  onRequestPermission,
  compact = false,
}: Props) {
  const [requesting, setRequesting] = useState(false);

  const handleRequest = async () => {
    setRequesting(true);
    try {
      await onRequestPermission();
    } finally {
      setRequesting(false);
    }
  };

  // Compact layout (used in header during active interview)
  if (compact) {
    return (
      <div style={compactContainerStyle}>
        <span style={iconStyle} title="Microfone ativo">
          🎙️
        </span>
        <select
          value={selectedDeviceId}
          onChange={(e) => onSelectDevice(e.target.value)}
          style={compactSelectStyle}
          aria-label="Selecionar microfone"
        >
          {devices.map((device) => (
            <option key={device.deviceId} value={device.deviceId}>
              {device.label || `Microfone (${device.deviceId.slice(0, 8)})`}
            </option>
          ))}
        </select>
        {isStreaming && (
          <div style={compactMeterContainerStyle} title="Nível de captação">
            <div
              style={{
                ...compactMeterFillStyle,
                width: `${Math.round(audioLevel * 100)}%`,
              }}
            />
          </div>
        )}
      </div>
    );
  }

  // Full layout (used in pre-interview setup card)
  return (
    <div style={cardContainerStyle}>
      <div style={headerRowStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 18 }}>🎙️</span>
          <div>
            <div style={{ fontWeight: 600, fontSize: 13, color: "#1d1d1f" }}>
              Microfone de Entrada
            </div>
            <div style={{ fontSize: 11, color: "#86868b" }}>
              Selecione o microfone usado para a entrevista
            </div>
          </div>
        </div>

        {permissionState !== "granted" && (
          <button
            type="button"
            onClick={handleRequest}
            disabled={requesting}
            style={permissionButtonStyle}
          >
            {requesting ? "Verificando…" : "Autorizar Microfone"}
          </button>
        )}
      </div>

      <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 12 }}>
        <select
          value={selectedDeviceId}
          onChange={(e) => onSelectDevice(e.target.value)}
          style={selectStyle}
          aria-label="Selecionar microfone"
        >
          {devices.length === 0 ? (
            <option value="">Nenhum microfone detectado</option>
          ) : (
            devices.map((device) => (
              <option key={device.deviceId} value={device.deviceId}>
                {device.label || `Microfone (${device.deviceId.slice(0, 8)})`}
              </option>
            ))
          )}
        </select>
      </div>

      {/* Live VU Audio Level Meter */}
      <div style={meterWrapperStyle}>
        <div style={{ fontSize: 11, color: "#515154", minWidth: 70 }}>
          Teste de voz:
        </div>
        <div style={meterTrackStyle}>
          <div
            style={{
              ...meterFillStyle,
              width: `${Math.max(4, Math.round(audioLevel * 100))}%`,
              background:
                audioLevel > 0.75
                  ? "#ff9500"
                  : audioLevel > 0.05
                    ? "#34c759"
                    : "#d2d2d7",
            }}
          />
        </div>
        <span style={{ fontSize: 10, color: "#86868b", minWidth: 35 }}>
          {audioLevel > 0.05 ? "OK" : "Silêncio"}
        </span>
      </div>
    </div>
  );
}

const cardContainerStyle: React.CSSProperties = {
  background: "#f5f5f7",
  border: "1px solid #e5e5ea",
  borderRadius: 12,
  padding: "12px 16px",
  marginBottom: 16,
};

const headerRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
};

const selectStyle: React.CSSProperties = {
  flex: 1,
  padding: "8px 12px",
  fontSize: 13,
  borderRadius: 8,
  border: "1px solid #d2d2d7",
  background: "white",
  color: "#1d1d1f",
  outline: "none",
  cursor: "pointer",
};

const permissionButtonStyle: React.CSSProperties = {
  padding: "6px 12px",
  fontSize: 11,
  fontWeight: 600,
  borderRadius: 100,
  border: "none",
  background: "#0071e3",
  color: "white",
  cursor: "pointer",
};

const meterWrapperStyle: React.CSSProperties = {
  marginTop: 10,
  display: "flex",
  alignItems: "center",
  gap: 10,
};

const meterTrackStyle: React.CSSProperties = {
  flex: 1,
  height: 8,
  background: "#e5e5ea",
  borderRadius: 4,
  overflow: "hidden",
};

const meterFillStyle: React.CSSProperties = {
  height: "100%",
  transition: "width 80ms ease, background 150ms ease",
  borderRadius: 4,
};

const compactContainerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  background: "#f5f5f7",
  padding: "4px 8px",
  borderRadius: 8,
  border: "1px solid #e5e5ea",
};

const iconStyle: React.CSSProperties = {
  fontSize: 14,
};

const compactSelectStyle: React.CSSProperties = {
  fontSize: 12,
  background: "transparent",
  border: "none",
  color: "#1d1d1f",
  outline: "none",
  maxWidth: 160,
  cursor: "pointer",
};

const compactMeterContainerStyle: React.CSSProperties = {
  width: 32,
  height: 6,
  background: "#e5e5ea",
  borderRadius: 3,
  overflow: "hidden",
};

const compactMeterFillStyle: React.CSSProperties = {
  height: "100%",
  background: "#34c759",
  transition: "width 80ms ease",
  borderRadius: 3,
};
