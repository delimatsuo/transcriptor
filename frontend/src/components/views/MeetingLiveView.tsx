"use client";

import TranscriptPanel from "@/components/TranscriptPanel";
import SummaryPanel from "@/components/SummaryPanel";
import SuggestionsPanel from "@/components/SuggestionsPanel";
import type { SuggestionEntry, TranscriptSegment } from "@/types/ws";

interface Props {
  transcript: TranscriptSegment[];
  suggestionHistory: SuggestionEntry[];
  summary: string;
  isSummaryFinal: boolean;
}

export default function MeetingLiveView({
  transcript,
  suggestionHistory,
  summary,
  isSummaryFinal,
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
      <TranscriptPanel segments={transcript} />
      {suggestionHistory.length > 0 && (
        <div style={{ borderTop: "1px solid #f5f5f7", maxHeight: 200, overflow: "auto" }}>
          <SuggestionsPanel suggestionHistory={suggestionHistory} />
        </div>
      )}
      <SummaryPanel summary={summary} isFinal={isSummaryFinal} />
    </div>
  );
}
