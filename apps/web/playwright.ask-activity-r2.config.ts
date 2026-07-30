import { defineConfig, devices } from "@playwright/test";

/**
 * Isolated Playwright config for R2.5 Agentic Ask Activity acceptance and
 * the ASK-COT (B1) Chain of Thought acceptance. Ports 3400/3401 — does
 * not touch shared 3200/3201 spike servers.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  globalSetup: "./tests/e2e/ask-activity-r2-server-setup.ts",
  testMatch: /(reader-record-ask-agentic-activity-r2|ask-chain-of-thought)\.spec\.ts$/,
  use: {
    baseURL: "http://127.0.0.1:3400",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium-spike-enabled",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: "http://127.0.0.1:3400",
      },
    },
  ],
});
