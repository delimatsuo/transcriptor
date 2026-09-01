"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/auth";
import {
  MAX_MEET_IMPORT_BYTES,
  MeetTranscriptImportError,
  parseMeetImportFixture,
  parseMeetImportResult,
  validateMeetImportFile,
} from "@/lib/meetTranscriptImport";
import { apiUrl } from "@/lib/runtimeConfig";

interface Props {
  onOpenReview: (sessionId: string) => void | Promise<void>;
}
export default function MeetTranscriptImport({ onOpenReview }: Props) {
  const [file, setFile] = useState<File | null>(null);
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
      {message && <p role="status" style={{ fontSize: 13 }}>{message}</p>}
      {error && <p role="alert" style={{ color: "#b42318", fontSize: 13 }}>{error}</p>}
    </section>
  );
}
