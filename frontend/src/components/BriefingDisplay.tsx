"use client";

import ReactMarkdown from "react-markdown";

interface Props {
  markdown: string;
  collapsed?: boolean;
  onToggle?: () => void;
}

export default function BriefingDisplay({
  markdown,
  collapsed = false,
  onToggle,
}: Props) {
  return (
    <div
      style={{
        border: "1px solid #e5e5ea",
        borderRadius: 12,
        backgroundColor: "#fafafa",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "12px 16px",
          backgroundColor: "#f5f5f7",
          borderBottom: collapsed ? "none" : "1px solid #e5e5ea",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: onToggle ? "pointer" : "default",
        }}
        onClick={onToggle}
      >
        <h3
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "#86868b",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            margin: 0,
          }}
        >
          Briefing Pre-Entrevista
        </h3>
        {onToggle && (
          <span
            style={{
              fontSize: 12,
              color: "#007aff",
              fontWeight: 500,
            }}
          >
            {collapsed ? "Mostrar briefing" : "Ocultar briefing"}
          </span>
        )}
      </div>

      {/* Content */}
      {!collapsed && (
        <div
          className="briefing-content"
          style={{
            padding: "16px 20px",
            fontSize: 14,
            lineHeight: 1.7,
            color: "#1d1d1f",
          }}
        >
          <ReactMarkdown>{markdown}</ReactMarkdown>
        </div>
      )}

      <style jsx global>{`
        .briefing-content h3 {
          font-size: 15px;
          font-weight: 600;
          color: #1d1d1f;
          margin: 20px 0 8px 0;
        }
        .briefing-content h3:first-child {
          margin-top: 0;
        }
        .briefing-content h4 {
          font-size: 14px;
          font-weight: 600;
          color: #3a3a3c;
          margin: 16px 0 6px 0;
        }
        .briefing-content p {
          margin: 6px 0;
        }
        .briefing-content strong {
          color: #1d1d1f;
        }
        .briefing-content ul,
        .briefing-content ol {
          padding-left: 20px;
          margin: 6px 0;
        }
        .briefing-content li {
          margin: 4px 0;
        }
      `}</style>
    </div>
  );
}
