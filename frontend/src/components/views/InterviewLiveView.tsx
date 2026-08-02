"use client";

import { useState } from "react";

import HeroQuestion from "@/components/HeroQuestion";
import TranscriptSheet from "@/components/TranscriptSheet";
import {
  dismissHero,
  initialHeroState,
  isHeroStale,
  queueCount,
  selectHero,
} from "@/lib/interviewQueue";
import { tokens } from "@/lib/tokens";
import type { SuggestionEntry, TranscriptSegment } from "@/types/ws";

interface Props {
  transcript: TranscriptSegment[];
  suggestionHistory: SuggestionEntry[];
}

export default function InterviewLiveView({
  transcript,
  suggestionHistory,
}: Props) {
  const [heroState, setHeroState] = useState(initialHeroState);
  const [sheetOpen, setSheetOpen] = useState(false);

  const hero = selectHero(suggestionHistory, heroState);
  const stale = isHeroStale(hero, suggestionHistory);
  const queued = queueCount(suggestionHistory, hero);

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
      />
      <TranscriptSheet
        segments={transcript}
        open={sheetOpen}
        onToggle={() => setSheetOpen((o) => !o)}
      />
    </div>
  );
}
