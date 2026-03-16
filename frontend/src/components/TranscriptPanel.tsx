"use client";

import { useEffect, useRef } from "react";
import type { TranscriptSegment } from "@/types/ws";

interface Props {
  segments: TranscriptSegment[];
  speakerMap?: Record<string, string>;
}

export default function TranscriptPanel({ segments, speakerMap = {} }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [segments.length]);

  const getSpeakerLabel = (speaker: string) => speakerMap[speaker] || speaker;

  const getSpeakerColor = (speaker: string) => {
    const colors = ["#3b82f6", "#8b5cf6", "#ec4899", "#f97316", "#14b8a6"];
    const idx =
      parseInt(speaker.replace(/\D/g, "") || "0", 10) % colors.length;
    return colors[idx];
  };

  return (
    <div
      style={{
        flex: 1,
        overflowY: "auto",
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>
        Transcript
      </h2>

      {segments.length === 0 && (
        <p style={{ color: "#9ca3af", fontStyle: "italic" }}>
          Waiting for speech...
        </p>
      )}

      {segments.map((seg) => (
        <div
          key={seg.id}
          style={{
            padding: "8px 12px",
            borderRadius: 8,
            backgroundColor: seg.is_final ? "#f9fafb" : "#fffbeb",
            borderLeft: `3px solid ${getSpeakerColor(seg.speaker)}`,
            opacity: seg.is_final ? 1 : 0.7,
          }}
        >
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: getSpeakerColor(seg.speaker),
              marginBottom: 2,
            }}
          >
            {getSpeakerLabel(seg.speaker)}
          </div>
          <div style={{ fontSize: 14, lineHeight: 1.5 }}>{seg.text}</div>
        </div>
      ))}

      <div ref={bottomRef} />
    </div>
  );
}
