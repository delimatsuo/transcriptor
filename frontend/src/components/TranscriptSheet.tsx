"use client";

import TranscriptPanel from "@/components/TranscriptPanel";
import { tokens } from "@/lib/tokens";
import type { CoverageGapSegment, TranscriptSegment } from "@/types/ws";

interface Props {
  segments: TranscriptSegment[];
  gaps?: CoverageGapSegment[];
  open: boolean;
  onToggle: () => void;
}

export default function TranscriptSheet({
  segments,
  gaps = [],
  open,
  onToggle,
}: Props) {
  return (
    <div
      style={{
        borderTop: `1px solid ${tokens.color.border.subtle}`,
        backgroundColor: tokens.color.surface.base,
        flexShrink: 0,
      }}
    >
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: `${tokens.space.md}px ${tokens.space.xl}px`,
          background: "none",
          border: "none",
          cursor: "pointer",
          fontSize: tokens.text.small,
          color: tokens.color.text.secondary,
        }}
      >
        <span>Transcrição</span>
        <span aria-hidden="true">{open ? "⌄" : "⌃"}</span>
      </button>

      {open && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            maxHeight: 320,
            overflow: "hidden",
          }}
        >
          <TranscriptPanel segments={segments} gaps={gaps} />
        </div>
      )}
    </div>
  );
}
