import type { SuggestionEntry } from "@/types/ws";

/**
 * Which suggestion batches the interviewer has already worked through.
 *
 * Ordering is by the server-assigned sequence number rather than arrival time,
 * because replayed frames arrive after live ones and are stamped on receipt.
 */
export interface HeroState {
  dismissedThroughSeq: number;
}

export const initialHeroState: HeroState = { dismissedThroughSeq: 0 };

function bySequence(a: SuggestionEntry, b: SuggestionEntry): number {
  return a.sequenceNumber - b.sequenceNumber;
}

/** Does this batch have anything worth showing — an extracted question or raw markdown? */
export function entryHasContent(entry: SuggestionEntry): boolean {
  const question = entry.questions[0]?.trim() ?? "";
  const markdown = entry.markdown?.trim() ?? "";
  return question.length > 0 || markdown.length > 0;
}

/** The oldest batch the interviewer has not yet dismissed. */
export function selectHero(
  history: SuggestionEntry[],
  state: HeroState,
): SuggestionEntry | null {
  const pending = history
    .filter((e) => e.sequenceNumber > state.dismissedThroughSeq && entryHasContent(e))
    .sort(bySequence);
  return pending[0] ?? null;
}

/** True when the conversation has moved past the displayed question. */
export function isHeroStale(
  hero: SuggestionEntry | null,
  history: SuggestionEntry[],
): boolean {
  if (!hero) return false;
  return history.some((e) => e.sequenceNumber > hero.sequenceNumber);
}

/** How many undismissed batches sit behind the hero. */
export function queueCount(
  history: SuggestionEntry[],
  hero: SuggestionEntry | null,
): number {
  if (!hero) return 0;
  return history.filter(
    (e) => e.sequenceNumber > hero.sequenceNumber && entryHasContent(e),
  ).length;
}

export function dismissHero(
  hero: SuggestionEntry | null,
  state: HeroState,
): HeroState {
  if (!hero) return state;
  return { dismissedThroughSeq: Math.max(state.dismissedThroughSeq, hero.sequenceNumber) };
}
