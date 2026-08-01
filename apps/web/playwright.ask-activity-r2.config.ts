import { defineConfig, devices } from "@playwright/test";

/**
 * Isolated Playwright config for the Ask Claread lifecycle, CoT, history,
 * streaming, and composer-selection acceptance suites. Port 3400 does not
 * touch the shared 3200/3201 spike servers.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  globalSetup: "./tests/e2e/ask-activity-r2-server-setup.ts",
  testMatch:
    /(reader-record-ask-agentic-activity-r2|reader-record-ask-process-target-r0|ask-chain-of-thought|ask-ux-history-cold-load|ask-ux-streaming-delta-r2|ask-composer-focus-anchors|ask-ux-mobile-r3-floating-overlay)\.spec\.ts$/,
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
