"use client";

import { useEffect, useRef } from "react";
import type { TranscriptSegment } from "@/types/ws";

interface Props {
  segments: TranscriptSegment[];
  speakerMap?: Record<string, string>;
  readOnly?: boolean;
}

const SPEAKER_STYLES: Record<string, { color: string; bg: string; label: string }> = {
  Entrevistador: { color: "#007aff", bg: "rgba(0, 122, 255, 0.08)", label: "Entrevistador" },
  Interviewer: { color: "#007aff", bg: "rgba(0, 122, 255, 0.08)", label: "Interviewer" },
  Candidato: { color: "#34c759", bg: "rgba(52, 199, 89, 0.08)", label: "Candidato" },
  Candidate: { color: "#34c759", bg: "rgba(52, 199, 89, 0.08)", label: "Candidate" },
};

function getSpeakerStyle(speaker: string) {
  // Check for known speaker labels
  for (const [key, style] of Object.entries(SPEAKER_STYLES)) {
    if (speaker.toLowerCase().includes(key.toLowerCase())) {
      return style;
    }
  }
  // Fallback: alternate colors based on speaker ID
  const fallbackColors = [
    { color: "#007aff", bg: "rgba(0, 122, 255, 0.08)" },
    { color: "#34c759", bg: "rgba(52, 199, 89, 0.08)" },
    { color: "#af52de", bg: "rgba(175, 82, 222, 0.08)" },
    { color: "#ff9500", bg: "rgba(255, 149, 0, 0.08)" },
    { color: "#ff3b30", bg: "rgba(255, 59, 48, 0.08)" },
  ];
  const idx = parseInt(speaker.replace(/\D/g, "") || "0", 10) % fallbackColors.length;
  return { ...fallbackColors[idx], label: speaker };
}

export default function TranscriptPanel({ segments, speakerMap = {}, readOnly = false }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (readOnly) return;
    // Only auto-scroll if user is near the bottom
    const container = containerRef.current;
    if (container) {
      const isNearBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight < 120;
      if (isNearBottom) {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      }
    }
  }, [segments.length, readOnly]);

  const getSpeakerLabel = (seg: TranscriptSegment) => {
    const speaker = seg.speaker_override || seg.speaker;
    return speakerMap[speaker] || speaker;
  };

  return (
    <div
      ref={containerRef}
      style={{
        flex: 1,
        overflowY: "auto",
        padding: "24px 28px",
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      {!readOnly && (
        <h2
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "#86868b",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            marginBottom: 16,
          }}
        >
          Transcrição
        </h2>
      )}

      {segments.length === 0 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: 120,
          }}
        >
          <p style={{ color: "#86868b", fontSize: 15 }}>
            Aguardando fala...
          </p>
        </div>
      )}

      {segments.map((seg) => {
        const speakerLabel = getSpeakerLabel(seg);
        const style = getSpeakerStyle(speakerLabel);
        return (
          <div
            key={seg.id}
            style={{
              padding: "10px 16px",
              borderRadius: 12,
              backgroundColor: seg.is_final ? "transparent" : "#fafafa",
              opacity: seg.is_final ? 1 : 0.6,
              transition: "opacity 0.3s ease",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 4,
              }}
            >
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: style.color,
                  backgroundColor: style.bg,
                  padding: "2px 10px",
                  borderRadius: 100,
                  letterSpacing: "0.2px",
                }}
              >
                {speakerLabel}
              </span>
              {!seg.is_final && (
                <span
                  style={{
                    fontSize: 10,
                    color: "#aeaeb2",
                    fontStyle: "italic",
                  }}
                >
                  transcrevendo...
                </span>
              )}
            </div>
            <div
              style={{
                fontSize: 15,
                lineHeight: 1.6,
                color: seg.is_final ? "#1d1d1f" : "#aeaeb2",
                paddingLeft: 2,
              }}
            >
              {seg.text}
            </div>
          </div>
        );
      })}

      <div ref={bottomRef} />
    </div>
  );
}
