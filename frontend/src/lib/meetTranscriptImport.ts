import type {
  GoogleMeetImportRequest,
  GoogleMeetImportResult,
  ManualMeetTranscriptSyncRequest,
  MeetTranscriptAutomationResult,
  TranscriptImportStatus,
} from "../types/ws";

export const MAX_MEET_IMPORT_BYTES = 2_000_000;

export class MeetTranscriptImportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MeetTranscriptImportError";
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function utf8Length(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: Set<string>): boolean {
  return Object.keys(value).every((key) => allowed.has(key));
}

function nonemptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

const REQUEST_KEYS = new Set([
  "sourceType", "sourceArtifactId", "title", "noticeGiven", "noticeProvenance",
  "transcriptSessions", "candidateId", "candidateName", "resumeArtifactId",
  "resumeText", "jobDescriptionArtifactId", "jobDescriptionText", "briefing",
]);
const SESSION_KEYS = new Set(["name", "entries"]);
const ENTRY_KEYS = new Set([
  "name", "participant", "participantName", "text", "startTime", "endTime",
  "languageCode", "confidence",
]);

function optionalString(value: unknown): boolean {
  return value === undefined || value === null || nonemptyString(value);
}

function isPresent(value: unknown): boolean {
  return value !== undefined && value !== null;
}

function validEntry(value: unknown): boolean {
  if (!isObject(value) || !hasOnlyKeys(value, ENTRY_KEYS)) return false;
  return (
    nonemptyString(value.name) &&
    nonemptyString(value.participant) &&
    nonemptyString(value.text) &&
    optionalString(value.participantName) &&
    optionalString(value.startTime) &&
    optionalString(value.endTime) &&
    optionalString(value.languageCode) &&
    (value.confidence === undefined || value.confidence === null ||
      (typeof value.confidence === "number" &&
        Number.isFinite(value.confidence) &&
        value.confidence >= 0 &&
        value.confidence <= 1))
  );
}

function validSession(value: unknown): boolean {
  return (
    isObject(value) &&
    hasOnlyKeys(value, SESSION_KEYS) &&
    nonemptyString(value.name) &&
    Array.isArray(value.entries) &&
    value.entries.length > 0 &&
    value.entries.every(validEntry)
  );
}

export function validateMeetImportFile(fileName: string, size: number): void {
  if (!fileName || fileName.includes("/") || fileName.includes("\\")) {
    throw new MeetTranscriptImportError("Selecione um arquivo local .json, não um caminho.");
  }
  if (!fileName.toLowerCase().endsWith(".json")) {
    throw new MeetTranscriptImportError("O fixture precisa ter extensão .json.");
  }
  if (!Number.isSafeInteger(size) || size < 1 || size > MAX_MEET_IMPORT_BYTES) {
    throw new MeetTranscriptImportError("O fixture deve ter no máximo 2 MB.");
  }
}

export function parseMeetImportFixture(
  raw: string,
  fileName = "fixture.json",
): GoogleMeetImportRequest {
  validateMeetImportFile(fileName, utf8Length(raw));
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new MeetTranscriptImportError("O fixture não contém JSON válido.");
  }
  if (!isObject(parsed)) {
    throw new MeetTranscriptImportError("O fixture precisa conter um objeto JSON.");
  }
  if (
    !hasOnlyKeys(parsed, REQUEST_KEYS) ||
    parsed.sourceType !== "GOOGLE_MEET" ||
    !nonemptyString(parsed.sourceArtifactId) ||
    !nonemptyString(parsed.title) ||
    typeof parsed.noticeGiven !== "boolean" ||
    !nonemptyString(parsed.noticeProvenance) ||
    !Array.isArray(parsed.transcriptSessions) ||
    parsed.transcriptSessions.length < 1 ||
    parsed.transcriptSessions.length > 8 ||
    !parsed.transcriptSessions.every(validSession) ||
    !optionalString(parsed.candidateId) ||
    !optionalString(parsed.candidateName) ||
    !optionalString(parsed.resumeArtifactId) ||
    !optionalString(parsed.resumeText) ||
    !optionalString(parsed.jobDescriptionArtifactId) ||
    !optionalString(parsed.jobDescriptionText) ||
    !optionalString(parsed.briefing) ||
    (isPresent(parsed.resumeArtifactId) !== isPresent(parsed.resumeText)) ||
    (isPresent(parsed.jobDescriptionArtifactId) !==
      isPresent(parsed.jobDescriptionText))
  ) {
    throw new MeetTranscriptImportError("O fixture não é um pedido de importação Meet válido.");
  }
  return parsed as unknown as GoogleMeetImportRequest;
}

const IMPORT_STATUSES = new Set<TranscriptImportStatus>([
  "queued",
  "leased",
  "completed",
  "failed",
]);
const RESULT_KEYS = new Set([
  "session_id",
  "source_key",
  "source_digest",
  "status",
  "segment_count",
  "attempt_count",
  "idempotent_replay",
]);

export function parseMeetImportResult(value: unknown): GoogleMeetImportResult {
  if (!isObject(value) || Object.keys(value).some((key) => !RESULT_KEYS.has(key))) {
    throw new MeetTranscriptImportError("A resposta da importação é inválida.");
  }
  const status = value.status;
  if (
    typeof value.session_id !== "string" ||
    !/^meet-import-[0-9a-f]{32}$/.test(value.session_id) ||
    typeof value.source_key !== "string" ||
    !/^[0-9a-f]{64}$/.test(value.source_key) ||
    typeof value.source_digest !== "string" ||
    !/^[0-9a-f]{64}$/.test(value.source_digest) ||
    typeof status !== "string" ||
    !IMPORT_STATUSES.has(status as TranscriptImportStatus) ||
    !Number.isSafeInteger(value.segment_count) ||
    (value.segment_count as number) < 0 ||
    !Number.isSafeInteger(value.attempt_count) ||
    (value.attempt_count as number) < 0 ||
    typeof value.idempotent_replay !== "boolean"
  ) {
    throw new MeetTranscriptImportError("A resposta da importação é inválida.");
  }
  return value as unknown as GoogleMeetImportResult;
}

const EXACT_ID = /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,511}$/;

function exactInput(value: string, label: string, pattern?: RegExp): string {
  if (
    value.length < 1 ||
    value !== value.trim() ||
    /[\u0000-\u001f\u007f]/.test(value) ||
    (pattern !== undefined && !pattern.test(value))
  ) {
    throw new MeetTranscriptImportError(`${label} é inválido.`);
  }
  return value;
}

export function buildMeetTranscriptSyncRequest(values: {
  grantId: string;
  calendarId: string;
  calendarEventId: string;
}): ManualMeetTranscriptSyncRequest {
  const grantId = exactInput(values.grantId, "O ID da autorização", EXACT_ID);
  const calendarId = exactInput(values.calendarId, "O ID do calendário");
  if (calendarId.length > 1024 || /\s/.test(calendarId)) {
    throw new MeetTranscriptImportError("O ID do calendário é inválido.");
  }
  const calendarEventId = exactInput(
    values.calendarEventId,
    "O ID do evento",
    EXACT_ID,
  );
  return { grantId, calendarId, calendarEventId };
}

const AUTOMATION_RESULT_KEYS = new Set([...RESULT_KEYS, "automation_replay"]);

export function parseMeetTranscriptAutomationResult(
  value: unknown,
): MeetTranscriptAutomationResult {
  if (
    !isObject(value) ||
    !hasOnlyKeys(value, AUTOMATION_RESULT_KEYS) ||
    Object.keys(value).length !== AUTOMATION_RESULT_KEYS.size ||
    typeof value.automation_replay !== "boolean"
  ) {
    throw new MeetTranscriptImportError("A resposta da sincronização é inválida.");
  }
  const base: Record<string, unknown> = {};
  for (const key of RESULT_KEYS) base[key] = value[key];
  try {
    parseMeetImportResult(base);
  } catch {
    throw new MeetTranscriptImportError("A resposta da sincronização é inválida.");
  }
  return value as unknown as MeetTranscriptAutomationResult;
}
