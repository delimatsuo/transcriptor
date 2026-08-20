import type { TranscriptSegment } from "@/types/ws";

export interface NoteAnchor {
  transcriptSegmentId: string;
  transcriptOffsetMs: number;
}

export function latestFinalNoteAnchor(
  transcript: TranscriptSegment[],
): NoteAnchor | null {
  let latest: NoteAnchor | null = null;
  for (const segment of transcript) {
    if (!segment.is_final || !Number.isFinite(segment.end_time)) continue;
    const offset = Math.round(segment.end_time * 1000);
    if (offset < 0) continue;
    if (latest === null || offset >= latest.transcriptOffsetMs) {
      latest = {
        transcriptSegmentId: segment.id,
        transcriptOffsetMs: offset,
      };
    }
  }
  return latest;
}

export function formatNoteOffset(offsetMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(offsetMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
