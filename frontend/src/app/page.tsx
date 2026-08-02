"use client";

import { useCallback, useRef, useState } from "react";
import { useWebSocket } from "@/hooks/useWebSocket";
import ConnectionStatus from "@/components/ConnectionStatus";
import SessionControls from "@/components/SessionControls";
import PreSessionView from "@/components/views/PreSessionView";
import InterviewLiveView from "@/components/views/InterviewLiveView";
import MeetingLiveView from "@/components/views/MeetingLiveView";
import PostSessionView from "@/components/views/PostSessionView";
import type { SessionMode } from "@/types/ws";

const API_BASE = "http://localhost:8000";

export default function Home() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionMode, setSessionMode] = useState<SessionMode>("meeting");
  const [isActive, setIsActive] = useState(false);
  const [preInterviewBriefing, setPreInterviewBriefing] = useState("");
  const disconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const {
    transcript,
    summary,
    isSummaryFinal,
    suggestionHistory,
    connectionHealth,
    lastError,
    connect,
    disconnect,
  } = useWebSocket();

  const handleSessionStart = useCallback(
    (id: string, mode: SessionMode) => {
      // Cancel any pending disconnect from a previous session
      if (disconnectTimerRef.current) {
        clearTimeout(disconnectTimerRef.current);
        disconnectTimerRef.current = null;
        disconnect();
      }
      setSessionId(id);
      setSessionMode(mode);
      setIsActive(true);
      connect(id);
    },
    [connect, disconnect],
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
    disconnectTimerRef.current = setTimeout(() => {
      disconnect();
      disconnectTimerRef.current = null;
    }, 10000);
  }, [sessionId, disconnect]);

  const isInterview = sessionMode === "interview";
  const isPostSession = !isActive && sessionId !== null;
  const hasContent = isActive || isPostSession;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        maxWidth: isActive && isInterview ? 1440 : 960,
        margin: "0 auto",
        transition: "max-width 0.3s ease",
      }}
    >
      {/* Header */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 28px",
          borderBottom: "1px solid #f5f5f7",
          flexShrink: 0,
          backgroundColor: "rgba(255, 255, 255, 0.8)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h1
            style={{
              fontSize: 17,
              fontWeight: 600,
              margin: 0,
              color: "#1d1d1f",
              letterSpacing: "-0.2px",
            }}
          >
            T.A.R.S.
          </h1>
          {hasContent && <ConnectionStatus health={connectionHealth} />}
        </div>
        <SessionControls
          onSessionStart={handleSessionStart}
          onSessionStop={handleSessionStop}
          onBriefingReady={setPreInterviewBriefing}
          isActive={isActive}
          sessionId={sessionId}
        />
      </header>

      {/* Error banner */}
      {lastError && (
        <div
          style={{
            padding: "10px 28px",
            backgroundColor: "rgba(255, 59, 48, 0.06)",
            color: "#ff3b30",
            fontSize: 13,
            borderBottom: "1px solid rgba(255, 59, 48, 0.1)",
            flexShrink: 0,
            fontWeight: 500,
          }}
        >
          {lastError}
        </div>
      )}

      {/* Pre-session: centered welcome or briefing */}
      {!hasContent && (
        <PreSessionView preInterviewBriefing={preInterviewBriefing} />
      )}

      {/* Active interview: two-column layout */}
      {isActive && isInterview && (
        <InterviewLiveView
          transcript={transcript}
          suggestionHistory={suggestionHistory}
        />
      )}

      {/* Active meeting: single column */}
      {isActive && !isInterview && (
        <MeetingLiveView
          transcript={transcript}
          suggestionHistory={suggestionHistory}
          summary={summary}
          isSummaryFinal={isSummaryFinal}
        />
      )}

      {/* Post-session review */}
      {isPostSession && (
        <PostSessionView
          transcript={transcript}
          summary={summary}
          isSummaryFinal={isSummaryFinal}
          isInterview={isInterview}
          onNewSession={() => {
            setSessionId(null);
            setIsActive(false);
          }}
        />
      )}
    </div>
  );
}
