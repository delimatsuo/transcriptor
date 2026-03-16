"use client";

interface Props {
  summary: string;
  isFinal: boolean;
}

export default function SummaryPanel({ summary, isFinal }: Props) {
  if (!summary) return null;

  return (
    <div
      style={{
        padding: 16,
        borderTop: "1px solid #e5e7eb",
        maxHeight: 300,
        overflowY: "auto",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
        }}
      >
        <h2 style={{ fontSize: 18, fontWeight: 600 }}>
          {isFinal ? "Final Summary" : "Rolling Summary"}
        </h2>
        {!isFinal && (
          <span
            style={{
              fontSize: 11,
              color: "#6b7280",
              backgroundColor: "#f3f4f6",
              padding: "2px 6px",
              borderRadius: 4,
            }}
          >
            live
          </span>
        )}
      </div>
      <div
        style={{
          fontSize: 14,
          lineHeight: 1.6,
          whiteSpace: "pre-wrap",
          color: "#374151",
        }}
      >
        {summary}
      </div>
    </div>
  );
}
