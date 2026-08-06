/**
 * WebSocket message types — mirrors backend Pydantic models.
 */

export type WSMessageType =
  | "transcript_delta"
  | "summary_update"
  | "suggestion"
  | "session_state"
  | "connection_status"
  | "error"
  | "speaker_relabel_batch";

export type ConnectionHealth = "healthy" | "degraded" | "disconnected";
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

export interface ConnectionStatusPayload {
  stt_health: ConnectionHealth;
  ws_health: ConnectionHealth;
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
