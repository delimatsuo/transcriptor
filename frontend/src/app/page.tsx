"use client";

import { useCallback, useRef, useState } from "react";
import { useWebSocket } from "@/hooks/useWebSocket";
import ConnectionStatus from "@/components/ConnectionStatus";
import TranscriptPanel from "@/components/TranscriptPanel";
import SummaryPanel from "@/components/SummaryPanel";
import SuggestionsPanel from "@/components/SuggestionsPanel";
import SessionControls from "@/components/SessionControls";
import BriefingDisplay from "@/components/BriefingDisplay";
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
        <div
          style={{
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            alignItems: preInterviewBriefing ? "stretch" : "center",
            justifyContent: preInterviewBriefing ? "flex-start" : "center",
            gap: 8,
            padding: preInterviewBriefing ? "24px 28px" : 40,
            overflowY: "auto",
          }}
        >
          {preInterviewBriefing ? (
            <BriefingDisplay markdown={preInterviewBriefing} />
          ) : (
            <>
              <h2
                style={{
                  fontSize: 28,
                  fontWeight: 600,
                  color: "#1d1d1f",
                  margin: 0,
                  letterSpacing: "-0.5px",
                }}
              >
                Ready to begin
              </h2>
              <p
                style={{
                  fontSize: 15,
                  color: "#86868b",
                  margin: 0,
                  textAlign: "center",
                  maxWidth: 400,
                  lineHeight: 1.5,
                }}
              >
                Configure your session above and start recording. T.A.R.S. will
                transcribe and assist in real time.
              </p>
            </>
          )}
        </div>
      )}

      {/* Active interview: two-column layout */}
      {isActive && isInterview && (
        <div
          style={{
            flex: 1,
            display: "flex",
            overflow: "hidden",
          }}
        >
          {/* Left: Transcript (60%) */}
          <div
            style={{
              flex: "0 0 60%",
              overflow: "hidden",
              borderRight: "1px solid #f5f5f7",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <TranscriptPanel segments={transcript} />
          </div>

          {/* Right: Interview Assistant (40%) */}
          <div
            style={{
              flex: "0 0 40%",
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
              backgroundColor: "#fafafa",
            }}
          >
            <SuggestionsPanel
              suggestionHistory={suggestionHistory}
              briefing={preInterviewBriefing}
              isInterview
            />
          </div>
        </div>
      )}

      {/* Active meeting: single column */}
      {isActive && !isInterview && (
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <TranscriptPanel segments={transcript} />
          {suggestionHistory.length > 0 && (
            <div style={{ borderTop: "1px solid #f5f5f7", maxHeight: 200, overflow: "auto" }}>
              <SuggestionsPanel suggestionHistory={suggestionHistory} />
            </div>
          )}
          <SummaryPanel summary={summary} isFinal={isSummaryFinal} />
        </div>
      )}

      {/* Post-session review */}
      {isPostSession && (
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "auto",
          }}
        >
          {/* Session ended banner */}
          <div
            style={{
              padding: "16px 28px",
              backgroundColor: "#f5f5f7",
              borderBottom: "1px solid #e8e8ed",
              flexShrink: 0,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <div>
                <h2
                  style={{
                    fontSize: 17,
                    fontWeight: 600,
                    color: "#1d1d1f",
                    margin: "0 0 2px 0",
                  }}
                >
                  Session Complete
                </h2>
                <p
                  style={{
                    fontSize: 13,
                    color: "#86868b",
                    margin: 0,
                  }}
                >
                  Review your {isInterview ? "interview" : "meeting"} below
                </p>
              </div>
              <button
                onClick={() => {
                  setSessionId(null);
                  setIsActive(false);
                }}
                style={{
                  padding: "8px 18px",
                  backgroundColor: "white",
                  color: "#007aff",
                  border: "1px solid #d2d2d7",
                  borderRadius: 100,
                  fontWeight: 500,
                  cursor: "pointer",
                  fontSize: 13,
                  transition: "all 0.2s ease",
                }}
              >
                New Session
              </button>
            </div>
          </div>

          {/* Transcript (read-only) */}
          <div
            style={{
              padding: "24px 28px",
              borderBottom: "1px solid #f5f5f7",
            }}
          >
            <h3
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "#86868b",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                margin: "0 0 16px 0",
              }}
            >
              Full Transcript
            </h3>
            <div
              style={{
                maxHeight: 400,
                overflowY: "auto",
                borderRadius: 12,
                border: "1px solid #f0f0f0",
                backgroundColor: "#fafafa",
              }}
            >
              <TranscriptPanel segments={transcript} readOnly />
            </div>
          </div>

          {/* Summary / Assessment */}
          <SummaryPanel
            summary={summary}
            isFinal={isSummaryFinal}
            transcript={transcript}
            isInterview={isInterview}
          />
        </div>
      )}
    </div>
  );
}
