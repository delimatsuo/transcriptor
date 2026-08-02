"use client";

import TranscriptPanel from "@/components/TranscriptPanel";
import SummaryPanel from "@/components/SummaryPanel";
import type { TranscriptSegment } from "@/types/ws";

interface Props {
  transcript: TranscriptSegment[];
  summary: string;
  isSummaryFinal: boolean;
  isInterview: boolean;
  onNewSession: () => void;
}

export default function PostSessionView({
  transcript,
  summary,
  isSummaryFinal,
  isInterview,
  onNewSession,
}: Props) {
  return (
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
              Sessão concluída
            </h2>
            <p
              style={{
                fontSize: 13,
                color: "#86868b",
                margin: 0,
              }}
            >
              Revise sua {isInterview ? "entrevista" : "reunião"} abaixo
            </p>
          </div>
          <button
            onClick={onNewSession}
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
            Nova sessão
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
          Transcrição completa
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
  );
}
