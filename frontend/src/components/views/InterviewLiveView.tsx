"use client";

import TranscriptPanel from "@/components/TranscriptPanel";
import SuggestionsPanel from "@/components/SuggestionsPanel";
import type { SuggestionEntry, TranscriptSegment } from "@/types/ws";

interface Props {
  transcript: TranscriptSegment[];
  suggestionHistory: SuggestionEntry[];
  preInterviewBriefing: string;
}

export default function InterviewLiveView({
  transcript,
  suggestionHistory,
  preInterviewBriefing,
}: Props) {
  return (
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
  );
}
