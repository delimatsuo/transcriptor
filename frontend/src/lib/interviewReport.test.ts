import assert from "node:assert/strict";
import test from "node:test";

import {
  buildReportUpdateRequest,
  isSafeClientParagraph,
  parseApprovedClientReport,
} from "./interviewReport.ts";
import type { InterviewReport } from "../types/ws.ts";

test("client prose rejects ratings, hire verdicts, and rubric markup", () => {
  assert.equal(isSafeClientParagraph("Minha leitura é positiva e recomendo avançar."), true);
  assert.equal(isSafeClientParagraph("Nota 4 para liderança."), false);
  assert.equal(isSafeClientParagraph("Liderança 4/5."), false);
  assert.equal(isSafeClientParagraph("Recomendado com Ressalvas."), false);
  assert.equal(isSafeClientParagraph("Segundo a rubrica interna, alcançou nível 5."), false);
  assert.equal(isSafeClientParagraph("Recebeu score elevado."), false);
  assert.equal(isSafeClientParagraph("Recomendo a contratação da candidata."), false);
  assert.equal(isSafeClientParagraph("A contratação é recomendada."), false);
  assert.equal(isSafeClientParagraph("- ponto em formato de rubrica"), false);
  assert.equal(isSafeClientParagraph("Foi contratada pela Empresa X para liderar produto."), true);
  assert.equal(isSafeClientParagraph("Liderou a contratação de quarenta pessoas."), true);
  assert.equal(isSafeClientParagraph("Conduziu uma aquisição aprovada pelo CADE."), true);
});

test("approved export must match the approved session and version", () => {
  const payload = {
    session_id: "session-1",
    version: 3,
    trajectory: "Construiu uma trajetória concreta em produto.",
    assessment: "Minha leitura é positiva; recomendo avançar para nova conversa.",
    approved_at: "2026-08-06T12:00:00Z",
  };
  assert.deepEqual(parseApprovedClientReport(payload, "session-1", 3), payload);
  assert.equal(parseApprovedClientReport(payload, "session-2", 3), null);
  assert.equal(
    parseApprovedClientReport({ ...payload, assessment: "Nota 5" }, "session-1", 3),
    null,
  );
});

test("update payload changes prose without accepting evidence or rating edits", () => {
  const report = {
    version: 2,
    internal_sections: [{ id: "leadership", body: "Original" }],
  } as InterviewReport;
  assert.deepEqual(
    buildReportUpdateRequest(
      report,
      { leadership: "Revisado" },
      "Trajetória revisada",
      "Avaliação revisada",
    ),
    {
      expected_version: 2,
      sections: [{ id: "leadership", body: "Revisado" }],
      client_narrative: {
        trajectory: "Trajetória revisada",
        assessment: "Avaliação revisada",
      },
    },
  );
});
