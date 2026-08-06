"use client";

import { useState } from "react";

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
import type { SuggestionEntry, TranscriptSegment } from "@/types/ws";

interface Props {
  sessionId: string;
  transcript: TranscriptSegment[];
  suggestionHistory: SuggestionEntry[];
}

export default function InterviewLiveView({
  sessionId,
  transcript,
  suggestionHistory,
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
        open={sheetOpen}
        onToggle={() => setSheetOpen((o) => !o)}
      />
    </div>
  );
}
