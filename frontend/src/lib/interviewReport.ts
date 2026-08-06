import type {
  ApprovedClientReport,
  InterviewReport,
} from "../types/ws.ts";

const FORBIDDEN_CLIENT_TEXT =
  /(?:\b(?:nota|score|rating|rubrica|pontua(?:ção|cao))\b|\bnível\s*(?:de\s*)?(?:[1-5]|um|dois|três|tres|quatro|cinco)\b|\b[1-5]\s*(?:\/|de)\s*5\b|(?:^|[.!?]\s*)(?:não\s+)?recomendad[oa](?:\s+com\s+ressalvas)?\s*[.!?]?$|\b(?:o\s+perfil|a\s+candidata|o\s+candidato|perfil|candidat[oa])\s+(?:está\s+|é\s+|foi\s+)?(?:não\s+)?(?:recomendad[oa]|aprovad[oa]|reprovad[oa])\b|\b(?:aprovad[oa]|reprovad[oa])\s+para\s+(?:a\s+)?vaga\b|\b(?:recomendo|recomendamos)\s+(?:a\s+)?contrata(?:ção|cao)\s+(?:do\s+candidato|da\s+candidata|do\s+perfil)\b|\b(?:a|sua)\s+contrata(?:ção|cao)\s+(?:(?:do\s+candidato|da\s+candidata|do\s+perfil)\s+)?(?:não\s+)?é\s+recomendad[oa]\b|\b(?:aprovar|reprovar|contratar)\s+(?:o\s+candidato|a\s+candidata)\b)/i;
const MARKDOWN_BLOCK = /(^|\n)\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)/;

// Report generation is asynchronous, but polling every 750 ms creates a
// needless request stream while the model is still working. Back off to a
// five-second cadence while preserving roughly the same 90-second wait
// budget as the previous 120 x 750 ms loop.
export const REPORT_POLL_ATTEMPTS = 20;
export function reportPollDelayMs(attempt: number): number {
  const safeAttempt = Number.isFinite(attempt) ? Math.max(0, Math.floor(attempt)) : 0;
  return Math.min(750 * 2 ** safeAttempt, 5000);
}

export function isSafeClientParagraph(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    !FORBIDDEN_CLIENT_TEXT.test(value) &&
    !MARKDOWN_BLOCK.test(value)
  );
}

export function parseApprovedClientReport(
  value: unknown,
  sessionId: string,
  version: number,
): ApprovedClientReport | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<ApprovedClientReport>;
  if (
    candidate.session_id !== sessionId ||
    candidate.version !== version ||
    !isSafeClientParagraph(candidate.trajectory) ||
    !isSafeClientParagraph(candidate.assessment) ||
    typeof candidate.approved_at !== "string"
  ) {
    return null;
  }
  return candidate as ApprovedClientReport;
}

export function buildReportUpdateRequest(
  report: InterviewReport,
  sectionBodies: Record<string, string>,
  trajectory: string,
  assessment: string,
) {
  return {
    expected_version: report.version,
    sections: report.internal_sections.map((section) => ({
      id: section.id,
      body: sectionBodies[section.id] ?? section.body,
    })),
    client_narrative: { trajectory, assessment },
  };
}

export function formatReportOffset(offsetMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(offsetMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
