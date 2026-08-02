"use client";

import ReactMarkdown from "react-markdown";

import Chip from "@/components/ui/Chip";
import Label from "@/components/ui/Label";
import { tokens } from "@/lib/tokens";
import type { SuggestionEntry } from "@/types/ws";

const HERO_MARKDOWN_ELEMENTS = [
  "p", "strong", "em", "h3", "h4", "ul", "ol", "li", "code", "br", "hr",
];

interface Props {
  hero: SuggestionEntry | null;
  isStale: boolean;
  queueCount: number;
  onDismiss: () => void;
}

export default function HeroQuestion({
  hero,
  isStale,
  queueCount,
  onDismiss,
}: Props) {
  if (!hero) {
    return (
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: tokens.space.md,
          padding: tokens.space.xl,
        }}
      >
        <p style={{ color: tokens.color.text.secondary, fontSize: tokens.text.body, margin: 0 }}>
          Ouvindo a conversa...
        </p>
        <p style={{ color: tokens.color.text.tertiary, fontSize: tokens.text.small, margin: 0 }}>
          As sugestões aparecem conforme a entrevista avança
        </p>
      </div>
    );
  }

  const question = hero.questions[0]?.trim() ?? "";

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: tokens.space.md,
        padding: `${tokens.space.xl}px ${tokens.space.xxl}px`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: tokens.space.md }}>
        <Label>Próxima pergunta</Label>
        {isStale && <Chip tone="warn">conversa avançou</Chip>}
      </div>

      {question ? (
        <p
          style={{
            fontSize: tokens.text.hero,
            lineHeight: 1.45,
            fontWeight: 500,
            letterSpacing: "-0.2px",
            color: tokens.color.text.primary,
            margin: 0,
          }}
        >
          {question}
        </p>
      ) : (
        <div
          style={{
            fontSize: tokens.text.title,
            lineHeight: 1.5,
            color: tokens.color.text.primary,
          }}
        >
          <ReactMarkdown allowedElements={HERO_MARKDOWN_ELEMENTS}>
            {hero.markdown ?? ""}
          </ReactMarkdown>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: tokens.space.md }}>
        <button
          onClick={onDismiss}
          style={{
            padding: `${tokens.space.sm}px ${tokens.space.lg}px`,
            backgroundColor: tokens.color.surface.base,
            color: tokens.color.accent,
            border: `1px solid ${tokens.color.border.strong}`,
            borderRadius: tokens.radius.pill,
            fontWeight: 500,
            fontSize: tokens.text.small,
            cursor: "pointer",
          }}
        >
          Próxima
        </button>
        {queueCount > 0 && <Chip>{queueCount} na fila</Chip>}
      </div>
    </div>
  );
}
