"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { SuggestionEntry } from "@/types/ws";
import BriefingDisplay from "@/components/BriefingDisplay";

const ALLOWED_ELEMENTS = [
  "p", "strong", "em", "h3", "h4", "ul", "ol", "li", "code", "br", "hr",
];

interface Props {
  suggestionHistory: SuggestionEntry[];
  briefing?: string;
  isInterview?: boolean;
}

function formatRelativeTime(timestamp: number): string {
  const now = Date.now();
  const diff = Math.floor((now - timestamp) / 1000);

  if (diff < 10) return "Just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

export default function SuggestionsPanel({
  suggestionHistory,
  briefing = "",
  isInterview = false,
}: Props) {
  const [briefingCollapsed, setBriefingCollapsed] = useState(false);

  if (!isInterview && suggestionHistory.length === 0) return null;

  const hasBriefing = briefing.length > 0;
  const hasSuggestions = suggestionHistory.length > 0;

  // Auto-collapse briefing when suggestions start arriving
  const effectiveCollapsed =
    hasBriefing && hasSuggestions ? briefingCollapsed : false;

  // Show newest first
  const reversed = [...suggestionHistory].reverse();

  return (
    <div
      style={{
        padding: "24px 28px",
        height: "100%",
        overflowY: "auto",
        display: "flex",
        flexDirection: "column",
        gap: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 20,
        }}
      >
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
          Interview Assistant
        </h2>
        {isInterview && hasSuggestions && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 500,
              color: "#34c759",
              backgroundColor: "rgba(52, 199, 89, 0.1)",
              padding: "3px 10px",
              borderRadius: 100,
            }}
          >
            Live
          </span>
        )}
      </div>

      {/* Briefing section */}
      {hasBriefing && (
        <div style={{ marginBottom: hasSuggestions ? 16 : 0 }}>
          <BriefingDisplay
            markdown={briefing}
            collapsed={hasSuggestions ? effectiveCollapsed : false}
            onToggle={
              hasSuggestions
                ? () => setBriefingCollapsed((c) => !c)
                : undefined
            }
          />
        </div>
      )}

      {/* Suggestions or placeholder */}
      {!hasSuggestions && !hasBriefing && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            flex: 1,
            gap: 12,
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 20,
              backgroundColor: "#f5f5f7",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              animation: "subtlePulse 2.5s ease-in-out infinite",
            }}
          >
            <span style={{ fontSize: 18 }}>&#9834;</span>
          </div>
          <p
            style={{
              color: "#86868b",
              fontSize: 14,
              textAlign: "center",
              lineHeight: 1.5,
              margin: 0,
            }}
          >
            Listening to the conversation...
          </p>
          <p
            style={{
              color: "#aeaeb2",
              fontSize: 12,
              textAlign: "center",
              margin: 0,
            }}
          >
            Suggestions will appear as the interview progresses
          </p>
        </div>
      )}

      {hasSuggestions && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {reversed.map((entry, batchIdx) => (
            <div
              key={entry.timestamp}
              style={{
                opacity: batchIdx === 0 ? 1 : 0.7,
                transition: "opacity 0.3s ease",
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  color: "#aeaeb2",
                  marginBottom: 8,
                  fontWeight: 500,
                }}
              >
                {formatRelativeTime(entry.timestamp)}
              </div>
              {entry.markdown ? (
                <div
                  className="suggestion-markdown"
                  style={{
                    padding: "12px 16px",
                    backgroundColor: batchIdx === 0 ? "#f5f5f7" : "#fafafa",
                    borderRadius: 12,
                    fontSize: 14,
                    lineHeight: 1.6,
                    color: "#1d1d1f",
                    border: "none",
                    transition: "background-color 0.2s ease",
                  }}
                >
                  <ReactMarkdown allowedElements={ALLOWED_ELEMENTS}>
                    {entry.markdown}
                  </ReactMarkdown>
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {entry.questions.map((q, i) => (
                    <div
                      key={i}
                      style={{
                        padding: "12px 16px",
                        backgroundColor: batchIdx === 0 ? "#f5f5f7" : "#fafafa",
                        borderRadius: 12,
                        fontSize: 14,
                        lineHeight: 1.6,
                        color: "#1d1d1f",
                        border: "none",
                        transition: "background-color 0.2s ease",
                      }}
                    >
                      {q}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
