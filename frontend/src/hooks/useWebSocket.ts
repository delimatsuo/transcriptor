"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { mergeTranscriptSegment } from "@/lib/transcript";
import { apiFetch, authBypassEnabled } from "@/lib/auth";
import type {
  CompanionHealthPayload,
  ConnectionHealth,
  CoverageGapPayload,
  CoverageGapSegment,
  PhysicalCaptureState,
  SourceHealthReport,
  Suggestion,
  SuggestionEntry,
  TranscriptSegment,
  WSMessage,
} from "@/types/ws";

const WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Exponential backoff: 1s, 2s, 4s, 8s, max 30s
const INITIAL_RETRY_DELAY = 1000;
const MAX_RETRY_DELAY = 30000;

interface UseWebSocketReturn {
  transcript: TranscriptSegment[];
  coverageGaps: CoverageGapSegment[];
  summary: string;
  isSummaryFinal: boolean;
  suggestions: string[];
  suggestionHistory: SuggestionEntry[];
  latestSuggestions: string[];
  connectionHealth: ConnectionHealth;
  companionCaptureState: PhysicalCaptureState;
  sources: SourceHealthReport;
  lastError: string | null;
  connect: (sessionId: string) => void;
  disconnect: () => void;
  hydrateReview: (
    transcript: TranscriptSegment[],
    summary: string | null,
  ) => void;
}

export function useWebSocket(): UseWebSocketReturn {
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([]);
  const [coverageGaps, setCoverageGaps] = useState<CoverageGapSegment[]>([]);
  const [summary, setSummary] = useState("");
  const [isSummaryFinal, setIsSummaryFinal] = useState(false);
  const [suggestionHistory, setSuggestionHistory] = useState<SuggestionEntry[]>(
    [],
  );
  const [connectionHealth, setConnectionHealth] =
    useState<ConnectionHealth>("disconnected");
  const [companionCaptureState, setCompanionCaptureState] =
    useState<PhysicalCaptureState>("not_started");
  const [sources, setSources] = useState<SourceHealthReport>({
    microphone: "unknown",
    system_audio: "unknown",
  });
  const [lastError, setLastError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const sessionIdRef = useRef<string>("");
  const lastSeqRef = useRef<number>(0);
  const retryDelayRef = useRef(INITIAL_RETRY_DELAY);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalCloseRef = useRef(false);

  const handleMessage = useCallback((event: MessageEvent) => {
    const msg: WSMessage = JSON.parse(event.data);
    lastSeqRef.current = Math.max(lastSeqRef.current, msg.sequence_number);

    switch (msg.type) {
      case "transcript_delta": {
        const segment = msg.payload.segment as unknown as TranscriptSegment;
        setTranscript((prev) => mergeTranscriptSegment(prev, segment));
        break;
      }
      case "summary_update": {
        const payload = msg.payload as unknown as {
          text: string;
          is_final: boolean;
        };
        setSummary(payload.text);
        setIsSummaryFinal(payload.is_final);
        break;
      }
      case "suggestion": {
        const payload = msg.payload as unknown as Suggestion;
        const entry: SuggestionEntry = {
          questions: payload.questions,
          markdown: payload.markdown,
          timestamp: Date.now(),
          sequenceNumber: msg.sequence_number,
        };
        setSuggestionHistory((prev) => [...prev, entry]);
        break;
      }
      case "session_state": {
        const state = msg.payload as unknown as {
          transcript: TranscriptSegment[];
          latest_summary: string | null;
          pending_suggestions: string[];
        };
        setTranscript(state.transcript);
        if (state.latest_summary) setSummary(state.latest_summary);
        if (state.pending_suggestions) {
          setSuggestionHistory((prev) => [
            ...prev,
            {
              questions: state.pending_suggestions,
              timestamp: Date.now(),
              sequenceNumber: msg.sequence_number,
            },
          ]);
        }
        break;
      }
      case "connection_status": {
        const payload = msg.payload as unknown as {
          stt_health: ConnectionHealth;
          ws_health: ConnectionHealth;
        };
        // Use the worse health status
        if (
          payload.stt_health === "disconnected" ||
          payload.ws_health === "disconnected"
        ) {
          setConnectionHealth("disconnected");
        } else if (
          payload.stt_health === "degraded" ||
          payload.ws_health === "degraded"
        ) {
          setConnectionHealth("degraded");
        } else {
          setConnectionHealth("healthy");
        }
        break;
      }
      case "error": {
        const payload = msg.payload as unknown as {
          message: string;
          severity: string;
        };
        setLastError(payload.message);
        break;
      }
      case "coverage_gap": {
        const payload = msg.payload as unknown as CoverageGapPayload;
        if (payload?.gap) {
          setCoverageGaps((prev) => [...prev, payload.gap]);
        }
        break;
      }
      case "companion_health": {
        const payload = msg.payload as unknown as CompanionHealthPayload;
        if (payload?.sources) {
          setSources(payload.sources);
        }
        if (payload?.physical_capture) {
          setCompanionCaptureState(payload.physical_capture);
        }
        break;
      }
      case "speaker_relabel_batch": {
        const { updates } = msg.payload as unknown as {
          updates: Array<{ segment_id: string; new_speaker: string }>;
        };
        setTranscript((prev) => {
          const updateMap = new Map(
            updates.map((u) => [u.segment_id, u.new_speaker]),
          );
          return prev.map((seg) =>
            updateMap.has(seg.id)
              ? { ...seg, speaker_override: updateMap.get(seg.id)! }
              : seg,
          );
        });
        break;
      }
    }
  }, []);

  const connectWs = useCallback(
    (sessionId: string) => {
      intentionalCloseRef.current = false;
      sessionIdRef.current = sessionId;

      // Clear previous state
      setTranscript([]);
      setCoverageGaps([]);
      setSummary("");
      setIsSummaryFinal(false);
      setSuggestionHistory([]);
      setSources({ microphone: "unknown", system_audio: "unknown" });
      setCompanionCaptureState("not_started");
      setLastError(null);
      lastSeqRef.current = 0;
      retryDelayRef.current = INITIAL_RETRY_DELAY;

      const doConnect = async () => {
        try {
          const ticketPayload = authBypassEnabled
            ? { ticket: "playwright-ticket" }
            : await (async () => {
                const ticketResponse = await apiFetch(
                  `${API_BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}/ws-ticket`,
                  { method: "POST" },
                );
                if (!ticketResponse.ok) {
                  throw new Error("A sessão autenticada do áudio expirou.");
                }
                return (await ticketResponse.json()) as { ticket?: string };
              })();
          if (!ticketPayload.ticket || intentionalCloseRef.current || sessionIdRef.current !== sessionId) {
            return;
          }
          const url = `${WS_BASE_URL}/${sessionId}?last_seq=${lastSeqRef.current}`;
          const ws = new WebSocket(url, ["tars-ticket", ticketPayload.ticket]);

          ws.onopen = () => {
            setConnectionHealth("healthy");
            retryDelayRef.current = INITIAL_RETRY_DELAY;
          };

          ws.onmessage = handleMessage;

          ws.onclose = () => {
            setConnectionHealth("disconnected");
            wsRef.current = null;

            if (!intentionalCloseRef.current) {
              retryTimeoutRef.current = setTimeout(() => {
                void doConnect();
              }, retryDelayRef.current);

              retryDelayRef.current = Math.min(
                retryDelayRef.current * 2,
                MAX_RETRY_DELAY,
              );
            }
          };

          ws.onerror = () => {
            setConnectionHealth("degraded");
          };

          wsRef.current = ws;
        } catch (error) {
          if (intentionalCloseRef.current) return;
          setConnectionHealth("degraded");
          setLastError(error instanceof Error ? error.message : "Falha na conexão autenticada.");
          retryTimeoutRef.current = setTimeout(() => {
            void doConnect();
          }, retryDelayRef.current);
          retryDelayRef.current = Math.min(retryDelayRef.current * 2, MAX_RETRY_DELAY);
        }
      };

      void doConnect();
    },
    [handleMessage],
  );

  const disconnect = useCallback(() => {
    intentionalCloseRef.current = true;
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setConnectionHealth("disconnected");
  }, []);

  const hydrateReview = useCallback(
    (
      persistedTranscript: TranscriptSegment[],
      persistedSummary: string | null,
    ) => {
      setTranscript(persistedTranscript);
      setCoverageGaps([]);
      setSummary(persistedSummary ?? "");
      setIsSummaryFinal(Boolean(persistedSummary));
      setSuggestionHistory([]);
      setSources({ microphone: "unknown", system_audio: "unknown" });
      setCompanionCaptureState("not_started");
      setLastError(null);
      setConnectionHealth("disconnected");
    },
    [],
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      intentionalCloseRef.current = true;
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  // Derive backward-compatible values
  const latestSuggestions =
    suggestionHistory.length > 0
      ? suggestionHistory[suggestionHistory.length - 1].questions
      : [];

  return {
    transcript,
    coverageGaps,
    summary,
    isSummaryFinal,
    suggestions: latestSuggestions,
    suggestionHistory,
    latestSuggestions,
    connectionHealth,
    companionCaptureState,
    sources,
    lastError,
    connect: connectWs,
    disconnect,
    hydrateReview,
  };
}
