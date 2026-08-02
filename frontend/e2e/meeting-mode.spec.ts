import { expect, test } from "@playwright/test";

import { mockSession } from "./fixtures";

test("meeting mode renders transcript segments from the socket", async ({ page }) => {
  const server = await mockSession(page, "meeting");

  await page.goto("/");
  await page.getByRole("button", { name: /start session|iniciar/i }).click();

  await server.transcript("boa tarde a todos", "Entrevistador", true);

  await expect(page.getByText("boa tarde a todos")).toBeVisible();
  await expect(page.getByText("Entrevistador")).toBeVisible();
});
