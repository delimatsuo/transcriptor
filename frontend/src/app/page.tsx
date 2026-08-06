"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useWebSocket } from "@/hooks/useWebSocket";
import ConnectionStatus from "@/components/ConnectionStatus";
import SessionControls from "@/components/SessionControls";
import PreSessionView from "@/components/views/PreSessionView";
import InterviewLiveView from "@/components/views/InterviewLiveView";
import MeetingLiveView from "@/components/views/MeetingLiveView";
import PostSessionView from "@/components/views/PostSessionView";
import { reviewWarning as getReviewWarning } from "@/lib/sessionReview";
import { requestSessionStop } from "@/lib/sessionStop";
import { apiFetch, useAuth } from "@/lib/auth";
import AuthControls from "@/components/AuthControls";
import type { SessionMode, SessionReview } from "@/types/ws";

const API_BASE = "http://localhost:8000";

function reviewLoadError(status: number): string {
  if (status === 404) {
    return "Esta entrevista não existe mais ou foi removida.";
  }
  if (status === 409) {
    return "O registro persistido desta entrevista é inválido e não pode ser reaberto.";
  }
  return "Não foi possível reabrir esta entrevista persistida.";
}

export default function Home() {
  const auth = useAuth();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionMode, setSessionMode] = useState<SessionMode>("meeting");
  const [isActive, setIsActive] = useState(false);
  const [preInterviewBriefing, setPreInterviewBriefing] = useState("");
  const [stopError, setStopError] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [persistedReviewWarning, setPersistedReviewWarning] = useState<
    string | null
  >(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [stopCapability, setStopCapability] = useState<string | null>(null);
  const disconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reviewRequestRef = useRef<AbortController | null>(null);
  const reviewRequestTokenRef = useRef(0);
  const previousUidRef = useRef<string | null>(null);

  const {
    transcript,
    summary,
    isSummaryFinal,
    suggestionHistory,
    connectionHealth,
    lastError,
    connect,
    disconnect,
    hydrateReview,
  } = useWebSocket();

  // Authentication is a hard data boundary. Clear all prior-account state
  // synchronously before rendering a different principal.
  useEffect(() => {
    const uid = auth.user?.uid ?? null;
    if (previousUidRef.current !== null && previousUidRef.current !== uid) {
      disconnect();
      reviewRequestTokenRef.current += 1;
      reviewRequestRef.current?.abort();
      setSessionId(null);
      setSessionMode("interview");
      setIsActive(false);
      setPreInterviewBriefing("");
      setStopError(null);
      setReviewError(null);
      setPersistedReviewWarning(null);
      setStopCapability(null);
      hydrateReview([], null);
      window.history.replaceState({}, "", window.location.pathname);
    }
    previousUidRef.current = uid;
  }, [auth.user?.uid, disconnect, hydrateReview]);

  const handleSessionStart = useCallback(
    (id: string, mode: SessionMode, initialStopCapability?: string) => {
      reviewRequestTokenRef.current += 1;
      reviewRequestRef.current?.abort();
      reviewRequestRef.current = null;
      setReviewLoading(false);
      // Cancel any pending disconnect from a previous session
      if (disconnectTimerRef.current) {
        clearTimeout(disconnectTimerRef.current);
        disconnectTimerRef.current = null;
        disconnect();
      }
      setSessionId(id);
      setSessionMode(mode);
      setIsActive(true);
      setStopError(null);
      setReviewError(null);
      setPersistedReviewWarning(null);
      setStopCapability(initialStopCapability ?? null);
      window.history.replaceState({}, "", window.location.pathname);
      connect(id);
    },
    [connect, disconnect],
  );

  const handleOpenReview = useCallback(
    async (id: string, updateLocation = true) => {
      reviewRequestRef.current?.abort();
      const controller = new AbortController();
      const requestToken = reviewRequestTokenRef.current + 1;
      reviewRequestTokenRef.current = requestToken;
      reviewRequestRef.current = controller;
      setReviewLoading(true);
      setReviewError(null);
      try {
        const response = await apiFetch(
          `${API_BASE}/api/sessions/${encodeURIComponent(id)}/review`,
          { signal: controller.signal },
        );
        if (!response.ok) {
          throw new Error(reviewLoadError(response.status));
        }
        const review = (await response.json()) as SessionReview;
        if (
          controller.signal.aborted ||
          requestToken !== reviewRequestTokenRef.current
        ) {
          return;
        }
        const warning = getReviewWarning(review);
        if (review.session.status !== "completed") {
          setReviewError(
            warning ?? "Esta entrevista não está disponível para revisão.",
          );
          return;
        }

        disconnect();
        hydrateReview(review.transcript, review.summary);
        setSessionId(review.session.id);
        setSessionMode(review.session.mode);
        setIsActive(false);
        setStopError(null);
        setPersistedReviewWarning(warning);
        setPreInterviewBriefing("");

        if (updateLocation) {
          const url = new URL(window.location.href);
          url.searchParams.set("review", review.session.id);
          window.history.replaceState({}, "", `${url.pathname}${url.search}`);
        }
      } catch (error) {
        if (
          !controller.signal.aborted &&
          requestToken === reviewRequestTokenRef.current
        ) {
          setReviewError(
            error instanceof Error
              ? error.message
              : "Não foi possível reabrir esta entrevista persistida.",
          );
        }
      } finally {
        if (requestToken === reviewRequestTokenRef.current) {
          reviewRequestRef.current = null;
          setReviewLoading(false);
        }
      }
    },
    [disconnect, hydrateReview, auth.status],
  );

  useEffect(() => {
    const reviewId = new URLSearchParams(window.location.search).get("review");
    if (reviewId && auth.status === "signed_in") void handleOpenReview(reviewId, false);
  }, [handleOpenReview, auth.status]);

  useEffect(
    () => () => {
      reviewRequestTokenRef.current += 1;
      reviewRequestRef.current?.abort();
    },
    [],
  );

  const handleSessionStop = useCallback(async () => {
    if (!sessionId) {
      return;
    }

    try {
      const outcome = await requestSessionStop(
        apiFetch,
        `${API_BASE}/api/sessions/${sessionId}/stop`,
        stopCapability,
      );
      setStopError(outcome.warning);
    } catch (err) {
      console.error("Failed to stop session:", err);
      setStopError(
        "Encerramento não confirmado: a captura pode continuar ativa. Tente encerrar novamente.",
      );
      return;
    }

    setIsActive(false);
    disconnectTimerRef.current = setTimeout(() => {
      disconnect();
      disconnectTimerRef.current = null;
    }, 10000);
  }, [sessionId, disconnect, stopCapability]);

  const handleSignOut = useCallback(async () => {
    if (isActive) {
      setStopError("Encerre a entrevista e aguarde a confirmação antes de sair.");
      return;
    }
    await auth.signOut();
  }, [auth, isActive]);

  if (auth.status === "initializing") {
    return <main role="status" aria-live="polite" style={{ padding: 40 }}>Verificando acesso…</main>;
  }
  if (!auth.user) {
    return (
      <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24 }}>
        <section aria-labelledby="auth-title" style={{ maxWidth: 460, textAlign: "center" }}>
          <h1 id="auth-title">T.A.R.S.</h1>
          <p>Entre com sua conta Google autorizada para preparar entrevistas com aviso e controle de acesso.</p>
          <AuthControls status={auth.status} user={auth.user} error={auth.error} onSignIn={auth.signIn} onSignOut={handleSignOut} />
        </section>
      </main>
    );
  }

  const isInterview = sessionMode === "interview";
  const isPostSession = !isActive && sessionId !== null;
  const hasContent = isActive || isPostSession;

  return (
    <div
      key={auth.user.uid}
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        maxWidth: isActive && isInterview ? 1440 : 960,
        margin: "0 auto",
        transition: "max-width 0.3s ease",
      }}
    >
      {/* Header */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 28px",
          borderBottom: "1px solid #f5f5f7",
          flexShrink: 0,
          backgroundColor: "rgba(255, 255, 255, 0.8)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h1
            style={{
              fontSize: 17,
              fontWeight: 600,
              margin: 0,
              color: "#1d1d1f",
              letterSpacing: "-0.2px",
            }}
          >
            T.A.R.S.
          </h1>
          {hasContent && <ConnectionStatus health={connectionHealth} />}
        </div>
        <SessionControls
          onSessionStart={handleSessionStart}
          onSessionStop={handleSessionStop}
          onBriefingReady={setPreInterviewBriefing}
          isActive={isActive}
          sessionId={sessionId}
          disabled={reviewLoading}
        />
        <AuthControls
          status={auth.status}
          user={auth.user}
          error={auth.error}
          onSignIn={auth.signIn}
          onSignOut={handleSignOut}
          disabled={isActive}
        />
      </header>

      {/* Error banner */}
      {(stopError || reviewError || lastError) && (
        <div
          style={{
            padding: "10px 28px",
            backgroundColor: "rgba(255, 59, 48, 0.06)",
            color: "#ff3b30",
            fontSize: 13,
            borderBottom: "1px solid rgba(255, 59, 48, 0.1)",
            flexShrink: 0,
            fontWeight: 500,
          }}
        >
          {stopError || reviewError || lastError}
        </div>
      )}

      {/* Pre-session: centered welcome or briefing */}
      {!hasContent && (
        <PreSessionView
          preInterviewBriefing={preInterviewBriefing}
          onOpenReview={handleOpenReview}
        />
      )}

      {/* Active interview: two-column layout */}
      {isActive && isInterview && sessionId && (
        <InterviewLiveView
          sessionId={sessionId}
          transcript={transcript}
          suggestionHistory={suggestionHistory}
        />
      )}

      {/* Active meeting: single column */}
      {isActive && !isInterview && (
        <MeetingLiveView
          transcript={transcript}
          suggestionHistory={suggestionHistory}
          summary={summary}
          isSummaryFinal={isSummaryFinal}
        />
      )}

      {/* Post-session review */}
      {isPostSession && (
        <PostSessionView
          sessionId={sessionId}
          transcript={transcript}
          summary={summary}
          isSummaryFinal={isSummaryFinal}
          isInterview={isInterview}
          reviewWarning={persistedReviewWarning}
          onNewSession={() => {
            reviewRequestTokenRef.current += 1;
            reviewRequestRef.current?.abort();
            reviewRequestRef.current = null;
            setReviewLoading(false);
            hydrateReview([], null);
            setSessionId(null);
            setStopCapability(null);
            setIsActive(false);
            setStopError(null);
            setReviewError(null);
            setPersistedReviewWarning(null);
            window.history.replaceState({}, "", window.location.pathname);
          }}
        />
      )}
    </div>
  );
}
