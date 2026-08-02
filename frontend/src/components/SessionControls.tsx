"use client";

import { useRef, useState } from "react";
import type { SessionMode } from "@/types/ws";

const API_BASE = "http://localhost:8000";

interface Props {
  onSessionStart: (sessionId: string, mode: SessionMode) => void;
  onSessionStop: () => void;
  onBriefingReady?: (briefing: string) => void;
  isActive: boolean;
  sessionId: string | null;
}

export default function SessionControls({
  onSessionStart,
  onSessionStop,
  onBriefingReady,
  isActive,
  sessionId,
}: Props) {
  const [mode, setMode] = useState<SessionMode>("meeting");
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [showInterviewPrep, setShowInterviewPrep] = useState(false);
  const [candidateName, setCandidateName] = useState("");
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [jdText, setJdText] = useState("");
  const resumeRef = useRef<HTMLInputElement>(null);

  // Pre-interview analysis state
  const [briefing, setBriefing] = useState<string | null>(null);
  const [cvText, setCvText] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);

  const resetBriefing = () => {
    setBriefing(null);
    setCvText(null);
    setAnalyzeError(null);
    onBriefingReady?.("");
  };

  const handleResumeChange = (file: File | null) => {
    setResumeFile(file);
    if (briefing) resetBriefing();
  };

  const handleJdChange = (text: string) => {
    setJdText(text);
    if (briefing) resetBriefing();
  };

  const handleAnalyze = async () => {
    if (!jdText.trim()) return;
    setAnalyzing(true);
    setAnalyzeError(null);

    try {
      const formData = new FormData();
      formData.append("jd_text", jdText.trim());
      if (resumeFile) {
        formData.append("file", resumeFile);
      }

      const res = await fetch(`${API_BASE}/api/analyze`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Falha na análise");
      }

      const data = await res.json();
      setBriefing(data.briefing_markdown);
      setCvText(data.cv_text || null);
      onBriefingReady?.(data.briefing_markdown);
    } catch (err) {
      setAnalyzeError(
        err instanceof Error ? err.message : "Falha na análise",
      );
    } finally {
      setAnalyzing(false);
    }
  };

  const sendContext = async (
    sid: string,
    docType: string,
    text: string,
  ) => {
    await fetch(`${API_BASE}/api/sessions/${sid}/context`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_type: docType, text }),
    });
  };

  const handleStart = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ mode, title });
      const res = await fetch(`${API_BASE}/api/sessions?${params}`, {
        method: "POST",
      });
      const data = await res.json();
      const sid = data.session_id;

      // Upload documents if provided (interview mode)
      if (mode === "interview") {
        const uploads: Promise<unknown>[] = [];

        // Upload CV file if provided
        if (resumeFile) {
          const formData = new FormData();
          formData.append("file", resumeFile);
          formData.append("doc_type", "resume");
          uploads.push(
            fetch(`${API_BASE}/api/sessions/${sid}/documents`, {
              method: "POST",
              body: formData,
            }),
          );
        } else if (cvText) {
          // Use pre-extracted CV text from analysis
          uploads.push(sendContext(sid, "resume", cvText));
        }

        if (jdText.trim()) {
          uploads.push(sendContext(sid, "jd", jdText.trim()));
        }

        if (briefing) {
          uploads.push(sendContext(sid, "briefing", briefing));
        }

        if (candidateName.trim()) {
          uploads.push(sendContext(sid, "candidate_name", candidateName.trim()));
        }

        if (uploads.length > 0) await Promise.all(uploads);
      }

      onSessionStart(sid, mode);
      setShowInterviewPrep(false);
    } catch (err) {
      console.error("Failed to start session:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    setLoading(true);
    try {
      onSessionStop();
    } finally {
      setLoading(false);
    }
  };

  const handleModeChange = (newMode: SessionMode) => {
    setMode(newMode);
    setShowInterviewPrep(newMode === "interview");
  };

  if (isActive) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            color: "#ff3b30",
            fontWeight: 500,
            fontSize: 13,
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              backgroundColor: "#ff3b30",
              animation: "pulse 1.5s infinite",
            }}
          />
          Gravando
        </span>
        <button
          onClick={handleStop}
          disabled={loading}
          style={{
            padding: "8px 20px",
            backgroundColor: "#ff3b30",
            color: "white",
            border: "none",
            borderRadius: 100,
            fontWeight: 500,
            cursor: loading ? "default" : "pointer",
            fontSize: 13,
            boxShadow: "0 1px 3px rgba(255, 59, 48, 0.3)",
            transition: "all 0.2s ease",
            opacity: loading ? 0.6 : 1,
          }}
        >
          Encerrar sessão
        </button>
      </div>
    );
  }

  const canAnalyze = jdText.trim().length > 0 && !analyzing && !briefing;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Top row: controls */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <input
          type="text"
          placeholder="Título da sessão (opcional)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          style={{
            padding: "8px 14px",
            border: "1px solid #d2d2d7",
            borderRadius: 10,
            fontSize: 13,
            width: 200,
            outline: "none",
            backgroundColor: "#fafafa",
            color: "#1d1d1f",
            transition: "border-color 0.2s ease, box-shadow 0.2s ease",
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = "#007aff";
            e.currentTarget.style.boxShadow =
              "0 0 0 3px rgba(0, 122, 255, 0.12)";
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = "#d2d2d7";
            e.currentTarget.style.boxShadow = "none";
          }}
        />
        <select
          value={mode}
          onChange={(e) => handleModeChange(e.target.value as SessionMode)}
          style={{
            padding: "8px 14px",
            border: "1px solid #d2d2d7",
            borderRadius: 10,
            fontSize: 13,
            outline: "none",
            backgroundColor: "#fafafa",
            color: "#1d1d1f",
            cursor: "pointer",
          }}
        >
          <option value="meeting">Reunião</option>
          <option value="interview">Entrevista</option>
        </select>
        <button
          onClick={handleStart}
          disabled={loading}
          style={{
            padding: "8px 22px",
            backgroundColor: "#007aff",
            color: "white",
            border: "none",
            borderRadius: 100,
            fontWeight: 500,
            cursor: loading ? "default" : "pointer",
            fontSize: 13,
            boxShadow: "0 1px 3px rgba(0, 122, 255, 0.3)",
            transition: "all 0.2s ease",
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? "Iniciando..." : "Iniciar sessão"}
        </button>
      </div>

      {/* Interview prep panel */}
      {showInterviewPrep && (
        <div
          style={{
            padding: 20,
            border: "1px solid #e5e5ea",
            borderRadius: 12,
            backgroundColor: "#fafafa",
            boxShadow: "0 1px 4px rgba(0, 0, 0, 0.04)",
          }}
        >
          <h3
            style={{
              fontSize: 14,
              fontWeight: 600,
              marginBottom: 4,
              color: "#1d1d1f",
              margin: "0 0 4px 0",
            }}
          >
            Preparação da entrevista
          </h3>
          <p
            style={{
              fontSize: 12,
              color: "#86868b",
              margin: "0 0 16px 0",
            }}
          >
            Envie o currículo, cole a descrição da vaga e analise antes de começar
          </p>

          {/* Candidate name */}
          <div style={{ marginBottom: 12 }}>
            <input
              type="text"
              placeholder="Nome do candidato"
              value={candidateName}
              onChange={(e) => setCandidateName(e.target.value)}
              style={{
                padding: "8px 14px",
                border: "1px solid #d2d2d7",
                borderRadius: 10,
                fontSize: 13,
                width: 300,
                outline: "none",
                backgroundColor: "white",
                color: "#1d1d1f",
                transition: "border-color 0.2s ease, box-shadow 0.2s ease",
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = "#007aff";
                e.currentTarget.style.boxShadow =
                  "0 0 0 3px rgba(0, 122, 255, 0.12)";
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = "#d2d2d7";
                e.currentTarget.style.boxShadow = "none";
              }}
            />
          </div>

          <div
            style={{
              display: "flex",
              gap: 12,
              alignItems: "flex-start",
            }}
          >
            <div>
              <input
                ref={resumeRef}
                type="file"
                accept=".pdf,.docx,.txt"
                onChange={(e) =>
                  handleResumeChange(e.target.files?.[0] || null)
                }
                style={{ display: "none" }}
              />
              <button
                onClick={() => resumeRef.current?.click()}
                style={{
                  padding: "8px 16px",
                  border: "1px solid #d2d2d7",
                  borderRadius: 10,
                  backgroundColor: resumeFile ? "#e8e8ed" : "white",
                  cursor: "pointer",
                  fontSize: 13,
                  color: "#1d1d1f",
                  transition: "all 0.2s ease",
                  boxShadow: "0 1px 2px rgba(0, 0, 0, 0.04)",
                }}
              >
                {resumeFile ? `CV: ${resumeFile.name}` : "Enviar currículo"}
              </button>
            </div>

            <div style={{ flex: 1 }}>
              <textarea
                placeholder="Cole a descrição da vaga aqui"
                value={jdText}
                onChange={(e) => handleJdChange(e.target.value)}
                rows={3}
                style={{
                  width: "100%",
                  padding: "10px 14px",
                  border: "1px solid #d2d2d7",
                  borderRadius: 10,
                  backgroundColor: "white",
                  fontSize: 13,
                  resize: "vertical",
                  fontFamily: "inherit",
                  outline: "none",
                  color: "#1d1d1f",
                  lineHeight: 1.5,
                  transition: "border-color 0.2s ease, box-shadow 0.2s ease",
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = "#007aff";
                  e.currentTarget.style.boxShadow =
                    "0 0 0 3px rgba(0, 122, 255, 0.12)";
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = "#d2d2d7";
                  e.currentTarget.style.boxShadow = "none";
                }}
              />
            </div>
          </div>

          {/* Analyze button */}
          <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 12 }}>
            <button
              onClick={handleAnalyze}
              disabled={!canAnalyze}
              style={{
                padding: "8px 20px",
                backgroundColor: canAnalyze ? "#34c759" : "#e8e8ed",
                color: canAnalyze ? "white" : "#aeaeb2",
                border: "none",
                borderRadius: 100,
                fontWeight: 500,
                cursor: canAnalyze ? "pointer" : "default",
                fontSize: 13,
                boxShadow: canAnalyze
                  ? "0 1px 3px rgba(52, 199, 89, 0.3)"
                  : "none",
                transition: "all 0.2s ease",
              }}
            >
              {analyzing
                ? "Analisando..."
                : briefing
                  ? "Análise concluída"
                  : "Analisar Candidato"}
            </button>

            {analyzeError && (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 12, color: "#ff3b30" }}>
                  {analyzeError}
                </span>
                <button
                  onClick={handleAnalyze}
                  style={{
                    padding: "4px 12px",
                    backgroundColor: "white",
                    color: "#ff3b30",
                    border: "1px solid #ff3b30",
                    borderRadius: 100,
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  Tentar novamente
                </button>
              </div>
            )}

            {briefing && (
              <span style={{ fontSize: 12, color: "#34c759", fontWeight: 500 }}>
                Briefing pronto — veja no painel ao lado
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
