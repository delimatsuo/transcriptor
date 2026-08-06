import { expect, test } from "@playwright/test";

import { mockSession, SESSION_ID } from "./fixtures";

async function startInterview(page: Parameters<typeof mockSession>[0]) {
  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("checkbox", { name: /candidato foi avisado/i }).check();
  await page.getByRole("button", { name: /iniciar sessão/i }).click();
}

test("wordless note chips persist only against the latest final segment", async ({
  page,
}) => {
  const server = await mockSession(page, "interview");
  const requests: Array<Record<string, unknown>> = [];

  await page.route(`**/api/sessions/${SESSION_ID}/notes`, async (route) => {
    expect(route.request().method()).toBe("POST");
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: body.client_note_id,
        session_id: SESSION_ID,
        kind: body.kind,
        text: "",
        transcript_segment_id: body.transcript_segment_id,
        transcript_offset_ms: 3000,
        source: "recruiter",
        created_at: "2026-08-05T20:00:00Z",
      }),
    });
  });

  await startInterview(page);

  await expect(page.getByRole("button", { name: "Ponto forte" })).toBeDisabled();
  await server.transcript("resposta parcial", "Candidato", false);
  await expect(page.getByRole("button", { name: "Ponto forte" })).toBeDisabled();

  await server.transcript("resultado confirmado", "Candidato", true);
  await expect(page.getByRole("button", { name: "Ponto forte" })).toBeEnabled();
  await page.getByRole("button", { name: "Ponto forte" }).click();

  await expect(page.getByText("Ponto forte salvo em 00:03")).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({
    kind: "strength",
    transcript_segment_id: "seg-2",
  });
  expect(Object.keys(requests[0]).sort()).toEqual([
    "client_note_id",
    "kind",
    "transcript_segment_id",
  ]);
});

test("a visible retry preserves the original note identity and evidence anchor", async ({
  page,
}) => {
  const server = await mockSession(page, "interview");
  const requests: Array<Record<string, unknown>> = [];

  await page.route(`**/api/sessions/${SESSION_ID}/notes`, async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    if (requests.length === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Firestore unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: body.client_note_id,
        session_id: SESSION_ID,
        kind: body.kind,
        text: "",
        transcript_segment_id: body.transcript_segment_id,
        transcript_offset_ms: 2000,
        source: "recruiter",
        created_at: "2026-08-05T20:00:00Z",
      }),
    });
  });

  await startInterview(page);
  await server.transcript("primeira evidência", "Candidato", true);
  await page.getByRole("button", { name: "Preocupação" }).click();
  await expect(page.getByText("Não foi possível salvar.")).toBeVisible();

  await server.transcript("evidência mais nova", "Candidato", true);
  await page.getByRole("button", { name: "Tentar novamente" }).click();

  await expect(page.getByText("Preocupação salvo em 00:02")).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  expect(requests[1].transcript_segment_id).toBe("seg-1");
});
