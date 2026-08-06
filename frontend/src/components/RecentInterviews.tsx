"use client";

import { useEffect, useState } from "react";

import {
  canOpenRecentInterview,
  recentInterviewStatusLabel,
} from "@/lib/sessionReview";
import { tokens } from "@/lib/tokens";
import type { RecentInterview } from "@/types/ws";
import { apiFetch } from "@/lib/auth";

const API_BASE = "http://localhost:8000";

interface Props {
  onOpen: (sessionId: string) => void;
}

function formatInterviewDate(value: string | null): string {
  if (!value) return "Data indisponível";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Data indisponível";
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export default function RecentInterviews({ onOpen }: Props) {
  const [interviews, setInterviews] = useState<RecentInterview[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const response = await apiFetch(`${API_BASE}/api/sessions/recent-interviews`);
        if (!response.ok) throw new Error("recent interviews unavailable");
        const payload = (await response.json()) as {
          interviews?: RecentInterview[];
        };
        if (!cancelled) setInterviews(payload.interviews ?? []);
      } catch {
        if (!cancelled) {
          setError("Não foi possível carregar as entrevistas recentes.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section
      aria-label="Entrevistas recentes"
      style={{
        width: "min(680px, 100%)",
        marginTop: tokens.space.xl,
        borderTop: `1px solid ${tokens.color.border.subtle}`,
        paddingTop: tokens.space.lg,
      }}
    >
      <h3
        style={{
          margin: "0 0 4px",
          fontSize: tokens.text.body,
          color: tokens.color.text.primary,
        }}
      >
        Entrevistas recentes
      </h3>
      <p
        style={{
          margin: `0 0 ${tokens.space.md}px`,
          fontSize: tokens.text.caption,
          color: tokens.color.text.secondary,
        }}
      >
        Reabra uma entrevista persistida para revisar a transcrição e o relatório.
      </p>

      {loading && (
        <p style={{ color: tokens.color.text.tertiary, fontSize: 13 }}>
          Carregando…
        </p>
      )}
      {error && (
        <p role="alert" style={{ color: tokens.color.warn, fontSize: 13 }}>
          {error}
        </p>
      )}
      {!loading && !error && interviews.length === 0 && (
        <p style={{ color: tokens.color.text.tertiary, fontSize: 13 }}>
          Nenhuma entrevista persistida ainda.
        </p>
      )}

      <div style={{ display: "grid", gap: tokens.space.sm }}>
        {interviews.map((interview) => {
          const canOpen = canOpenRecentInterview(interview);
          return (
            <button
              key={interview.id}
              type="button"
              disabled={!canOpen}
              onClick={() => onOpen(interview.id)}
              aria-label={`Abrir ${interview.title}`}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: tokens.space.md,
                width: "100%",
                padding: `${tokens.space.md}px ${tokens.space.lg}px`,
                border: `1px solid ${tokens.color.border.subtle}`,
                borderRadius: tokens.radius.md,
                background: tokens.color.surface.raised,
                textAlign: "left",
                cursor: canOpen ? "pointer" : "not-allowed",
                opacity: canOpen ? 1 : 0.62,
              }}
            >
              <span>
                <strong
                  style={{
                    display: "block",
                    color: tokens.color.text.primary,
                    fontSize: tokens.text.body,
                  }}
                >
                  {interview.title || "Entrevista sem título"}
                </strong>
                <span
                  style={{
                    color: tokens.color.text.tertiary,
                    fontSize: tokens.text.caption,
                  }}
                >
                  {formatInterviewDate(interview.started_at)}
                </span>
              </span>
              <span
                style={{
                  color:
                    interview.review_status === "available" ||
                    interview.review_status === "ready"
                      ? tokens.color.success
                      : tokens.color.warn,
                  fontSize: tokens.text.caption,
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
              >
                {recentInterviewStatusLabel(interview)}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
