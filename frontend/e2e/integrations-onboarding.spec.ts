import { expect, test } from "@playwright/test";

test.describe("Integrations Onboarding and Google Meet Recognition", () => {
  test("opens Conexões modal and allows switching through all 4 tabs", async ({
    page,
  }) => {
    await page.goto("http://localhost:3003");

    // 1. Verify Conexões button is visible in header and click it
    const conexoesBtn = page.getByRole("button", { name: /Conexões/i });
    await expect(conexoesBtn).toBeVisible();
    await conexoesBtn.click();

    // 2. Verify Modal title appears
    await expect(page.getByText("Conexões & Integrações")).toBeVisible();

    // Tab 1: Workable ATS
    await expect(
      page.getByRole("button", { name: "🏢 Workable" }),
    ).toBeVisible();
    await expect(page.getByText("Subdomínio da Empresa")).toBeVisible();
    await expect(
      page.getByText("Chave de Acesso da API (Partner Token)"),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/browser-control-tab1-workable.png",
    });

    // Tab 2: Calendário
    const calTab = page.getByRole("button", { name: "📅 Calendário" });
    await calTab.click();
    await expect(
      page.getByText("Endereço secreto no formato iCal (.ics)"),
    ).toBeVisible();
    await expect(
      page.getByText("Como obter seu link privado no Google Calendar:"),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/browser-control-tab2-calendar.png",
    });

    // Tab 3: Extensão Meet
    const extTab = page.getByRole("button", { name: "🧩 Extensão Meet" });
    await extTab.click();
    await expect(
      page.getByText("Como carregar a extensão no Chrome:"),
    ).toBeVisible();
    await expect(
      page.getByText("Status de Comunicação:"),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/browser-control-tab3-extension.png",
    });

    // Tab 4: Áudio & Companion
    const companionTab = page.getByRole("button", {
      name: "🎙️ Áudio & Companion",
    });
    await companionTab.click();
    await expect(
      page.getByText("Captura de Áudio & Companion Nativo"),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/browser-control-tab4-companion.png",
    });

    // Close modal via Fechar button
    const closeBtn = page.getByRole("button", { name: "Fechar" });
    await closeBtn.click();
    await expect(page.getByText("Conexões & Integrações")).not.toBeVisible();
  });

  test("prefills candidate and job from Google Meet URL launch parameters", async ({
    page,
  }) => {
    await page.goto(
      "http://localhost:3003/?candidate=Mariana%20Costa&job=Staff%20Product%20Manager&meet=xyz-uvwx-rst&open=1",
    );

    // 1. Verify session title has candidate and job
    const titleInput = page.getByPlaceholder("Título da sessão (opcional)");
    await expect(titleInput).toHaveValue(
      "Entrevista: Mariana Costa - Staff Product Manager",
    );

    // 2. Verify candidate name status badge is visible
    await expect(page.getByText("Mariana Costa")).toBeVisible();

    await page.screenshot({
      path: "test-results/browser-control-meet-prefill.png",
    });
  });
});
