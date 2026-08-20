"use client";

import type { ReactNode } from "react";
import { tokens } from "@/lib/tokens";

type Tone = "neutral" | "accent" | "success" | "warn";

interface Props {
  children: ReactNode;
  tone?: Tone;
}

const TONE_STYLES: Record<Tone, { color: string; backgroundColor: string }> = {
  neutral: {
    color: tokens.color.text.primary,
    backgroundColor: tokens.color.surface.sunken,
  },
  accent: {
    color: tokens.color.accent,
    backgroundColor: tokens.color.surface.sunken,
  },
  success: {
    color: tokens.color.success,
    backgroundColor: tokens.color.successWash,
  },
  warn: {
    color: tokens.color.warn,
    backgroundColor: tokens.color.dangerWash,
  },
};

export default function Chip({ children, tone = "neutral" }: Props) {
  return (
    <span
      style={{
        ...TONE_STYLES[tone],
        fontSize: tokens.text.micro,
        fontWeight: 600,
        padding: `3px ${tokens.space.md}px`,
        borderRadius: tokens.radius.pill,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}
