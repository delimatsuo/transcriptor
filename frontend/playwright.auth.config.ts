import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /auth-source-readiness\.spec\.ts/,
  timeout: 30_000,
  globalTimeout: 120_000,
  use: {
    baseURL: "http://127.0.0.1:3105",
    serviceWorkers: "block",
  },
  webServer: {
    command: "./node_modules/.bin/next dev --webpack --hostname 127.0.0.1 -p 3105",
    env: {
      NODE_ENV: "development",
      NEXT_PUBLIC_AUTH_BYPASS: "0",
      NEXT_PUBLIC_FIREBASE_API_KEY: "",
      NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "",
      NEXT_PUBLIC_FIREBASE_PROJECT_ID: "",
      NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "",
      NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "",
      NEXT_PUBLIC_FIREBASE_APP_ID: "",
      NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
      NEXT_PUBLIC_WS_URL: "ws://127.0.0.1:8000/ws",
      NEXT_PUBLIC_WS_STREAM_URL: "ws://127.0.0.1:8000/api/stream/native",
      NEXT_TELEMETRY_DISABLED: "1",
    },
    url: "http://127.0.0.1:3105",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
