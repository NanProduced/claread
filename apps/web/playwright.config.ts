import { defineConfig, devices } from "@playwright/test";

const e2ePort = process.env.CLAREAD_E2E_PORT ?? "3200";
const e2eBaseUrl = `http://127.0.0.1:${e2ePort}`;

/** Canonical Web Reader Playwright runner with one isolated dev server. */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  globalSetup: "./tests/e2e/server-setup.ts",
  use: {
    baseURL: e2eBaseUrl,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: e2eBaseUrl,
      },
    },
  ],
});
