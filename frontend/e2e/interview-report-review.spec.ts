import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { InterviewReport } from "../src/types/ws";

const SESSION_ID = "approved-report-session-001";

const transcript = [
  {
    id: "seg-1",
    text: "Liderei uma equipe de quarenta pessoas.",
    speaker: "Candidato",
    speaker_override: "Candidata",
    start_time: 1,
    end_time: 3,
    confidence: 0.95,
    sequence_number: 1,
    is_final: true,
  },
];

const review = {
  session: {
    id: SESSION_ID,
    mode: "interview",
    title: "Diretoria de Produto",
    started_at: "2026-08-06T14:00:00Z",
    ended_at: "2026-08-06T15:00:00Z",
    last_active: "2026-08-06T15:00:00Z",
    status: "completed",
    notice_given: true,
    speaker_map: {},
    summary: "## Rascunho gerado por IA\n\nConteúdo interno legado.",
    action_items: [],
  },
  transcript,
  summary: "## Rascunho gerado por IA\n\nConteúdo interno legado.",
  review_status: "ready",
  regeneration_status: "not_needed",
};

function draftReport() {
  return {
    session_id: SESSION_ID,
    version: 1,
    status: "draft",
    ai_draft_label: "Rascunho gerado por IA",
    internal_sections: [
      {
        id: "leadership",
        title: "Liderança",
        body: "Construiu uma equipe de alto desempenho.",
        rating: 4,
        evidence: [{ source: "transcript", evidence_id: "seg-1" }],
      },
      {
        id: "risks",
        title: "Pontos de atenção",
        body: "A escala internacional precisa ser aprofundada.",
        rating: null,
        evidence: [{ source: "recruiter_note", evidence_id: "note-1" }],
      },
    ],
    client_narrative: {
      trajectory: "Marina construiu uma trajetória de quinze anos em produto.",
      assessment: "Minha leitura é positiva; recomendo avançar para conversa com Ana.",
      trajectory_evidence: [{ source: "context", evidence_id: "resume" }],
      assessment_evidence: [
        { source: "transcript", evidence_id: "seg-1" },
        { source: "recruiter_note", evidence_id: "note-1" },
      ],
    },
    created_at: "2026-08-06T15:01:00Z",
    updated_at: "2026-08-06T15:01:00Z",
    approved_version: null,
    approved_at: null,
  };
}

async function mockReviewBase(page: Page) {
  await page.route("**/api/sessions/recent-interviews", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ interviews: [] }),
    });
  });
  await page.route(`**/api/sessions/${SESSION_ID}/review`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(review),
    });
  });
  await page.route(`**/api/sessions/${SESSION_ID}/notes`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        notes: [
          {
            id: "note-1",
            session_id: SESSION_ID,
            kind: "concern",
            text: "",
            transcript_segment_id: "seg-1",
            transcript_offset_ms: 3000,
            source: "recruiter",
            created_at: "2026-08-06T14:30:00Z",
          },
        ],
      }),
    });
  });
}

test("only an explicitly approved version reaches the two-paragraph print surface", async ({
  page,
}) => {
  await mockReviewBase(page);
  let report = draftReport() as InterviewReport;
  const updates: Array<Record<string, unknown>> = [];
  const approvals: Array<Record<string, unknown>> = [];
  let exportReads = 0;

  await page.route(`**/api/sessions/${SESSION_ID}/report`, async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      updates.push(body);
      const client = body.client_narrative as {
        trajectory: string;
        assessment: string;
      };
      report = {
        ...report,
        version: 2,
        updated_at: "2026-08-06T15:03:00Z",
        internal_sections: report.internal_sections.map((section, index) => ({
          ...section,
          body: (body.sections as Array<{ body: string }>)[index].body,
        })),
        client_narrative: {
          ...report.client_narrative,
          trajectory: client.trajectory,
          assessment: client.assessment,
        },
      };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(report),
    });
  });
  await page.route(`**/api/sessions/${SESSION_ID}/report/approve`, async (route) => {
    approvals.push(route.request().postDataJSON() as Record<string, unknown>);
    report = {
      ...report,
      status: "approved",
      approved_version: report.version,
      approved_at: "2026-08-06T15:04:00Z",
      updated_at: "2026-08-06T15:04:00Z",
    };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(report),
    });
  });
  await page.route(
    `**/api/sessions/${SESSION_ID}/report/client-export`,
    async (route) => {
      exportReads += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: SESSION_ID,
          version: report.version,
          trajectory: report.client_narrative.trajectory,
          assessment: report.client_narrative.assessment,
          approved_at: report.approved_at,
        }),
      });
    },
  );

  await page.goto(`/?review=${SESSION_ID}`);

  await expect(page.getByText("Rascunho gerado por IA · uso interno")).toBeVisible();
  await expect(page.getByText("Nota interna 4/5")).toBeVisible();
  await expect(page.getByText("Currículo", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Currículo" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Exportar PDF" })).toHaveCount(0);
  await expect(page.getByTestId("approved-client-print")).toHaveCount(0);
  expect(exportReads).toBe(0);

  await page.getByRole("textbox", { name: "Trajetória", exact: true }).fill(
    "Marina construiu uma trajetória de quinze anos em produto e tecnologia.",
  );
  await expect(page.getByRole("button", { name: "Aprovar relatório" })).toBeDisabled();
  await page.getByRole("button", { name: "Salvar alterações" }).click();
  await expect(page.getByText("Alterações salvas na versão 2.")).toBeVisible();

  expect(updates).toHaveLength(1);
  expect(Object.keys(updates[0]).sort()).toEqual([
    "client_narrative",
    "expected_version",
    "sections",
  ]);
  expect(JSON.stringify(updates[0])).not.toContain("rating");
  expect(JSON.stringify(updates[0])).not.toContain("evidence");

  await page.getByRole("button", { name: "Aprovar relatório" }).click();
  await expect(page.getByText("Versão 2 aprovada e bloqueada para edição.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Exportar PDF" })).toBeVisible();
  expect(approvals).toEqual([{ expected_version: 2 }]);
  expect(exportReads).toBe(1);

  const printSurface = page.getByTestId("approved-client-print");
  await expect(printSurface.locator("p")).toHaveCount(2);
  await expect(printSurface).not.toContainText("Liderança");
  await expect(printSurface).not.toContainText("4/5");

  await page.emulateMedia({ media: "print" });
  expect(
    await page.getByText("Nota interna 4/5").evaluate(
      (element) => getComputedStyle(element).visibility,
    ),
  ).toBe("hidden");
  expect(
    await printSurface.evaluate((element) => getComputedStyle(element).visibility),
  ).toBe("visible");
  const renderedPdf = await page.pdf({ format: "A4", printBackground: true });
  expect(renderedPdf.subarray(0, 4).toString()).toBe("%PDF");
  expect(renderedPdf.toString("latin1").match(/\/Type\s*\/Page\b/g)).toHaveLength(1);
  await page.emulateMedia({ media: "screen" });

  await page.evaluate(() => {
    window.print = () => {
      document.body.dataset.printInvoked = "true";
    };
  });
  await page.getByRole("button", { name: "Exportar PDF" }).click();
  await expect(page.locator("body")).toHaveAttribute("data-print-invoked", "true");
});

test("a slow report remains pending until its durable success is available", async ({
  page,
}) => {
  await mockReviewBase(page);
  let reads = 0;
  await page.route(`**/api/sessions/${SESSION_ID}/report`, async (route) => {
    reads += 1;
    if (reads < 3) {
      await route.fulfill({
        status: 425,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Relatório ainda em geração" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(draftReport()),
    });
  });

  await page.goto(`/?review=${SESSION_ID}`);

  await expect(page.getByRole("button", { name: "Aprovar relatório" })).toBeVisible();
  expect(reads).toBe(3);
});

test("a persisted generation failure is visible and legacy AI text has no export", async ({
  page,
}) => {
  await mockReviewBase(page);
  await page.route(`**/api/sessions/${SESSION_ID}/report`, async (route) => {
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "A geração do relatório falhou e não será repetida automaticamente.",
      }),
    });
  });

  await page.goto(`/?review=${SESSION_ID}`);

  await expect(
    page.getByText("A geração do relatório falhou e não será repetida automaticamente."),
  ).toBeVisible();
  await expect(page.getByText("Conteúdo interno legado.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Aprovar relatório" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Exportar PDF" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Baixar relatório" })).toHaveCount(0);
});
