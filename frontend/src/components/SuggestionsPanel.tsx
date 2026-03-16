"use client";

interface Props {
  questions: string[];
}

export default function SuggestionsPanel({ questions }: Props) {
  if (questions.length === 0) return null;

  return (
    <div
      style={{
        padding: 16,
        borderTop: "1px solid #e5e7eb",
        backgroundColor: "#eff6ff",
      }}
    >
      <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, color: "#1e40af" }}>
        Suggested Follow-ups
      </h2>
      <ul style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6 }}>
        {questions.map((q, i) => (
          <li key={i} style={{ fontSize: 14, lineHeight: 1.5, color: "#1e3a5f" }}>
            {q}
          </li>
        ))}
      </ul>
    </div>
  );
}
