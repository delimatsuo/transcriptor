"use client";

import { useCallback, useState } from "react";
import { useWebSocket } from "@/hooks/useWebSocket";
import ConnectionStatus from "@/components/ConnectionStatus";
import TranscriptPanel from "@/components/TranscriptPanel";
import SummaryPanel from "@/components/SummaryPanel";
import SuggestionsPanel from "@/components/SuggestionsPanel";
import SessionControls from "@/components/SessionControls";
import DocumentUpload from "@/components/DocumentUpload";
import type { SessionMode } from "@/types/ws";

const API_BASE = "http://localhost:8000";

export default function Home() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionMode, setSessionMode] = useState<SessionMode>("meeting");
  const [isActive, setIsActive] = useState(false);

  const {
    transcript,
    summary,
    isSummaryFinal,
    suggestions,
    connectionHealth,
    lastError,
    connect,
    disconnect,
  } = useWebSocket();

  const handleSessionStart = useCallback(
    (id: string, mode: SessionMode) => {
      setSessionId(id);
      setSessionMode(mode);
      setIsActive(true);
      connect(id);
    },
    [connect],
  );

  const handleSessionStop = useCallback(async () => {
    if (sessionId) {
      try {
        await fetch(`${API_BASE}/api/sessions/${sessionId}/stop`, {
          method: "POST",
        });
      } catch (err) {
        console.error("Failed to stop session:", err);
      }
    }
    setIsActive(false);
    // Don't disconnect WS yet — wait for final summary
    setTimeout(() => disconnect(), 10000);
  }, [sessionId, disconnect]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        maxWidth: 1200,
        margin: "0 auto",
      }}
    >
      {/* Header */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: "1px solid #e5e7eb",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>T.A.R.S.</h1>
          <ConnectionStatus health={connectionHealth} />
        </div>
        <SessionControls
          onSessionStart={handleSessionStart}
          onSessionStop={handleSessionStop}
          isActive={isActive}
        />
      </header>

      {/* Error banner */}
      {lastError && (
        <div
          style={{
            padding: "8px 16px",
            backgroundColor: "#fef2f2",
            color: "#dc2626",
            fontSize: 13,
            borderBottom: "1px solid #fecaca",
          }}
        >
          {lastError}
        </div>
      )}

      {/* Interview document upload */}
      {isActive && sessionMode === "interview" && sessionId && (
        <div style={{ padding: "12px 16px" }}>
          <DocumentUpload sessionId={sessionId} />
        </div>
      )}

      {/* Main content */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <TranscriptPanel segments={transcript} />
        <SuggestionsPanel questions={suggestions} />
        <SummaryPanel summary={summary} isFinal={isSummaryFinal} />
      </div>
    </div>
  );
}
