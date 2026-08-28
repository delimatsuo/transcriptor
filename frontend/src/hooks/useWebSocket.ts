"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { mergeTranscriptSegment } from "@/lib/transcript";
import { apiFetch } from "@/lib/auth";
import { getRuntimeConfig } from "@/lib/runtimeConfig";
import {
  IAP_AUTH_TERMINAL_EVENT,
  emitIapTerminalAuthEvent,
  isIapTerminalClose,
} from "@/lib/iapSession";
import {
  boundedRetryDelay,
  cancelLifecycleRetry,
  commitIfCurrent,
  createLifecycleGenerationController,
  isIapTerminalHttpStatus,
  lifecycleAttemptIsCurrent,
  scheduleLifecycleRetry,
} from "@/lib/iapLifecycle";
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

const runtimeConfig = getRuntimeConfig();
const WS_BASE_URL = runtimeConfig.wsUrl;
const API_BASE_URL = runtimeConfig.apiOrigin;

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
  companionMessage: string | null;
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
  const [companionMessage, setCompanionMessage] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const sessionIdRef = useRef<string>("");
  const lastSeqRef = useRef<number>(0);
  const retryDelayRef = useRef(INITIAL_RETRY_DELAY);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalCloseRef = useRef(false);
  const generationRef = useRef(0);
  const lifecycleControllerRef = useRef(createLifecycleGenerationController());
  const connectAbortRef = useRef<AbortController | null>(null);

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
        setCompanionMessage(payload?.message ?? null);
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
      if (retryTimeoutRef.current) {
        cancelLifecycleRetry(retryTimeoutRef.current);
        retryTimeoutRef.current = null;
      }
      const previousSocket = wsRef.current;
      wsRef.current = null;
      previousSocket?.close();
      const generation = lifecycleControllerRef.current.begin();
      generationRef.current = generation;
      intentionalCloseRef.current = false;
      sessionIdRef.current = sessionId;
      connectAbortRef.current?.abort();
      const controller = new AbortController();
      connectAbortRef.current = controller;

      const attemptIsCurrent = () =>
        lifecycleAttemptIsCurrent(
          generation,
          generationRef.current,
          sessionId,
          sessionIdRef.current || null,
        );

      // Clear previous state
      setTranscript([]);
      setCoverageGaps([]);
      setSummary("");
      setIsSummaryFinal(false);
      setSuggestionHistory([]);
      setSources({ microphone: "unknown", system_audio: "unknown" });
      setCompanionCaptureState("not_started");
      setCompanionMessage(null);
      setLastError(null);
      lastSeqRef.current = 0;
      retryDelayRef.current = INITIAL_RETRY_DELAY;

      const doConnect = async () => {
        try {
          const ticketResponse = await apiFetch(
            `${API_BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}/ws-ticket`,
            { method: "POST", signal: controller.signal },
          );
          if (!attemptIsCurrent() || controller.signal.aborted) return;
          if (!ticketResponse.ok) {
            if (runtimeConfig.iap && isIapTerminalHttpStatus(ticketResponse.status)) {
              intentionalCloseRef.current = true;
              emitIapTerminalAuthEvent();
              return;
            }
            throw new Error("A sessão autenticada do áudio expirou.");
          }
          const ticketPayload = (await ticketResponse.json()) as { ticket?: string };
          if (!ticketPayload.ticket || !attemptIsCurrent() || intentionalCloseRef.current) {
            return;
          }
          const url = `${WS_BASE_URL}/${sessionId}?last_seq=${lastSeqRef.current}`;
          let ws = new WebSocket(url, ["tars-ticket", ticketPayload.ticket]);

          ws.onopen = () => {
            if (!attemptIsCurrent() || wsRef.current !== ws) {
              ws.close();
              return;
            }
            setConnectionHealth("healthy");
            retryDelayRef.current = INITIAL_RETRY_DELAY;
          };

          ws.onmessage = (event) => {
            if (attemptIsCurrent() && wsRef.current === ws) handleMessage(event);
          };

          ws.onclose = (event) => {
            if (!attemptIsCurrent() || wsRef.current !== ws) return;
            setConnectionHealth("disconnected");
            wsRef.current = null;

            if (runtimeConfig.iap && isIapTerminalClose(event.code, event.reason)) {
              intentionalCloseRef.current = true;
              generationRef.current = lifecycleControllerRef.current.invalidate();
              controller.abort();
              emitIapTerminalAuthEvent();
              return;
            }
            if (!intentionalCloseRef.current && attemptIsCurrent()) {
              const delay = retryDelayRef.current;
              retryTimeoutRef.current = scheduleLifecycleRetry(attemptIsCurrent, delay, () => {
                retryTimeoutRef.current = null;
                if (!intentionalCloseRef.current) void doConnect();
              });

              retryDelayRef.current = boundedRetryDelay(delay, MAX_RETRY_DELAY);
            }
          };

          ws.onerror = () => {
            if (attemptIsCurrent() && wsRef.current === ws) setConnectionHealth("degraded");
          };

          if (!attemptIsCurrent() || intentionalCloseRef.current) {
            ws.close();
            return;
          }
          const committedSocket = commitIfCurrent(ws, attemptIsCurrent, (stale) => stale.close());
          if (!committedSocket) return;
          ws = committedSocket;
          wsRef.current = ws;
        } catch (error) {
          if (controller.signal.aborted || !attemptIsCurrent() || intentionalCloseRef.current) return;
          setConnectionHealth("degraded");
          setLastError(error instanceof Error ? error.message : "Falha na conexão autenticada.");
          const delay = retryDelayRef.current;
          retryTimeoutRef.current = scheduleLifecycleRetry(attemptIsCurrent, delay, () => {
            retryTimeoutRef.current = null;
            if (!intentionalCloseRef.current) void doConnect();
          });
          retryDelayRef.current = boundedRetryDelay(delay, MAX_RETRY_DELAY);
        }
      };

      void doConnect();
    },
    [handleMessage],
  );

  const disconnect = useCallback(() => {
    generationRef.current = lifecycleControllerRef.current.invalidate();
    intentionalCloseRef.current = true;
    sessionIdRef.current = "";
    connectAbortRef.current?.abort();
    connectAbortRef.current = null;
    cancelLifecycleRetry(retryTimeoutRef.current);
    retryTimeoutRef.current = null;
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
      setCompanionMessage(null);
      setLastError(null);
      setConnectionHealth("disconnected");
    },
    [],
  );

  // Cleanup on unmount
  useEffect(() => {
    const onTerminalAuth = () => {
              generationRef.current = lifecycleControllerRef.current.invalidate();
      intentionalCloseRef.current = true;
      sessionIdRef.current = "";
      connectAbortRef.current?.abort();
      connectAbortRef.current = null;
      cancelLifecycleRetry(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setConnectionHealth("disconnected");
    };
    window.addEventListener(IAP_AUTH_TERMINAL_EVENT, onTerminalAuth);
    return () => {
      window.removeEventListener(IAP_AUTH_TERMINAL_EVENT, onTerminalAuth);
      generationRef.current = lifecycleControllerRef.current.invalidate();
      intentionalCloseRef.current = true;
      sessionIdRef.current = "";
      connectAbortRef.current?.abort();
      cancelLifecycleRetry(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
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
    companionMessage,
    lastError,
    connect: connectWs,
    disconnect,
    hydrateReview,
  };
}
