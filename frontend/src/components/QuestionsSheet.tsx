"use client";

import Label from "@/components/ui/Label";
import { tokens } from "@/lib/tokens";
import type { QuestionLog } from "@/lib/interviewQueue";
import type { SuggestionEntry } from "@/types/ws";

interface Props {
  log: QuestionLog;
  open: boolean;
  onToggle: () => void;
}

function entryText(entry: SuggestionEntry): string {
  const question = entry.questions[0]?.trim() ?? "";
  if (question) return question;
  const markdown = entry.markdown?.trim() ?? "";
  return markdown.length > 140 ? `${markdown.slice(0, 140)}…` : markdown;
}

function QuestionRow({
  entry,
  tone,
}: {
  entry: SuggestionEntry;
  tone: "atual" | "fila" | "anterior";
}) {
  const color =
    tone === "anterior" ? tokens.color.text.tertiary : tokens.color.text.primary;
  return (
    <li
      style={{
        padding: `${tokens.space.sm}px 0`,
        fontSize: tokens.text.body,
        lineHeight: 1.4,
        color,
        fontWeight: tone === "atual" ? 600 : 400,
        borderBottom: `1px solid ${tokens.color.border.subtle}`,
        listStyle: "none",
      }}
    >
      {entryText(entry)}
    </li>
  );
}

function Section({
  title,
  entries,
  tone,
}: {
  title: string;
  entries: SuggestionEntry[];
  tone: "atual" | "fila" | "anterior";
}) {
  if (entries.length === 0) return null;
  return (
    <div style={{ marginBottom: tokens.space.lg }}>
      <Label>{title}</Label>
      <ul style={{ margin: `${tokens.space.sm}px 0 0`, padding: 0 }}>
        {entries.map((e) => (
          <QuestionRow key={e.sequenceNumber} entry={e} tone={tone} />
        ))}
      </ul>
    </div>
  );
}

export default function QuestionsSheet({ log, open, onToggle }: Props) {
  const total =
    (log.atual ? 1 : 0) + log.fila.length + log.anteriores.length;

  return (
    <div
      style={{
        borderTop: `1px solid ${tokens.color.border.subtle}`,
        backgroundColor: tokens.color.surface.base,
        flexShrink: 0,
      }}
    >
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: `${tokens.space.md}px ${tokens.space.xl}px`,
          background: "none",
          border: "none",
          cursor: "pointer",
          fontSize: tokens.text.small,
          color: tokens.color.text.secondary,
        }}
      >
        <span>Perguntas{total > 0 ? ` (${total})` : ""}</span>
        <span aria-hidden="true">{open ? "⌄" : "⌃"}</span>
      </button>

      {open && (
        <div
          style={{
            maxHeight: 320,
            overflowY: "auto",
            padding: `0 ${tokens.space.xl}px ${tokens.space.lg}px`,
          }}
        >
          {total === 0 ? (
            <p
              style={{
                color: tokens.color.text.tertiary,
                fontSize: tokens.text.small,
                margin: 0,
              }}
            >
              Nenhuma pergunta ainda — as sugestões aparecem conforme a
              entrevista avança.
            </p>
          ) : (
            <>
              <Section title="Atual" entries={log.atual ? [log.atual] : []} tone="atual" />
              <Section title="Na fila" entries={log.fila} tone="fila" />
              <Section title="Anteriores" entries={log.anteriores} tone="anterior" />
            </>
          )}
        </div>
      )}
    </div>
  );
}
