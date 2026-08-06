"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import {
  buildReportUpdateRequest,
  formatReportOffset,
  parseApprovedClientReport,
} from "@/lib/interviewReport";
import type {
  ApprovedClientReport,
  EvidenceReference,
  InterviewReport,
  RecruiterNote,
  TranscriptSegment,
} from "@/types/ws";
import { apiFetch } from "@/lib/auth";

const API_BASE = "http://localhost:8000";
const REPORT_POLL_INTERVAL_MS = 750;
const REPORT_POLL_ATTEMPTS = 120;
const LEGACY_ALLOWED_ELEMENTS = [
  "p", "strong", "em", "h2", "h3", "h4", "ul", "ol", "li", "br", "hr",
];
const NOTE_LABELS: Record<string, string> = {
  bookmark: "Marcar",
  concern: "Preocupação",
  strength: "Ponto forte",
  follow_up: "Retomar",
  note: "Nota",
};
const CONTEXT_LABELS: Record<string, string> = {
  resume: "Currículo",
  jd: "Descrição da vaga",
  briefing: "Briefing",
  candidate_name: "Nome informado",
  next_steps: "Próximas etapas informadas",
};

interface Props {
  sessionId: string;
  summary: string;
  isSummaryFinal: boolean;
  transcript: TranscriptSegment[];
}

function downloadText(content: string, filename: string) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function readReport(value: unknown, sessionId: string): InterviewReport | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<InterviewReport>;
  if (
    candidate.session_id !== sessionId ||
    !Number.isInteger(candidate.version) ||
    (candidate.version ?? 0) < 1 ||
    !["draft", "approved"].includes(candidate.status ?? "") ||
    candidate.ai_draft_label !== "Rascunho gerado por IA" ||
    !Array.isArray(candidate.internal_sections) ||
    !candidate.client_narrative
  ) {
    return null;
  }
  return candidate as InterviewReport;
}

export default function InterviewReportReview({
  sessionId,
  summary,
  isSummaryFinal,
  transcript,
}: Props) {
  const [report, setReport] = useState<InterviewReport | null>(null);
  const [notes, setNotes] = useState<RecruiterNote[]>([]);
  const [sectionBodies, setSectionBodies] = useState<Record<string, string>>({});
  const [trajectory, setTrajectory] = useState("");
  const [assessment, setAssessment] = useState("");
  const [clientExport, setClientExport] = useState<ApprovedClientReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestTokenRef = useRef(0);

  const adoptReport = (next: InterviewReport) => {
    setReport(next);
    setSectionBodies(
      Object.fromEntries(next.internal_sections.map((section) => [section.id, section.body])),
    );
    setTrajectory(next.client_narrative.trajectory);
    setAssessment(next.client_narrative.assessment);
    setDirty(false);
  };

  useEffect(() => {
    const controller = new AbortController();
    const token = requestTokenRef.current + 1;
    requestTokenRef.current = token;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const current = () =>
      !controller.signal.aborted && token === requestTokenRef.current;

    const loadApprovedExport = async (next: InterviewReport) => {
      const response = await apiFetch(
        `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/report/client-export`,
        { signal: controller.signal },
      );
      if (!response.ok) throw new Error("A versão aprovada não pôde ser carregada.");
      const parsed = parseApprovedClientReport(
        await response.json(),
        sessionId,
        next.version,
      );
      if (!parsed) throw new Error("A versão aprovada recebida é inválida.");
      if (current()) setClientExport(parsed);
    };

    const loadReport = async (attempt = 0): Promise<void> => {
      try {
        const response = await apiFetch(
          `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/report`,
          { signal: controller.signal },
        );
        if (response.ok) {
          const parsed = readReport(await response.json(), sessionId);
          if (!parsed) throw new Error("O relatório persistido é inválido.");
          if (!current()) return;
          adoptReport(parsed);
          setLoading(false);
          if (parsed.status === "approved") await loadApprovedExport(parsed);
          return;
        }
        const pending =
          response.status === 425 ||
          (response.status === 404 && !isSummaryFinal);
        if (pending && attempt < REPORT_POLL_ATTEMPTS) {
          retryTimer = setTimeout(
            () => void loadReport(attempt + 1),
            REPORT_POLL_INTERVAL_MS,
          );
          return;
        }
        if (pending) {
          if (current()) {
            setLoading(false);
            setError(
              "A geração do relatório excedeu o tempo esperado. Recarregue para verificar o estado persistido.",
            );
          }
          return;
        }
        if (response.status === 409) {
          const body = (await response.json().catch(() => null)) as { detail?: string } | null;
          if (current()) {
            setLoading(false);
            setError(
              body?.detail ??
                "A geração do relatório falhou e não será repetida automaticamente.",
            );
          }
          return;
        }
        if (!current()) return;
        setLoading(false);
      } catch (loadError) {
        if (!current()) return;
        setLoading(false);
        if (summary) return;
        setError(
          loadError instanceof Error
            ? loadError.message
            : "Não foi possível carregar o relatório.",
        );
      }
    };

    const loadNotes = async () => {
      try {
        const response = await apiFetch(
          `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/notes`,
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error();
        const body = (await response.json()) as { notes?: RecruiterNote[] };
        if (current() && Array.isArray(body.notes)) setNotes(body.notes);
      } catch {
        if (current()) setError("Não foi possível carregar as notas da recrutadora.");
      }
    };

    setLoading(true);
    setError(null);
    setNotice(null);
    setClientExport(null);
    void Promise.all([loadReport(), loadNotes()]);

    return () => {
      requestTokenRef.current += 1;
      controller.abort();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [isSummaryFinal, sessionId, summary]);

  const save = async () => {
    if (!report || report.status !== "draft" || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await apiFetch(
        `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/report`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            buildReportUpdateRequest(
              report,
              sectionBodies,
              trajectory,
              assessment,
            ),
          ),
        },
      );
      if (!response.ok) throw new Error("Não foi possível salvar as alterações.");
      const parsed = readReport(await response.json(), sessionId);
      if (!parsed) throw new Error("A confirmação de salvamento é inválida.");
      adoptReport(parsed);
      setNotice(`Alterações salvas na versão ${parsed.version}.`);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Falha ao salvar.");
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!report || report.status !== "draft" || dirty || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await apiFetch(
        `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/report/approve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expected_version: report.version }),
        },
      );
      if (!response.ok) throw new Error("Não foi possível aprovar esta versão.");
      const approved = readReport(await response.json(), sessionId);
      if (!approved || approved.status !== "approved") {
        throw new Error("A confirmação de aprovação é inválida.");
      }
      const exportResponse = await apiFetch(
        `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/report/client-export`,
      );
      if (!exportResponse.ok) throw new Error("A versão aprovada não pôde ser carregada.");
      const approvedExport = parseApprovedClientReport(
        await exportResponse.json(),
        sessionId,
        approved.version,
      );
      if (!approvedExport) throw new Error("A versão aprovada recebida é inválida.");
      adoptReport(approved);
      setClientExport(approvedExport);
      setNotice(`Versão ${approved.version} aprovada e bloqueada para edição.`);
    } catch (approvalError) {
      setError(
        approvalError instanceof Error ? approvalError.message : "Falha ao aprovar.",
      );
    } finally {
      setBusy(false);
    }
  };

  const jumpToEvidence = (evidence: EvidenceReference) => {
    let segmentId = evidence.source === "transcript" ? evidence.evidence_id : null;
    if (evidence.source === "recruiter_note") {
      segmentId = notes.find((note) => note.id === evidence.evidence_id)
        ?.transcript_segment_id ?? null;
    }
    if (segmentId) {
      document.getElementById(`transcript-segment-${segmentId}`)?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }
  };

  const evidenceLabel = (evidence: EvidenceReference) => {
    if (evidence.source === "context") {
      return CONTEXT_LABELS[evidence.evidence_id] ?? `Contexto ${evidence.evidence_id}`;
    }
    if (evidence.source === "recruiter_note") {
      const note = notes.find((item) => item.id === evidence.evidence_id);
      return note
        ? `${NOTE_LABELS[note.kind] ?? note.kind} · ${formatReportOffset(note.transcript_offset_ms)}`
        : `Nota ${evidence.evidence_id}`;
    }
    const segment = transcript.find((item) => item.id === evidence.evidence_id);
    return segment
      ? `Trecho ${formatReportOffset(segment.end_time * 1000)} · ${segment.speaker_override ?? segment.speaker}`
      : `Trecho ${evidence.evidence_id}`;
  };

  const evidenceChip = (evidence: EvidenceReference) => {
    const key = `${evidence.source}:${evidence.evidence_id}`;
    const style = {
      fontSize: 11,
      borderRadius: 100,
      border: "1px solid #d2d2d7",
      background: "white",
      padding: "2px 7px",
    };
    if (evidence.source === "context") {
      return (
        <span key={key} style={style} title="Fonte de contexto persistida para esta entrevista">
          {evidenceLabel(evidence)}
        </span>
      );
    }
    return (
      <button
        type="button"
        key={key}
        onClick={() => jumpToEvidence(evidence)}
        style={style}
      >
        {evidenceLabel(evidence)}
      </button>
    );
  };

  const downloadTranscript = () => {
    const text = transcript
      .filter((segment) => segment.is_final)
      .map(
        (segment) =>
          `[${formatReportOffset(segment.end_time * 1000)}] ` +
          `${segment.speaker_override ?? segment.speaker}: ${segment.text}`,
      )
      .join("\n\n");
    downloadText(text, `transcricao-${sessionId}.txt`);
  };

  const downloadNotes = () => {
    const text = notes
      .map(
        (note) =>
          `[${formatReportOffset(note.transcript_offset_ms)}] ` +
          `${NOTE_LABELS[note.kind] ?? note.kind} · trecho ${note.transcript_segment_id}`,
      )
      .join("\n");
    downloadText(text, `notas-${sessionId}.txt`);
  };

  const controls = (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <button type="button" onClick={downloadTranscript}>Baixar transcrição</button>
      {notes.length > 0 && (
        <button type="button" onClick={downloadNotes}>Baixar notas</button>
      )}
    </div>
  );

  if (loading && !summary) {
    return <div style={{ padding: "24px 28px", color: "#86868b" }}>Preparando relatório…</div>;
  }

  if (!report) {
    return (
      <section style={{ padding: "24px 28px", borderTop: "1px solid #f0f0f0" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
          <div>
            <strong style={{ color: "#8a4b00" }}>Rascunho gerado por IA · uso interno</strong>
            <p style={{ color: "#86868b", fontSize: 13 }}>
              Este registro legado não possui o relatório estruturado e não pode ser exportado para cliente.
            </p>
          </div>
          {controls}
        </div>
        {error && <p role="alert" style={{ color: "#ff3b30" }}>{error}</p>}
        {summary && (
          <div style={{ padding: 20, borderRadius: 12, background: "#fafafa" }}>
            <ReactMarkdown allowedElements={LEGACY_ALLOWED_ELEMENTS}>{summary}</ReactMarkdown>
          </div>
        )}
      </section>
    );
  }

  const editable = report.status === "draft";
  const markDirty = () => {
    setDirty(true);
    setNotice(null);
  };

  return (
    <section style={{ padding: "24px 28px", borderTop: "1px solid #f0f0f0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
        <div>
          <strong style={{ color: "#8a4b00" }}>{report.ai_draft_label} · uso interno</strong>
          <div style={{ color: "#86868b", fontSize: 12, marginTop: 4 }}>
            Versão {report.version} · {editable ? "aguardando aprovação humana" : "aprovada e imutável"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {controls}
          {editable && (
            <>
              <button type="button" onClick={() => void save()} disabled={!dirty || busy}>
                Salvar alterações
              </button>
              <button type="button" onClick={() => void approve()} disabled={dirty || busy}>
                Aprovar relatório
              </button>
            </>
          )}
          {clientExport && (
            <button type="button" onClick={() => window.print()}>
              Exportar PDF
            </button>
          )}
        </div>
      </div>

      {error && <p role="alert" style={{ color: "#ff3b30" }}>{error}</p>}
      {notice && <p aria-live="polite" style={{ color: "#248a3d" }}>{notice}</p>}
      {dirty && (
        <p style={{ color: "#8a4b00", fontSize: 13 }}>
          Salve as alterações antes de aprovar esta versão.
        </p>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(220px, 1fr)", gap: 20, marginTop: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {report.internal_sections.map((section) => (
            <article key={section.id} style={{ border: "1px solid #e5e5ea", borderRadius: 12, padding: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                <h3 style={{ margin: 0, fontSize: 15 }}>{section.title}</h3>
                {section.rating !== null && (
                  <span style={{ color: "#8a4b00", fontSize: 12 }}>
                    Nota interna {section.rating}/5
                  </span>
                )}
              </div>
              <textarea
                aria-label={`Editar ${section.title}`}
                value={sectionBodies[section.id] ?? section.body}
                disabled={!editable}
                onChange={(event) => {
                  setSectionBodies((current) => ({ ...current, [section.id]: event.target.value }));
                  markDirty();
                }}
                rows={5}
                style={{ width: "100%", marginTop: 12, resize: "vertical", lineHeight: 1.5 }}
              />
              <div aria-label={`Evidências de ${section.title}`} style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
                {section.evidence.map(evidenceChip)}
              </div>
            </article>
          ))}

          <article style={{ border: "1px solid #d2d2d7", borderRadius: 12, padding: 16 }}>
            <h3 style={{ margin: "0 0 4px", fontSize: 15 }}>Texto para o cliente</h3>
            <p style={{ margin: "0 0 12px", color: "#86868b", fontSize: 12 }}>
              Dois blocos narrativos. Ratings e rubrica nunca entram na exportação.
            </p>
            <label style={{ display: "block", fontSize: 13, fontWeight: 600 }}>
              Trajetória
              <textarea
                value={trajectory}
                disabled={!editable}
                onChange={(event) => { setTrajectory(event.target.value); markDirty(); }}
                rows={8}
                style={{ width: "100%", marginTop: 6, resize: "vertical", lineHeight: 1.55 }}
              />
            </label>
            <div aria-label="Evidências da trajetória" style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {report.client_narrative.trajectory_evidence.map(evidenceChip)}
            </div>
            <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginTop: 14 }}>
              Avaliação
              <textarea
                value={assessment}
                disabled={!editable}
                onChange={(event) => { setAssessment(event.target.value); markDirty(); }}
                rows={8}
                style={{ width: "100%", marginTop: 6, resize: "vertical", lineHeight: 1.55 }}
              />
            </label>
            <div aria-label="Evidências da avaliação" style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {report.client_narrative.assessment_evidence.map(evidenceChip)}
            </div>
          </article>
        </div>

        <aside aria-label="Notas da recrutadora" style={{ border: "1px solid #e5e5ea", borderRadius: 12, padding: 16, alignSelf: "start" }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 14 }}>Notas da recrutadora</h3>
          {notes.length === 0 ? (
            <p style={{ color: "#86868b", fontSize: 13 }}>Nenhuma nota registrada.</p>
          ) : notes.map((note) => (
            <button
              type="button"
              key={note.id}
              onClick={() => jumpToEvidence({ source: "recruiter_note", evidence_id: note.id })}
              style={{ display: "block", width: "100%", textAlign: "left", marginBottom: 8, padding: 10, borderRadius: 10, border: "1px solid #f0f0f0", background: "#fafafa" }}
            >
              <strong>{NOTE_LABELS[note.kind] ?? note.kind}</strong>
              <span style={{ display: "block", color: "#86868b", fontSize: 11, marginTop: 3 }}>
                {formatReportOffset(note.transcript_offset_ms)} · trecho {note.transcript_segment_id}
              </span>
            </button>
          ))}
        </aside>
      </div>

      {clientExport && (
        <div className="client-report-print" data-testid="approved-client-print" aria-hidden="true">
          <p>{clientExport.trajectory}</p>
          <p>{clientExport.assessment}</p>
        </div>
      )}
    </section>
  );
}
