"use client";

import { useEffect, useRef, useState } from "react";

import {
  formatNoteOffset,
  latestFinalNoteAnchor,
} from "@/lib/recruiterNotes";
import { tokens } from "@/lib/tokens";
import type { NoteKind, RecruiterNote, TranscriptSegment } from "@/types/ws";
import { apiFetch } from "@/lib/auth";
import { apiUrl } from "@/lib/runtimeConfig";

const ACTIONS: Array<{
  kind: NoteKind;
  label: string;
  color: string;
  background: string;
}> = [
  {
    kind: "bookmark",
    label: "Marcar",
    color: tokens.color.accent,
    background: "rgba(0, 122, 255, 0.08)",
  },
  {
    kind: "concern",
    label: "Preocupação",
    color: tokens.color.danger,
    background: tokens.color.dangerWash,
  },
  {
    kind: "strength",
    label: "Ponto forte",
    color: tokens.color.success,
    background: tokens.color.successWash,
  },
  {
    kind: "follow_up",
    label: "Retomar",
    color: "#8a4b00",
    background: "rgba(255, 149, 0, 0.1)",
  },
];

interface NoteRequest {
  client_note_id: string;
  kind: NoteKind;
  transcript_segment_id: string;
}

type SaveState =
  | { status: "idle" }
  | { status: "saving"; label: string; request: NoteRequest }
  | { status: "saved"; label: string; note: RecruiterNote }
  | { status: "failed"; label: string; request: NoteRequest };

interface Props {
  sessionId: string;
  transcript: TranscriptSegment[];
}

export default function NoteChips({ sessionId, transcript }: Props) {
  const anchor = latestFinalNoteAnchor(transcript);
  const [saveState, setSaveState] = useState<SaveState>({ status: "idle" });
  const requestTokenRef = useRef(0);
  const requestControllerRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      requestTokenRef.current += 1;
      requestControllerRef.current?.abort();
    },
    [],
  );

  const persist = async (request: NoteRequest, label: string) => {
    requestControllerRef.current?.abort();
    const controller = new AbortController();
    const requestToken = requestTokenRef.current + 1;
    requestTokenRef.current = requestToken;
    requestControllerRef.current = controller;
    setSaveState({ status: "saving", label, request });

    try {
      const response = await apiFetch(
        apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/notes`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
          signal: controller.signal,
        },
      );
      if (!response.ok) throw new Error("note persistence failed");
      const note = (await response.json()) as RecruiterNote;
      if (
        note.id !== request.client_note_id ||
        note.session_id !== sessionId ||
        note.kind !== request.kind ||
        note.transcript_segment_id !== request.transcript_segment_id ||
        note.source !== "recruiter" ||
        !Number.isFinite(note.transcript_offset_ms) ||
        note.transcript_offset_ms < 0
      ) {
        throw new Error("note acknowledgement mismatch");
      }
      if (
        !controller.signal.aborted &&
        requestToken === requestTokenRef.current
      ) {
        setSaveState({ status: "saved", label, note });
      }
    } catch {
      if (
        !controller.signal.aborted &&
        requestToken === requestTokenRef.current
      ) {
        setSaveState({ status: "failed", label, request });
      }
    } finally {
      if (requestToken === requestTokenRef.current) {
        requestControllerRef.current = null;
      }
    }
  };

  const create = (kind: NoteKind, label: string) => {
    if (!anchor || saveState.status === "saving") return;
    void persist(
      {
        client_note_id: crypto.randomUUID(),
        kind,
        transcript_segment_id: anchor.transcriptSegmentId,
      },
      label,
    );
  };

  const disabled = anchor === null || saveState.status === "saving";

  return (
    <section
      aria-label="Anotações rápidas"
      style={{
        borderTop: `1px solid ${tokens.color.border.subtle}`,
        padding: `${tokens.space.md}px ${tokens.space.xl}px`,
        backgroundColor: tokens.color.surface.base,
        flexShrink: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: tokens.space.md,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", gap: tokens.space.sm, flexWrap: "wrap" }}>
          {ACTIONS.map((action) => (
            <button
              key={action.kind}
              type="button"
              disabled={disabled}
              onClick={() => create(action.kind, action.label)}
              title={
                anchor
                  ? `Ancorar ao último trecho confirmado (${formatNoteOffset(anchor.transcriptOffsetMs)})`
                  : "Disponível após a primeira fala confirmada"
              }
              style={{
                border: `1px solid ${action.color}`,
                borderRadius: tokens.radius.pill,
                padding: `${tokens.space.sm}px ${tokens.space.lg}px`,
                backgroundColor: action.background,
                color: action.color,
                fontSize: tokens.text.small,
                fontWeight: 600,
                cursor: disabled ? "default" : "pointer",
                opacity: disabled ? 0.5 : 1,
              }}
            >
              {action.label}
            </button>
          ))}
        </div>

        <div aria-live="polite" style={{ minHeight: 20 }}>
          {saveState.status === "idle" && !anchor && (
            <span style={{ color: tokens.color.text.tertiary, fontSize: 12 }}>
              Aguarde a primeira fala confirmada
            </span>
          )}
          {saveState.status === "saving" && (
            <span style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
              Salvando {saveState.label.toLowerCase()}…
            </span>
          )}
          {saveState.status === "saved" && (
            <span style={{ color: tokens.color.success, fontSize: 12 }}>
              {saveState.label} salvo em {formatNoteOffset(saveState.note.transcript_offset_ms)}
            </span>
          )}
          {saveState.status === "failed" && (
            <span role="alert" style={{ color: tokens.color.danger, fontSize: 12 }}>
              Não foi possível salvar.{" "}
              <button
                type="button"
                onClick={() => void persist(saveState.request, saveState.label)}
                style={{
                  all: "unset",
                  color: tokens.color.danger,
                  cursor: "pointer",
                  fontWeight: 600,
                  textDecoration: "underline",
                }}
              >
                Tentar novamente
              </button>
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
