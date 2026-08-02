"use client";

import type { ReactNode } from "react";
import { tokens } from "@/lib/tokens";

interface Props {
  children: ReactNode;
}

/** Small uppercase section heading, e.g. "PRÓXIMA PERGUNTA". */
export default function Label({ children }: Props) {
  return (
    <div
      style={{
        fontSize: tokens.text.caption,
        fontWeight: 600,
        color: tokens.color.text.secondary,
        textTransform: "uppercase",
        letterSpacing: "0.5px",
      }}
    >
      {children}
    </div>
  );
}
