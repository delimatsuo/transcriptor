import { expect, test } from "@playwright/test";

const SESSION_ID = "persisted-interview-001";

const recentInterview = {
  id: SESSION_ID,
  title: "Diretoria de Produto",
  started_at: "2026-08-05T14:00:00Z",
  ended_at: "2026-08-05T15:00:00Z",
  session_status: "completed",
  review_status: "available",
};

const review = {
  session: {
    id: SESSION_ID,
    mode: "interview",
    title: "Diretoria de Produto",
    started_at: "2026-08-05T14:00:00Z",
    ended_at: "2026-08-05T15:00:00Z",
    last_active: "2026-08-05T15:00:00Z",
    status: "completed",
    notice_given: true,
    speaker_map: {},
    summary: "Avaliação final persistida",
    action_items: [],
  },
  transcript: [
    {
      id: "seg-1",
      text: "Experiência persistida após reinício",
      speaker: "Candidato",
      speaker_override: "Candidata",
      start_time: 1,
      end_time: 3,
      confidence: 0.95,
      sequence_number: 1,
      is_final: true,
    },
  ],
  summary: "Avaliação final persistida",
  review_status: "ready",
  regeneration_status: "not_needed",
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/sessions/recent-interviews", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        interviews: [
          recentInterview,
          {
            ...recentInterview,
            id: "incomplete-interview-001",
            title: "Entrevista incompleta",
            session_status: "incomplete",
            review_status: "incomplete",
          },
        ],
      }),
    });
  });

  await page.route(`**/api/sessions/${SESSION_ID}/review`, async (route) => {
    expect(route.request().method()).toBe("GET");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(review),
    });
  });
  await page.route(`**/api/sessions/${SESSION_ID}/report`, async (route) => {
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Report not found" }),
    });
  });
  await page.route("**/api/sessions/**/ws-ticket", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ticket: "synthetic-ws-ticket" }),
    });
  });
  await page.route(`**/api/sessions/${SESSION_ID}/notes`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ notes: [] }),
    });
  });
});

test("a completed interview reopens after backend and browser state are gone", async ({
  page,
}) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Entrevistas recentes" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Abrir Entrevista incompleta" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Abrir Diretoria de Produto" }).click();

  await expect(page.getByText("Experiência persistida após reinício")).toBeVisible();
  await expect(page.getByText("Candidata")).toBeVisible();
  await expect(page.getByText("Avaliação final persistida")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`review=${SESSION_ID}`));

  await page.reload();

  await expect(page.getByText("Experiência persistida após reinício")).toBeVisible();
  await expect(page.getByText("Avaliação final persistida")).toBeVisible();
});

test("missing report context blocks regeneration loudly", async ({ page }) => {
  await page.unroute(`**/api/sessions/${SESSION_ID}/review`);
  await page.route(`**/api/sessions/${SESSION_ID}/review`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...review,
        session: { ...review.session, summary: null },
        summary: null,
        review_status: "summary_unavailable",
        regeneration_status: "blocked_source_context",
      }),
    });
  });

  await page.goto(`/?review=${SESSION_ID}`);

  await expect(page.getByText(/dados de contexto necessários/i)).toBeVisible();
  await expect(page.getByText("Experiência persistida após reinício")).toBeVisible();
  await expect(page.getByText("Avaliação final persistida")).toHaveCount(0);
});

test("opening a persisted review owns the UI until its read completes", async ({
  page,
}) => {
  let apiPosts = 0;
  let webSockets = 0;

  await page.unroute(`**/api/sessions/${SESSION_ID}/review`);
  await page.route(`**/api/sessions/${SESSION_ID}/review`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(review),
    });
  });
  await page.route("**/api/**", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    apiPosts += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session_id: "unexpected-session", mode: "meeting" }),
    });
  });
  await page.routeWebSocket(/\/ws\//, (socket) => {
    webSockets += 1;
    socket.onMessage(() => {});
  });

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByPlaceholder("Cole a descrição da vaga aqui").fill("Liderança de produto");
  await page.getByRole("button", { name: "Abrir Diretoria de Produto" }).click();

  await expect(page.getByRole("button", { name: "Iniciar sessão" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Analisar Candidato" })).toBeDisabled();
  await expect(page.getByText("Experiência persistida após reinício")).toBeVisible();
  expect(apiPosts).toBe(0);
  expect(webSockets).toBe(0);
});

test("an already-starting live session wins over a stale review response", async ({
  page,
}) => {
  let sessionPosts = 0;
  let webSockets = 0;

  await page.unroute(`**/api/sessions/${SESSION_ID}/review`);
  await page.route(`**/api/sessions/${SESSION_ID}/review`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route
      .fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(review),
      })
      .catch(() => {});
  });
  await page.route("**/api/sessions?**", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    sessionPosts += 1;
    await new Promise((resolve) => setTimeout(resolve, 75));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: "live-session-wins",
        mode: "meeting",
        status: "active",
      }),
    });
  });
  await page.routeWebSocket(/\/ws\//, (socket) => {
    webSockets += 1;
    socket.onMessage(() => {});
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Iniciar sessão" }).click();
  await page
    .getByRole("button", { name: "Abrir Diretoria de Produto" })
    .evaluate((button) => (button as HTMLButtonElement).click());

  await expect(page.getByText("Gravando")).toBeVisible();
  await expect(page.getByRole("button", { name: "Encerrar sessão" })).toBeVisible();
  await expect(page.getByText("Experiência persistida após reinício")).toHaveCount(0);
  await expect(page).not.toHaveURL(/review=/);
  expect(sessionPosts).toBe(1);
  expect(webSockets).toBe(1);
});

test("deleted and corrupt reviews keep distinct visible errors", async ({ page }) => {
  await page.unroute(`**/api/sessions/${SESSION_ID}/review`);
  await page.route(`**/api/sessions/${SESSION_ID}/review`, async (route) => {
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Session not found" }),
    });
  });

  await page.goto(`/?review=${SESSION_ID}`);
  await expect(page.getByText("Esta entrevista não existe mais ou foi removida.")).toBeVisible();

  await page.unroute(`**/api/sessions/${SESSION_ID}/review`);
  await page.route(`**/api/sessions/${SESSION_ID}/review`, async (route) => {
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Persisted review is invalid" }),
    });
  });

  await page.reload();
  await expect(
    page.getByText(
      "O registro persistido desta entrevista é inválido e não pode ser reaberto.",
    ),
  ).toBeVisible();
});
