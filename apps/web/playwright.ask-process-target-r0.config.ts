import { defineConfig, devices } from "@playwright/test";

/**
 * ASK-PROCESS-UX-TARGET-R0 — isolated scripted-SSE acceptance suite.
 *
 * The suite uses the existing spike server and runs the same public contract
 * at desktop size and at the required 390x844 viewport.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: /reader-record-ask-process-target-r0\.spec\.ts$/,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  globalSetup: "./tests/e2e/ask-activity-r2-server-setup.ts",
  use: {
    baseURL: "http://127.0.0.1:3400",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium-desktop-r0",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 720 },
      },
    },
    {
      name: "chromium-mobile-390-r0",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
      },
    },
  ],
});
