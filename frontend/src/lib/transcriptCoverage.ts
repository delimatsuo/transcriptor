import type { CoverageGapSegment, TranscriptSegment } from "@/types/ws";

export type TimelineEntry =
  | { kind: "transcript"; segment: TranscriptSegment; sortTime: number }
  | { kind: "gap"; gap: CoverageGapSegment; sortTime: number };

export function formatTimeMs(ms: number): string {
  if (ms < 0 || !Number.isFinite(ms)) return "00:00";
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const mm = minutes.toString().padStart(2, "0");
  const ss = seconds.toString().padStart(2, "0");
  return `${mm}:${ss}`;
}

export function formatTimeRange(startMs: number, endMs: number | null): string {
  const startStr = formatTimeMs(startMs);
  if (endMs === null || !Number.isFinite(endMs)) {
    return `${startStr} – desconhecido`;
  }
  return `${startStr} – ${formatTimeMs(endMs)}`;
}

export function formatGapReason(reason: CoverageGapSegment["reason"]): string {
  switch (reason) {
    case "overrun":
      return "Saturação de buffer de captura";
    case "device_lost":
      return "Dispositivo de áudio desconectado";
    case "permission_denied":
      return "Permissão de áudio revogada";
    case "buffer_exhaustion":
      return "Esgotamento de memória de custódia";
    case "unknown":
    default:
      return "Interrupção de captura";
  }
}

export function formatGapSource(source: CoverageGapSegment["source"]): string {
  switch (source) {
    case "microphone":
      return "Microfone (Entrevistador)";
    case "system_audio":
      return "Áudio do Sistema (Candidato)";
    case "both":
    default:
      return "Ambos os Canais";
  }
}

export function buildTimelineEntries(
  segments: TranscriptSegment[],
  gaps: CoverageGapSegment[] = [],
): TimelineEntry[] {
  const entries: TimelineEntry[] = [];

  for (const seg of segments) {
    // Transcript segment start_time is in seconds or ms; convert to ms
    const sortTime = seg.start_time > 10000 ? seg.start_time : seg.start_time * 1000;
    entries.push({ kind: "transcript", segment: seg, sortTime });
  }

  for (const gap of gaps) {
    entries.push({ kind: "gap", gap, sortTime: gap.start_ms });
  }

  entries.sort((a, b) => a.sortTime - b.sortTime);
  return entries;
}
