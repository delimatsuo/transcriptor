"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import type { TranscriptSegment } from "@/types/ws";

const SUMMARY_ALLOWED_ELEMENTS = [
  "p", "strong", "em", "h2", "h3", "h4", "ul", "ol", "li", "code", "br", "hr",
];

interface Props {
  summary: string;
  isFinal: boolean;
  transcript?: TranscriptSegment[];
  isInterview?: boolean;
}

function downloadText(content: string, filename: string) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

const SummaryMarkdown = memo(function SummaryMarkdown({ summary }: { summary: string }) {
  return (
    <ReactMarkdown allowedElements={SUMMARY_ALLOWED_ELEMENTS}>
      {summary}
    </ReactMarkdown>
  );
});

export default function SummaryPanel({
  summary,
  isFinal,
  transcript = [],
  isInterview = false,
}: Props) {
  if (!summary) return null;

  const handleDownloadSummary = () => {
    downloadText(summary, "resumo-sessao.txt");
  };

  const handleDownloadTranscript = () => {
    const text = transcript
      .filter((s) => s.is_final)
      .map((s) => `[${s.speaker}] ${s.text}`)
      .join("\n\n");
    downloadText(text, "transcricao-entrevista.txt");
  };

  return (
    <div
      style={{
        padding: "24px 28px",
        borderTop: "1px solid #f5f5f7",
        maxHeight: isFinal ? "none" : 300,
        overflowY: "auto",
      }}
    >
      <div
        style={{
          backgroundColor: "#fafafa",
          borderRadius: 12,
          padding: "20px 24px",
          border: "1px solid #f0f0f0",
          boxShadow: "0 1px 4px rgba(0, 0, 0, 0.03)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 16,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h2
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "#86868b",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                margin: 0,
              }}
            >
              {isFinal
                ? isInterview
                  ? "Avaliação da entrevista"
                  : "Resumo da sessão"
                : "Resumo em andamento"}
            </h2>
            {!isFinal && (
              <span
                style={{
                  fontSize: 11,
                  color: "#86868b",
                  backgroundColor: "#f5f5f7",
                  padding: "2px 8px",
                  borderRadius: 100,
                  fontWeight: 500,
                }}
              >
                atualizando
              </span>
            )}
          </div>

          {isFinal && (
            <div style={{ display: "flex", gap: 8 }}>
              {!isInterview && (
                <button
                  onClick={handleDownloadSummary}
                  style={{
                    padding: "6px 14px",
                    backgroundColor: "white",
                    color: "#007aff",
                    border: "1px solid #d2d2d7",
                    borderRadius: 100,
                    fontWeight: 500,
                    cursor: "pointer",
                    fontSize: 12,
                    transition: "all 0.2s ease",
                  }}
                >
                  Baixar resumo
                </button>
              )}
              {transcript.length > 0 && (
                <button
                  onClick={handleDownloadTranscript}
                  style={{
                    padding: "6px 14px",
                    backgroundColor: "white",
                    color: "#007aff",
                    border: "1px solid #d2d2d7",
                    borderRadius: 100,
                    fontWeight: 500,
                    cursor: "pointer",
                    fontSize: 12,
                    transition: "all 0.2s ease",
                  }}
                >
                  Baixar transcrição
                </button>
              )}
            </div>
          )}
        </div>

        <div
          style={{
            fontSize: 14,
            lineHeight: 1.6,
            color: "#424245",
          }}
        >
          <SummaryMarkdown summary={summary} />
        </div>
      </div>
    </div>
  );
}
