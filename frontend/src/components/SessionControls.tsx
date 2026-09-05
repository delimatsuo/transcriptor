"use client";

import { useEffect, useRef, useState } from "react";
import type { SessionMode } from "@/types/ws";
import { apiFetch, authBypassEnabled } from "@/lib/auth";
import { apiUrl } from "@/lib/runtimeConfig";
import AudioDeviceSelector from "@/components/AudioDeviceSelector";
import type { UseBrowserAudioCaptureReturn } from "@/hooks/useBrowserAudioCapture";

interface ScheduledInterviewItem {
  id: string;
  title: string;
  starts_at: string;
  ends_at?: string | null;
  source: string;
  candidate_id?: string | null;
  candidate_name?: string | null;
  job_shortcode?: string | null;
  job_title?: string | null;
  conference_url?: string | null;
  interviewers: string[];
}

interface WorkableJobOption {
  title: string;
  shortcode: string;
  department?: string | null;
  location?: string | null;
}

interface WorkableCandidateOption {
  id: string;
  name: string;
  headline?: string | null;
  stage?: string | null;
  email?: string | null;
}

function formatTimeBadge(isoString: string): string {
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    const isTomorrow =
      new Date(now.getTime() + 86400000).toDateString() === d.toDateString();
    const timeStr = d.toLocaleTimeString("pt-BR", {
      hour: "2-digit",
      minute: "2-digit",
    });
    if (isToday) return `Hoje às ${timeStr}`;
    if (isTomorrow) return `Amanhã às ${timeStr}`;
    return `${d.toLocaleDateString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
    })} às ${timeStr}`;
  } catch {
    return isoString;
  }
}

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

  // Workable & Calendar automated scheduling state
  const [scheduledInterviews, setScheduledInterviews] = useState<
    ScheduledInterviewItem[]
  >([]);
  const [loadingSchedule, setLoadingSchedule] = useState(false);
  const [jobs, setJobs] = useState<WorkableJobOption[]>([]);
  const [selectedJob, setSelectedJob] = useState("");
  const [jobCandidates, setJobCandidates] = useState<WorkableCandidateOption[]>(
    [],
  );
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [showManualForm, setShowManualForm] = useState(false);

  // Workable ATS integration state
  const [workableInput, setWorkableInput] = useState("");
  const [workableLoading, setWorkableLoading] = useState(false);
  const [workableError, setWorkableError] = useState<string | null>(null);
  const [workableSuccess, setWorkableSuccess] = useState<string | null>(null);
  const [workableCandidateId, setWorkableCandidateId] = useState<string | null>(
    null,
  );

  const handleWorkableImport = async (overrideInput?: string) => {
    const targetInput = (overrideInput ?? workableInput).trim();
    if (!targetInput || disabled || workableLoading) return;
    setWorkableLoading(true);
    setWorkableError(null);
    setWorkableSuccess(null);
    try {
      const res = await apiFetch(apiUrl("/api/integrations/workable/parse"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url_or_id: targetInput }),
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
      setTitle(
        `Entrevista: ${dossier.candidate_name}${
          dossier.job_title ? ` - ${dossier.job_title}` : ""
        }`,
      );
      setWorkableSuccess(
        `✓ Dados carregados: ${dossier.candidate_name}${
          dossier.job_title ? ` (${dossier.job_title})` : ""
        }`,
      );
    } catch (err) {
      setWorkableError(
        err instanceof Error ? err.message : "Erro ao importar do Workable.",
      );
    } finally {
      setWorkableLoading(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    async function loadScheduleAndJobs() {
      setLoadingSchedule(true);
      try {
        const [calRes, jobsRes] = await Promise.all([
          apiFetch(apiUrl("/api/calendar/upcoming"))
            .then((r) => (r.ok ? r.json() : { interviews: [] }))
            .catch(() => ({ interviews: [] })),
          apiFetch(apiUrl("/api/integrations/workable/jobs"))
            .then((r) => (r.ok ? r.json() : { jobs: [] }))
            .catch(() => ({ jobs: [] })),
        ]);
        if (mounted) {
          if (Array.isArray(calRes.interviews)) {
            setScheduledInterviews(calRes.interviews);
          }
          if (Array.isArray(jobsRes.jobs)) {
            setJobs(jobsRes.jobs);
          }
        }
      } finally {
        if (mounted) setLoadingSchedule(false);
      }
    }
    void loadScheduleAndJobs();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const params = new URLSearchParams(window.location.search);
      const candidateParam = params.get("candidate");
      const jobParam = params.get("job");
      if (candidateParam) {
        setCandidateName(candidateParam);
        setTitle(
          `Entrevista: ${candidateParam}${jobParam ? ` - ${jobParam}` : ""}`,
        );
        setMode("interview");
        setShowInterviewPrep(true);
      }
      if (jobParam) {
        setJdText((prev) => prev || `Vaga: ${jobParam}`);
      }
    } catch {
      // Ignore URL parsing errors
    }
  }, []);

  const handleJobSelect = async (shortcode: string) => {
    setSelectedJob(shortcode);
    setSelectedCandidateId("");
    if (!shortcode) {
      setJobCandidates([]);
      return;
    }
    setLoadingCandidates(true);
    try {
      const res = await apiFetch(
        apiUrl(`/api/integrations/workable/jobs/${shortcode}/candidates`),
      );
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.candidates)) {
          setJobCandidates(data.candidates);
        }
      }
    } catch {
      setJobCandidates([]);
    } finally {
      setLoadingCandidates(false);
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

          {/* 1. Scheduled Interviews (Workable & Calendar) */}
          {scheduledInterviews.length > 0 && (
            <div style={{ marginBottom: 18 }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 8,
                }}
              >
                <h4
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: "#1d1d1f",
                    margin: 0,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <span>📅</span> Entrevistas Agendadas (Workable & Calendário)
                </h4>
                {loadingSchedule && (
                  <span style={{ fontSize: 11, color: "#86868b" }}>
                    Atualizando...
                  </span>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {scheduledInterviews.slice(0, 6).map((item) => (
                  <div
                    key={item.id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "10px 14px",
                      backgroundColor: "white",
                      border: "1px solid #e5e5ea",
                      borderRadius: 10,
                      boxShadow: "0 1px 2px rgba(0, 0, 0, 0.03)",
                    }}
                  >
                    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          flexWrap: "wrap",
                        }}
                      >
                        <span
                          style={{
                            fontSize: 11,
                            fontWeight: 600,
                            color: "#007aff",
                            backgroundColor: "#eaf4ff",
                            padding: "2px 8px",
                            borderRadius: 4,
                          }}
                        >
                          {formatTimeBadge(item.starts_at)}
                        </span>
                        <span
                          style={{
                            fontSize: 13,
                            fontWeight: 600,
                            color: "#1d1d1f",
                          }}
                        >
                          {item.candidate_name || item.title}
                        </span>
                        {item.job_title && (
                          <span style={{ fontSize: 12, color: "#6e6e73" }}>
                            • {item.job_title}
                          </span>
                        )}
                      </div>
                      {item.conference_url && (
                        <a
                          href={item.conference_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{
                            fontSize: 11,
                            color: "#007aff",
                            textDecoration: "none",
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                          }}
                        >
                          <span>📹</span> Abrir chamada de vídeo
                        </a>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        if (item.candidate_id) {
                          void handleWorkableImport(item.candidate_id);
                        } else {
                          // Calendar event fallback: prefill candidate name and title
                          if (item.candidate_name) {
                            setCandidateName(item.candidate_name);
                          } else if (item.title) {
                            setCandidateName(item.title);
                          }
                          if (item.title) {
                            setTitle(item.title);
                          }
                          setShowManualForm(true);
                        }
                      }}
                      disabled={disabled || workableLoading}
                      style={{
                        padding: "6px 14px",
                        backgroundColor:
                          Boolean(workableCandidateId) &&
                          workableCandidateId === item.candidate_id
                            ? "#34c759"
                            : "#007aff",
                        color: "white",
                        border: "none",
                        borderRadius: 8,
                        fontSize: 12,
                        fontWeight: 500,
                        cursor:
                          disabled || workableLoading ? "default" : "pointer",
                        whiteSpace: "nowrap",
                        opacity: disabled || workableLoading ? 0.6 : 1,
                      }}
                    >
                      {Boolean(workableCandidateId) &&
                      workableCandidateId === item.candidate_id
                        ? "✓ Carregado"
                        : "Carregar Entrevista"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 2. Workable Candidate & Job Selector */}
          <div
            style={{
              marginBottom: 16,
              padding: 14,
              backgroundColor: "#f0f7ff",
              borderRadius: 10,
              border: "1px solid #cce5ff",
            }}
          >
            <h4
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "#004085",
                margin: "0 0 10px 0",
              }}
            >
              🔍 Selecionar Candidato do Workable
            </h4>

            {/* Vaga e Candidato Dropdowns */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 10,
                marginBottom: 10,
              }}
            >
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 11,
                    color: "#495057",
                    marginBottom: 4,
                    fontWeight: 500,
                  }}
                >
                  Vaga ({jobs.length})
                </label>
                <select
                  value={selectedJob}
                  onChange={(e) => void handleJobSelect(e.target.value)}
                  disabled={disabled || workableLoading || jobs.length === 0}
                  style={{
                    width: "100%",
                    padding: "7px 10px",
                    borderRadius: 8,
                    border: "1px solid #b8daff",
                    fontSize: 12,
                    backgroundColor: "white",
                    color: "#1d1d1f",
                  }}
                >
                  <option value="">
                    {jobs.length === 0
                      ? "Nenhuma vaga carregada"
                      : "-- Selecione a vaga --"}
                  </option>
                  {jobs.map((j) => (
                    <option key={j.shortcode} value={j.shortcode}>
                      {j.title}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 11,
                    color: "#495057",
                    marginBottom: 4,
                    fontWeight: 500,
                  }}
                >
                  Candidato ({jobCandidates.length})
                </label>
                <select
                  value={selectedCandidateId}
                  onChange={(e) => {
                    setSelectedCandidateId(e.target.value);
                    if (e.target.value) {
                      void handleWorkableImport(e.target.value);
                    }
                  }}
                  disabled={
                    disabled ||
                    workableLoading ||
                    !selectedJob ||
                    loadingCandidates ||
                    jobCandidates.length === 0
                  }
                  style={{
                    width: "100%",
                    padding: "7px 10px",
                    borderRadius: 8,
                    border: "1px solid #b8daff",
                    fontSize: 12,
                    backgroundColor: "white",
                    color: "#1d1d1f",
                  }}
                >
                  <option value="">
                    {loadingCandidates
                      ? "Carregando candidatos..."
                      : !selectedJob
                        ? "-- Escolha a vaga primeiro --"
                        : jobCandidates.length === 0
                          ? "Nenhum candidato nesta vaga"
                          : "-- Selecione o candidato --"}
                  </option>
                  {jobCandidates.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} {c.stage ? `[${c.stage}]` : ""}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Direct URL / ID Input */}
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                type="text"
                placeholder="Ou informe o link/ID direto do candidato (ex: c12345 ou URL do Workable)"
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
                  padding: "7px 10px",
                  borderRadius: 6,
                  border: "1px solid #b8daff",
                  fontSize: 12,
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
                  padding: "7px 14px",
                  backgroundColor: "#007aff",
                  color: "white",
                  border: "none",
                  borderRadius: 6,
                  fontSize: 12,
                  fontWeight: 500,
                  cursor:
                    !disabled && !workableLoading && workableInput.trim()
                      ? "pointer"
                      : "default",
                  opacity:
                    !disabled && !workableLoading && workableInput.trim()
                      ? 1
                      : 0.6,
                  whiteSpace: "nowrap",
                }}
              >
                {workableLoading ? "Importando..." : "Importar"}
              </button>
            </div>

            {workableSuccess && (
              <p
                style={{
                  margin: "8px 0 0 0",
                  fontSize: 12,
                  color: "#155724",
                  fontWeight: 500,
                }}
              >
                {workableSuccess}
              </p>
            )}
            {workableError && (
              <p
                style={{
                  margin: "8px 0 0 0",
                  fontSize: 12,
                  color: "#721c24",
                }}
              >
                {workableError}
              </p>
            )}
          </div>

          {/* 3. Loaded Candidate Status Badge */}
          {candidateName && (
            <div
              style={{
                marginBottom: 16,
                padding: "12px 16px",
                backgroundColor: "#eafaf1",
                border: "1px solid #b7eb8f",
                borderRadius: 10,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <span
                    style={{
                      fontSize: 14,
                      fontWeight: 600,
                      color: "#1d1d1f",
                    }}
                  >
                    {candidateName}
                  </span>
                  <div
                    style={{
                      display: "flex",
                      gap: 14,
                      fontSize: 12,
                      color: "#27ae60",
                      marginTop: 4,
                      flexWrap: "wrap",
                    }}
                  >
                    <span>✓ Currículo & Histórico Carregados</span>
                    {jdText && <span>✓ Descrição da Vaga</span>}
                    {briefing && <span>✓ Briefing e Notas Anteriores</span>}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setCandidateName("");
                    setWorkableCandidateId(null);
                    setWorkableSuccess(null);
                    resetBriefing();
                  }}
                  style={{
                    padding: "4px 8px",
                    fontSize: 11,
                    color: "#8c8c8c",
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  Limpar
                </button>
              </div>
            </div>
          )}

          {/* 4. Collapsible Manual Fallback Form */}
          <div style={{ marginTop: 8, paddingTop: 10, borderTop: "1px solid #e5e5ea" }}>
            <button
              type="button"
              onClick={() => setShowManualForm((prev) => !prev)}
              style={{
                background: "none",
                border: "none",
                color: "#007aff",
                fontSize: 12,
                cursor: "pointer",
                padding: "4px 0",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <span>{showManualForm ? "▼" : "▶"}</span>
              <span>
                {showManualForm
                  ? "Ocultar preenchimento manual"
                  : "+ Preenchimento manual de currículo e vaga (sem Workable)"}
              </span>
            </button>

            {showManualForm && (
              <div
                style={{
                  marginTop: 12,
                  display: "flex",
                  flexDirection: "column",
                  gap: 12,
                  padding: 12,
                  backgroundColor: "#f9f9fb",
                  borderRadius: 8,
                  border: "1px dashed #d2d2d7",
                }}
              >
                <div>
                  <input
                    type="text"
                    placeholder="Nome do candidato"
                    value={candidateName}
                    onChange={(e) => setCandidateName(e.target.value)}
                    style={{
                      padding: "8px 14px",
                      border: "1px solid #d2d2d7",
                      borderRadius: 8,
                      fontSize: 13,
                      width: 280,
                      outline: "none",
                      backgroundColor: "white",
                      color: "#1d1d1f",
                    }}
                  />
                </div>

                <div>
                  <textarea
                    placeholder="Próximas etapas e pessoas envolvidas (ex.: entrevista com Ana, depois avaliação técnica com João)"
                    value={nextSteps}
                    onChange={(event) => setNextSteps(event.target.value)}
                    rows={2}
                    style={{
                      padding: "8px 14px",
                      border: "1px solid #d2d2d7",
                      borderRadius: 8,
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

                <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
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
                      type="button"
                      onClick={() => resumeRef.current?.click()}
                      style={{
                        padding: "8px 16px",
                        border: "1px solid #d2d2d7",
                        borderRadius: 8,
                        backgroundColor: resumeFile ? "#e8e8ed" : "white",
                        cursor: "pointer",
                        fontSize: 13,
                        color: "#1d1d1f",
                      }}
                    >
                      {resumeFile
                        ? `CV: ${resumeFile.name}`
                        : "Enviar arquivo de currículo"}
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
                        borderRadius: 8,
                        backgroundColor: "white",
                        fontSize: 13,
                        resize: "vertical",
                        outline: "none",
                        color: "#1d1d1f",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>
                </div>
              </div>
            )}
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
