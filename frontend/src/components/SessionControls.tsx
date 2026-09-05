"use client";

import { useRef, useState } from "react";
import type { SessionMode } from "@/types/ws";
import { apiFetch, authBypassEnabled } from "@/lib/auth";
import { apiUrl } from "@/lib/runtimeConfig";
import AudioDeviceSelector from "@/components/AudioDeviceSelector";
import type { UseBrowserAudioCaptureReturn } from "@/hooks/useBrowserAudioCapture";

interface Props {
  onSessionStart: (
    sessionId: string,
    mode: SessionMode,
    stopCapability?: string,
    streamKey?: string,
  ) => void;
  onSessionStop: () => Promise<void>;
  onBriefingReady?: (briefing: string) => void;
  isActive: boolean;
  sessionId: string | null;
  disabled?: boolean;
  audioCapture?: UseBrowserAudioCaptureReturn;
}

export default function SessionControls({
  onSessionStart,
  onSessionStop,
  onBriefingReady,
  isActive,
  sessionId,
  disabled = false,
  audioCapture,
}: Props) {
  const [mode, setMode] = useState<SessionMode>("interview");
  const [title, setTitle] = useState("");
  const [noticeGiven, setNoticeGiven] = useState(authBypassEnabled);
  const [loading, setLoading] = useState(false);
  const [showInterviewPrep, setShowInterviewPrep] = useState(true);
  const [candidateName, setCandidateName] = useState("");
  const [nextSteps, setNextSteps] = useState("");
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [jdText, setJdText] = useState("");
  const resumeRef = useRef<HTMLInputElement>(null);

  // Pre-interview analysis state
  const [briefing, setBriefing] = useState<string | null>(null);
  const [cvText, setCvText] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);

  // Workable ATS integration state
  const [workableInput, setWorkableInput] = useState("");
  const [workableLoading, setWorkableLoading] = useState(false);
  const [workableError, setWorkableError] = useState<string | null>(null);
  const [workableSuccess, setWorkableSuccess] = useState<string | null>(null);
  const [workableCandidateId, setWorkableCandidateId] = useState<string | null>(null);

  const handleWorkableImport = async () => {
    if (!workableInput.trim() || disabled || workableLoading) return;
    setWorkableLoading(true);
    setWorkableError(null);
    setWorkableSuccess(null);
    try {
      const res = await apiFetch(apiUrl("/api/integrations/workable/parse"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url_or_id: workableInput.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Falha ao importar dados do Workable.");
      }
      const data = await res.json();
      const dossier = data.dossier;
      if (dossier.candidate_name) {
        setCandidateName(dossier.candidate_name);
      }
      if (dossier.jd_text) {
        setJdText(dossier.jd_text);
      }
      if (dossier.cv_text) {
        setCvText(dossier.cv_text);
      }
      if (dossier.briefing_text) {
        setBriefing(dossier.briefing_text);
        onBriefingReady?.(dossier.briefing_text);
      }
      setWorkableCandidateId(dossier.candidate_id);
      setWorkableSuccess(
        `✓ Importado do Workable: ${dossier.candidate_name}${
          dossier.job_title ? ` (${dossier.job_title})` : ""
        }`
      );
    } catch (err) {
      setWorkableError(
        err instanceof Error ? err.message : "Erro ao importar do Workable."
      );
    } finally {
      setWorkableLoading(false);
    }
  };

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
    if (disabled || !jdText.trim()) return;
    setAnalyzing(true);
    setAnalyzeError(null);

    try {
      const formData = new FormData();
      formData.append("jd_text", jdText.trim());
      if (resumeFile) {
        formData.append("file", resumeFile);
      }

      const res = await apiFetch(apiUrl("/api/analyze"), {
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
    const response = await apiFetch(apiUrl(`/api/sessions/${sid}/context`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_type: docType, text }),
    });
    if (!response.ok) {
      throw new Error(`Falha ao persistir contexto: ${docType}`);
    }
  };

  const handleStart = async () => {
    if (disabled) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ mode, title, notice_given: String(noticeGiven) });
      const res = await apiFetch(apiUrl(`/api/sessions?${params}`), {
        method: "POST",
      });
      if (!res.ok) throw new Error("Falha ao iniciar sessão");
      const data = await res.json();
      const sid = data.session_id;
      // The backend starts capture with the session. Make that state visible
      // before any follow-up context request can fail.
      onSessionStart(sid, mode, data.stop_capability, data.stream_key);

      // Upload documents if provided (interview mode)
      if (mode === "interview") {
        const uploads: Promise<unknown>[] = [];

        // Upload CV file if provided
        if (resumeFile) {
          const formData = new FormData();
          formData.append("file", resumeFile);
          formData.append("doc_type", "resume");
          uploads.push((async () => {
            const response = await apiFetch(
              apiUrl(`/api/sessions/${sid}/documents`),
              {
              method: "POST",
              body: formData,
              },
            );
            if (!response.ok) throw new Error("Falha ao persistir currículo");
          })());
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

        if (nextSteps.trim()) {
          uploads.push(sendContext(sid, "next_steps", nextSteps.trim()));
        }

        if (workableCandidateId) {
          uploads.push(sendContext(sid, "workable_candidate_id", workableCandidateId));
        }

        if (uploads.length > 0) await Promise.all(uploads);
      }

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
      await onSessionStop();
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

  const canAnalyze =
    !disabled && jdText.trim().length > 0 && !analyzing && !briefing;
  const canStart = !disabled && !loading && (mode !== "interview" || noticeGiven);

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
        {authBypassEnabled ? (
          <select
            value={mode}
            onChange={(e) => handleModeChange(e.target.value as SessionMode)}
            aria-label="Modo da sessão"
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
            <option value="interview">Entrevista</option>
            <option value="meeting">Reunião</option>
          </select>
        ) : (
          <span style={{ fontSize: 13, color: "#515154" }}>Entrevista</span>
        )}
        <button
          onClick={handleStart}
          disabled={!canStart}
          style={{
            padding: "8px 22px",
            backgroundColor: "#007aff",
            color: "white",
            border: "none",
            borderRadius: 100,
            fontWeight: 500,
            cursor: canStart ? "pointer" : "default",
            fontSize: 13,
            boxShadow: "0 1px 3px rgba(0, 122, 255, 0.3)",
            transition: "all 0.2s ease",
            opacity: canStart ? 1 : 0.6,
          }}
        >
          {loading ? "Iniciando..." : "Iniciar sessão"}
        </button>
      </div>

      {/* Microphone Selection & Live Audio VU Meter */}
      {audioCapture && (
        <AudioDeviceSelector
          devices={audioCapture.devices}
          selectedDeviceId={audioCapture.selectedDeviceId}
          onSelectDevice={(id) => void audioCapture.selectDevice(id)}
          audioLevel={audioCapture.audioLevel}
          isStreaming={audioCapture.isStreaming}
          permissionState={audioCapture.permissionState}
          onRequestPermission={audioCapture.requestPermission}
        />
      )}

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

          <label
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 8,
              fontSize: 13,
              color: "#1d1d1f",
              marginBottom: 16,
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={noticeGiven}
              onChange={(e) => setNoticeGiven(e.target.checked)}
              style={{ marginTop: 2 }}
            />
            <span>
              Confirmo que o candidato foi avisado sobre a transcrição desta
              entrevista (roteiro de aviso da Ella).
            </span>
          </label>

          {/* Workable 1-Click Import */}
          <div
            style={{
              marginBottom: 16,
              padding: 14,
              backgroundColor: "#f0f7ff",
              borderRadius: 10,
              border: "1px solid #cce5ff",
            }}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                type="text"
                placeholder="Link ou ID do Candidato no Workable (ex: c12345 ou URL completa)"
                value={workableInput}
                onChange={(e) => setWorkableInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void handleWorkableImport();
                  }
                }}
                disabled={disabled || workableLoading}
                style={{
                  flex: 1,
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px solid #b8daff",
                  fontSize: 13,
                  outline: "none",
                  backgroundColor: "white",
                  color: "#1d1d1f",
                }}
              />
              <button
                type="button"
                onClick={() => void handleWorkableImport()}
                disabled={disabled || workableLoading || !workableInput.trim()}
                style={{
                  padding: "8px 16px",
                  backgroundColor: "#007aff",
                  color: "white",
                  border: "none",
                  borderRadius: 8,
                  fontSize: 13,
                  fontWeight: 500,
                  cursor: !disabled && !workableLoading && workableInput.trim() ? "pointer" : "default",
                  opacity: !disabled && !workableLoading && workableInput.trim() ? 1 : 0.6,
                  whiteSpace: "nowrap",
                }}
              >
                {workableLoading ? "Importando..." : "Importar do Workable"}
              </button>
            </div>
            {workableSuccess && (
              <p style={{ margin: "8px 0 0 0", fontSize: 12, color: "#155724" }}>
                {workableSuccess}
              </p>
            )}
            {workableError && (
              <p style={{ margin: "8px 0 0 0", fontSize: 12, color: "#721c24" }}>
                {workableError}
              </p>
            )}
          </div>

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

          <div style={{ marginBottom: 12 }}>
            <textarea
              placeholder="Próximas etapas e pessoas envolvidas (ex.: entrevista com Ana, depois avaliação técnica com João)"
              value={nextSteps}
              onChange={(event) => setNextSteps(event.target.value)}
              rows={2}
              style={{
                padding: "8px 14px",
                border: "1px solid #d2d2d7",
                borderRadius: 10,
                fontSize: 13,
                width: "100%",
                resize: "vertical",
                outline: "none",
                backgroundColor: "white",
                color: "#1d1d1f",
                boxSizing: "border-box",
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
                  disabled={disabled}
                  style={{
                    padding: "4px 12px",
                    backgroundColor: "white",
                    color: "#ff3b30",
                    border: "1px solid #ff3b30",
                    borderRadius: 100,
                    fontSize: 11,
                    cursor: disabled ? "default" : "pointer",
                    opacity: disabled ? 0.6 : 1,
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
