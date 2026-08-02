"use client";

import type { CSSProperties, ReactNode } from "react";
import { tokens } from "@/lib/tokens";

interface Props {
  children: ReactNode;
  tone?: "base" | "raised" | "sunken";
  style?: CSSProperties;
}

export default function Panel({ children, tone = "base", style }: Props) {
  return (
    <div
      style={{
        backgroundColor: tokens.color.surface[tone],
        borderRadius: tokens.radius.md,
        padding: tokens.space.lg,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
