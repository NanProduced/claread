import { defineConfig, devices } from "@playwright/test";

/** Canonical Web Reader Playwright runner with one isolated dev server. */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  globalSetup: "./tests/e2e/server-setup.ts",
  use: {
    baseURL: "http://127.0.0.1:3200",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: "http://127.0.0.1:3200",
      },
    },
  ],
});
