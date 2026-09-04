"use client";

import CaptureSourceStatus from "@/components/CaptureSourceStatus";
import CompanionCommand from "@/components/CompanionCommand";
import TranscriptPanel from "@/components/TranscriptPanel";
import SummaryPanel from "@/components/SummaryPanel";
import SuggestionsPanel from "@/components/SuggestionsPanel";
import { tokens } from "@/lib/tokens";
import type {
  CoverageGapSegment,
  PhysicalCaptureState,
  SourceHealthReport,
  SuggestionEntry,
  TranscriptSegment,
} from "@/types/ws";

interface Props {
  sessionId?: string;
  transcript: TranscriptSegment[];
  gaps?: CoverageGapSegment[];
  sources?: SourceHealthReport;
  captureState?: PhysicalCaptureState;
  companionMessage?: string | null;
  suggestionHistory: SuggestionEntry[];
  summary: string;
  isSummaryFinal: boolean;
  streamKey?: string;
}

export default function MeetingLiveView({
  sessionId,
  transcript,
  gaps,
  sources,
  captureState,
  companionMessage,
  suggestionHistory,
  summary,
  isSummaryFinal,
  streamKey,
}: Props) {
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {sessionId && streamKey && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: `${tokens.space.xs}px ${tokens.space.md}px`,
            borderBottom: `1px solid ${tokens.color.border.subtle}`,
            backgroundColor: tokens.color.surface.raised,
            gap: tokens.space.md,
          }}
        >
          <CaptureSourceStatus
            captureState={captureState}
            micHealth={sources?.microphone}
            systemAudioHealth={sources?.system_audio}
            message={companionMessage}
          />
          <CompanionCommand
            sessionId={sessionId}
            streamKey={streamKey}
            isConnected={sources?.system_audio === "healthy"}
          />
        </div>
      )}
      <TranscriptPanel segments={transcript} gaps={gaps} />
      {suggestionHistory.length > 0 && (
        <div style={{ borderTop: "1px solid #f5f5f7", maxHeight: 200, overflow: "auto" }}>
          <SuggestionsPanel suggestionHistory={suggestionHistory} />
        </div>
      )}
      <SummaryPanel summary={summary} isFinal={isSummaryFinal} />
    </div>
  );
}
