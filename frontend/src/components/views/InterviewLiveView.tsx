"use client";

import { useState } from "react";

import CaptureSourceStatus from "@/components/CaptureSourceStatus";
import CompanionCommand from "@/components/CompanionCommand";
import HeroQuestion from "@/components/HeroQuestion";
import NoteChips from "@/components/NoteChips";
import QuestionsSheet from "@/components/QuestionsSheet";
import TranscriptSheet from "@/components/TranscriptSheet";
import {
  dismissHero,
  initialHeroState,
  isHeroStale,
  queueCount,
  questionLog,
  selectHero,
} from "@/lib/interviewQueue";
import { tokens } from "@/lib/tokens";
import type {
  CoverageGapSegment,
  PhysicalCaptureState,
  SourceHealthReport,
  SuggestionEntry,
  TranscriptSegment,
} from "@/types/ws";

interface Props {
  sessionId: string;
  transcript: TranscriptSegment[];
  gaps?: CoverageGapSegment[];
  sources?: SourceHealthReport;
  captureState?: PhysicalCaptureState;
  suggestionHistory: SuggestionEntry[];
  streamKey?: string;
}

export default function InterviewLiveView({
  sessionId,
  transcript,
  gaps = [],
  sources,
  captureState,
  suggestionHistory,
  streamKey,
}: Props) {
  const [heroState, setHeroState] = useState(initialHeroState);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [questionsOpen, setQuestionsOpen] = useState(false);

  const hero = selectHero(suggestionHistory, heroState);
  const stale = isHeroStale(hero, suggestionHistory);
  const queued = queueCount(suggestionHistory, hero);
  const log = questionLog(suggestionHistory, heroState);

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        backgroundColor: tokens.color.surface.base,
      }}
    >
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
        />
        <CompanionCommand sessionId={sessionId} streamKey={streamKey} />
      </div>
      <HeroQuestion
        hero={hero}
        isStale={stale}
        queueCount={queued}
        onDismiss={() => setHeroState((s) => dismissHero(hero, s))}
        onShowQueue={() => setQuestionsOpen(true)}
      />
      <NoteChips sessionId={sessionId} transcript={transcript} />
      <QuestionsSheet
        log={log}
        open={questionsOpen}
        onToggle={() => setQuestionsOpen((o) => !o)}
      />
      <TranscriptSheet
        segments={transcript}
        gaps={gaps}
        open={sheetOpen}
        onToggle={() => setSheetOpen((o) => !o)}
      />
    </div>
  );
}
