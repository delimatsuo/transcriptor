import type { TranscriptSegment } from "@/types/ws";

/**
 * Merge an incoming transcript segment into the current transcript list.
 *
 * T.A.R.S. runs two independent capture streams (microphone and BlackHole),
 * so interim results from both speakers are in flight simultaneously during
 * any two-way conversation. Interim state is therefore tracked per speaker:
 * one speaker's partial text must never replace or discard another's.
 */
export function mergeTranscriptSegment(
  prev: TranscriptSegment[],
  segment: TranscriptSegment,
): TranscriptSegment[] {
  if (segment.is_final) {
    // If exact segment ID already exists, update in-place rather than duplicating
    const existingIdx = prev.findIndex((s) => s.id === segment.id);
    if (existingIdx >= 0) {
      const updated = [...prev];
      updated[existingIdx] = segment;
      return updated;
    }

    // Retire only this speaker's interim; other speakers may still be mid-sentence.
    const kept = prev.filter((s) => s.is_final || s.speaker !== segment.speaker);
    return [...kept, segment];
  }

  const idx = prev.findIndex(
    (s) => !s.is_final && s.speaker === segment.speaker,
  );
  if (idx >= 0) {
    const updated = [...prev];
    updated[idx] = segment;
    return updated;
  }

  return [...prev, segment];
}
