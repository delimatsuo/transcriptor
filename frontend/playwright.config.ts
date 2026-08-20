import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://localhost:3100",
  },
  webServer: {
    command: "npm run dev -- -p 3100",
    env: {
      NEXT_PUBLIC_AUTH_BYPASS: "1",
    },
    url: "http://localhost:3100",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
