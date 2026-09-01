/**
 * WebSocket message types — mirrors backend Pydantic models.
 */

export type WSMessageType =
  | "transcript_delta"
  | "summary_update"
  | "suggestion"
  | "session_state"
  | "connection_status"
  | "companion_health"
  | "coverage_gap"
  | "error"
  | "speaker_relabel_batch";

export type ConnectionHealth = "healthy" | "degraded" | "disconnected";
export type PhysicalCaptureState =
  | "not_started"
  | "starting"
  | "active"
  | "pausing"
  | "paused"
  | "stopping"
  | "stopped"
  | "unknown";
export type SourceHealthState =
  | "healthy"
  | "reconnecting"
  | "permission_missing"
  | "permission_revoked"
  | "device_unavailable"
  | "overflow"
  | "failed"
  | "unknown";
export type ErrorSeverity = "warning" | "error" | "fatal";
export type SessionMode = "meeting" | "interview";
export type SessionStatus = "active" | "completed" | "incomplete";
export type ReviewStatus =
  | "available"
  | "ready"
  | "summary_unavailable"
  | "transcript_unavailable"
  | "incomplete"
  | "active"
  | "corrupt";
export type RegenerationStatus =
  | "not_needed"
  | "blocked_source_context"
  | "blocked_session_state";
export type NoteKind =
  | "note"
  | "bookmark"
  | "concern"
  | "strength"
  | "follow_up";
export type ReportStatus = "draft" | "approved";
export type EvidenceSource = "transcript" | "recruiter_note" | "context";
export type TranscriptImportStatus = "queued" | "leased" | "completed" | "failed";

export interface GoogleMeetTranscriptEntry {
  name: string;
  participant: string;
  participantName?: string | null;
  text: string;
  startTime?: string | null;
  endTime?: string | null;
  languageCode?: string | null;
  confidence?: number | null;
}

export interface GoogleMeetTranscriptSession {
  name: string;
  entries: GoogleMeetTranscriptEntry[];
}

export interface GoogleMeetImportRequest {
  sourceType: "GOOGLE_MEET";
  sourceArtifactId: string;
  title: string;
  noticeGiven: boolean;
  noticeProvenance: string;
  transcriptSessions: GoogleMeetTranscriptSession[];
  candidateId?: string | null;
  candidateName?: string | null;
  resumeArtifactId?: string | null;
  resumeText?: string | null;
  jobDescriptionArtifactId?: string | null;
  jobDescriptionText?: string | null;
  briefing?: string | null;
}

export interface GoogleMeetImportResult {
  session_id: string;
  source_key: string;
  source_digest: string;
  status: TranscriptImportStatus;
  segment_count: number;
  attempt_count: number;
  idempotent_replay: boolean;
}

export interface TranscriptSegment {
  id: string;
  text: string;
  speaker: string;
  speaker_override?: string;
  start_time: number;
  end_time: number;
  confidence: number;
  sequence_number: number;
  is_final: boolean;
}

export interface ActionItem {
  text: string;
  assignee?: string;
  completed: boolean;
}

export interface SuggestionEntry {
  questions: string[];
  markdown?: string;
  timestamp: number;
  sequenceNumber: number;
}

export interface Session {
  id: string;
  mode: SessionMode;
  title: string;
  started_at: string;
  ended_at: string | null;
  last_active: string;
  status: SessionStatus;
  notice_given: boolean;
  speaker_map: Record<string, string>;
  summary: string | null;
  action_items: ActionItem[];
  transcript_durability?: "complete" | "pending";
  transcript_failure_count?: number;
}

export interface RecentInterview {
  id: string;
  title: string;
  started_at: string | null;
  ended_at: string | null;
  session_status: SessionStatus | null;
  review_status: ReviewStatus;
}

export interface SessionReview {
  session: Session;
  transcript: TranscriptSegment[];
  summary: string | null;
  review_status: ReviewStatus;
  regeneration_status: RegenerationStatus;
}

export interface RecruiterNote {
  id: string;
  session_id: string;
  kind: NoteKind;
  text: string;
  transcript_segment_id: string;
  transcript_offset_ms: number;
  source: "recruiter";
  created_at: string;
}

export interface EvidenceReference {
  source: EvidenceSource;
  evidence_id: string;
}

export interface InternalReportSection {
  id: string;
  title: string;
  body: string;
  rating: number | null;
  evidence: EvidenceReference[];
}

export interface ClientNarrative {
  trajectory: string;
  assessment: string;
  trajectory_evidence: EvidenceReference[];
  assessment_evidence: EvidenceReference[];
}

export interface InterviewReport {
  session_id: string;
  version: number;
  status: ReportStatus;
  ai_draft_label: "Rascunho gerado por IA";
  internal_sections: InternalReportSection[];
  client_narrative: ClientNarrative;
  created_at: string;
  updated_at: string;
  approved_version: number | null;
  approved_at: string | null;
}

export interface ApprovedClientReport {
  session_id: string;
  version: number;
  trajectory: string;
  assessment: string;
  approved_at: string;
}

export interface TranscriptDelta {
  segment: TranscriptSegment;
}

export interface SummaryUpdate {
  text: string;
  is_final: boolean;
  covering_from: number;
  covering_to: number;
}

export interface Suggestion {
  questions: string[];
  markdown?: string;
  context: string;
}

export interface SessionState {
  session: Session;
  transcript: TranscriptSegment[];
  latest_summary: string | null;
  pending_suggestions: string[];
}

export interface SourceHealthReport {
  microphone: SourceHealthState;
  system_audio: SourceHealthState;
}

export interface CoverageGapSegment {
  id: string;
  source: "microphone" | "system_audio" | "both";
  start_ms: number;
  end_ms: number | null;
  reason: "overrun" | "device_lost" | "permission_denied" | "buffer_exhaustion" | "unknown";
}

export interface CompanionHealthPayload {
  physical_capture: PhysicalCaptureState;
  sources: SourceHealthReport;
  message?: string | null;
}

export interface CoverageGapPayload {
  gap: CoverageGapSegment;
}

export interface ConnectionStatusPayload {
  stt_health: ConnectionHealth;
  ws_health: ConnectionHealth;
  companion_health?: PhysicalCaptureState;
  sources?: SourceHealthReport;
  message: string;
}

export interface ErrorPayload {
  severity: ErrorSeverity;
  message: string;
  code: string;
}

export interface WSMessage {
  type: WSMessageType;
  session_id: string;
  sequence_number: number;
  timestamp: string;
  payload: Record<string, unknown>;
}
