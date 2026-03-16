"use client";

import { useState } from "react";
import type { SessionMode } from "@/types/ws";

const API_BASE = "http://localhost:8000";

interface Props {
  onSessionStart: (sessionId: string, mode: SessionMode) => void;
  onSessionStop: () => void;
  isActive: boolean;
}

export default function SessionControls({
  onSessionStart,
  onSessionStop,
  isActive,
}: Props) {
  const [mode, setMode] = useState<SessionMode>("meeting");
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);

  const handleStart = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ mode, title });
      const res = await fetch(`${API_BASE}/api/sessions?${params}`, {
        method: "POST",
      });
      const data = await res.json();
      onSessionStart(data.session_id, mode);
    } catch (err) {
      console.error("Failed to start session:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    setLoading(true);
    try {
      onSessionStop();
    } finally {
      setLoading(false);
    }
  };

  if (isActive) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            color: "#dc2626",
            fontWeight: 600,
            fontSize: 14,
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              backgroundColor: "#dc2626",
              animation: "pulse 1.5s infinite",
            }}
          />
          Recording
        </span>
        <button
          onClick={handleStop}
          disabled={loading}
          style={{
            padding: "8px 20px",
            backgroundColor: "#dc2626",
            color: "white",
            border: "none",
            borderRadius: 8,
            fontWeight: 600,
            cursor: "pointer",
            fontSize: 14,
          }}
        >
          Stop Session
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <input
        type="text"
        placeholder="Session title (optional)"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        style={{
          padding: "8px 12px",
          border: "1px solid #d1d5db",
          borderRadius: 8,
          fontSize: 14,
          width: 200,
        }}
      />
      <select
        value={mode}
        onChange={(e) => setMode(e.target.value as SessionMode)}
        style={{
          padding: "8px 12px",
          border: "1px solid #d1d5db",
          borderRadius: 8,
          fontSize: 14,
        }}
      >
        <option value="meeting">Meeting</option>
        <option value="interview">Interview</option>
      </select>
      <button
        onClick={handleStart}
        disabled={loading}
        style={{
          padding: "8px 20px",
          backgroundColor: "#2563eb",
          color: "white",
          border: "none",
          borderRadius: 8,
          fontWeight: 600,
          cursor: "pointer",
          fontSize: 14,
        }}
      >
        Start Session
      </button>
    </div>
  );
}
