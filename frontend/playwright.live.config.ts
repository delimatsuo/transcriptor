import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /integrations-onboarding\.spec\.ts/,
  timeout: 30_000,
  use: {
    baseURL: "http://localhost:3003",
    screenshot: "on",
  },
});
