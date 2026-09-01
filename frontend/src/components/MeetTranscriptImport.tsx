"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/auth";
import {
  MAX_MEET_IMPORT_BYTES,
  MeetTranscriptImportError,
  buildMeetTranscriptSyncRequest,
  parseMeetImportFixture,
  parseMeetImportResult,
  parseMeetTranscriptAutomationResult,
  validateMeetImportFile,
} from "@/lib/meetTranscriptImport";
import { apiUrl } from "@/lib/runtimeConfig";

interface Props {
  onOpenReview: (sessionId: string) => void | Promise<void>;
}
export default function MeetTranscriptImport({ onOpenReview }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [grantId, setGrantId] = useState("");
  const [calendarId, setCalendarId] = useState("");
  const [calendarEventId, setCalendarEventId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function importFixture() {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    setMessage("Validando fixture local…");
    try {
      validateMeetImportFile(file.name, file.size);
      if (file.size > MAX_MEET_IMPORT_BYTES) {
        throw new MeetTranscriptImportError("O fixture deve ter no máximo 2 MB.");
      }
      const payload = parseMeetImportFixture(await file.text(), file.name);
      setMessage("Importando transcrição offline…");
      const response = await apiFetch(apiUrl("/api/transcript-imports/google-meet"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new MeetTranscriptImportError(
          response.status === 409
            ? "Este artefato conflita com uma importação anterior."
            : response.status === 422
              ? "O fixture foi rejeitado por estar malformado ou fora dos limites."
              : "Não foi possível importar o fixture.",
        );
      }
      const result = parseMeetImportResult(await response.json());
      setMessage(`Importação ${result.status}: ${result.segment_count} segmentos.`);
      if (result.status === "completed") {
        await onOpenReview(result.session_id);
      }
    } catch (caught) {
      setMessage(null);
      setError(
        caught instanceof Error ? caught.message : "Não foi possível importar o fixture.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function syncEligibleEvent() {
    if (busy) return;
    setBusy(true);
    setError(null);
    setMessage("Validando identidade exata do evento elegível…");
    try {
      const payload = buildMeetTranscriptSyncRequest({
        grantId,
        calendarId,
        calendarEventId,
      });
      setMessage("Sincronizando transcrição elegível…");
      const response = await apiFetch(
        apiUrl("/api/workspace/meet-transcripts/sync"),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      if (!response.ok) {
        throw new MeetTranscriptImportError(
          response.status === 404
            ? "O evento elegível ou a autorização Workspace não foi encontrado."
            : response.status === 409
              ? "A sincronização já está em andamento ou conflita com o evento armazenado."
              : response.status === 422
                ? "A sincronização foi rejeitada por identidade ou conteúdo inválido."
                : response.status === 503
                  ? "O adaptador Workspace sintético/offline não está configurado."
                  : "Não foi possível sincronizar o evento elegível.",
        );
      }
      const result = parseMeetTranscriptAutomationResult(await response.json());
      setMessage(
        result.automation_replay
          ? `Sincronização já concluída: ${result.segment_count} segmentos.`
          : `Sincronização ${result.status}: ${result.segment_count} segmentos.`,
      );
      if (result.status === "completed") {
        await onOpenReview(result.session_id);
      }
    } catch (caught) {
      setMessage(null);
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível sincronizar o evento elegível.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="meet-import-title"
      style={{
        width: "100%",
        maxWidth: 560,
        padding: 16,
        border: "1px solid #e5e5e7",
        borderRadius: 12,
        marginTop: 16,
      }}
    >
      <h3 id="meet-import-title" style={{ margin: 0, fontSize: 15 }}>
        Importar transcrição do Google Meet
      </h3>
      <p style={{ color: "#6e6e73", fontSize: 13, lineHeight: 1.4 }}>
        Fluxo sintético e somente offline. Selecione um fixture JSON local; o T.A.R.S.
        não acessa o Google, não inicia áudio e não abre WebSocket.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          type="file"
          accept=".json,application/json"
          disabled={busy}
          onChange={(event) => {
            setFile(event.currentTarget.files?.[0] ?? null);
            setError(null);
            setMessage(null);
          }}
        />
        <button type="button" disabled={!file || busy} onClick={() => void importFixture()}>
          {busy ? "Importando…" : "Importar fixture"}
        </button>
      </div>
      <div
        style={{
          display: "grid",
          gap: 8,
          marginTop: 18,
          paddingTop: 16,
          borderTop: "1px solid #e5e5e7",
        }}
      >
        <p style={{ margin: 0, color: "#6e6e73", fontSize: 13, lineHeight: 1.4 }}>
          Sincronização sintética/offline de um evento já elegível. Indisponível sem um
          adaptador Workspace injetado; este fluxo não conecta contas Google.
        </p>
        <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
          ID da autorização Workspace
          <input
            type="text"
            value={grantId}
            disabled={busy}
            autoComplete="off"
            onChange={(event) => setGrantId(event.currentTarget.value)}
          />
        </label>
        <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
          ID do calendário
          <input
            type="text"
            value={calendarId}
            disabled={busy}
            autoComplete="off"
            onChange={(event) => setCalendarId(event.currentTarget.value)}
          />
        </label>
        <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
          ID do evento do calendário
          <input
            type="text"
            value={calendarEventId}
            disabled={busy}
            autoComplete="off"
            onChange={(event) => setCalendarEventId(event.currentTarget.value)}
          />
        </label>
        <button type="button" disabled={busy} onClick={() => void syncEligibleEvent()}>
          {busy ? "Sincronizando…" : "Sincronizar evento elegível"}
        </button>
      </div>
      {message && <p role="status" style={{ fontSize: 13 }}>{message}</p>}
      {error && <p role="alert" style={{ color: "#b42318", fontSize: 13 }}>{error}</p>}
    </section>
  );
}
