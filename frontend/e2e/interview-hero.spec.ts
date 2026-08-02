import { expect, test } from "@playwright/test";

import { mockSession } from "./fixtures";

test("a suggestion from the socket becomes the hero question", async ({ page }) => {
  const server = await mockSession(page, "interview");

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("button", { name: /iniciar sessão/i }).click();

  await expect(page.getByText(/ouvindo a conversa/i)).toBeVisible();

  await server.suggestion(["Como você estruturou essa equipe?"]);

  await expect(page.getByText("Como você estruturou essa equipe?")).toBeVisible();
  await expect(page.getByText(/próxima pergunta/i)).toBeVisible();
});

test("a newer batch marks the hero stale and fills the queue", async ({ page }) => {
  const server = await mockSession(page, "interview");

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("button", { name: /iniciar sessão/i }).click();

  await server.suggestion(["Primeira pergunta"]);
  await expect(page.getByText("Primeira pergunta")).toBeVisible();

  await server.suggestion(["Segunda pergunta"]);

  await expect(page.getByText(/conversa avançou/i)).toBeVisible();
  await expect(page.getByText(/1 na fila/i)).toBeVisible();
  // The first question must NOT be replaced — it holds until dismissed.
  await expect(page.getByText("Primeira pergunta")).toBeVisible();
});

test("dismissing advances to the queued question", async ({ page }) => {
  const server = await mockSession(page, "interview");

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("button", { name: /iniciar sessão/i }).click();

  await server.suggestion(["Primeira pergunta"]);
  await server.suggestion(["Segunda pergunta"]);
  await expect(page.getByText("Primeira pergunta")).toBeVisible();

  await page.getByRole("button", { name: /^próxima$/i }).click();

  await expect(page.getByText("Segunda pergunta")).toBeVisible();
  await expect(page.getByText("Primeira pergunta")).toHaveCount(0);
});

test("the transcript sheet opens over the hero", async ({ page }) => {
  const server = await mockSession(page, "interview");

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("button", { name: /iniciar sessão/i }).click();

  await server.transcript("eu liderei essa transformação", "Candidato", true);

  // Collapsed by default — transcript text is not on screen.
  await expect(page.getByText("eu liderei essa transformação")).toHaveCount(0);

  await page.getByRole("button", { name: /transcrição/i }).click();

  await expect(page.getByText("eu liderei essa transformação")).toBeVisible();
});

test("opening the transcript sheet jumps to the latest speech, not the oldest", async ({
  page,
}) => {
  const server = await mockSession(page, "interview");

  await page.goto("/");
  await page.getByRole("combobox").selectOption("interview");
  await page.getByRole("button", { name: /iniciar sessão/i }).click();

  for (let i = 1; i <= 40; i += 1) {
    await server.transcript(`linha número ${i}`, "Entrevistador", true);
  }

  await page.getByRole("button", { name: /transcrição/i }).click();

  await expect(page.getByText("linha número 40")).toBeInViewport();
});
