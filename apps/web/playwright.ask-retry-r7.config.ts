import { defineConfig, devices } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * ASK-RETRY-CONTRACT-R7 hard e2e gates.
 * Port is chosen at runtime by ask-retry-r7-server-setup (3410–3429).
 * Tests read base URL from `.claread-r7-e2e-url` or CLAREAD_R7_BASE_URL.
 */
function r7BaseUrl(): string {
  const file = path.resolve(__dirname, ".claread-r7-e2e-url");
  try {
    const fromFile = fs.readFileSync(file, "utf8").trim();
    if (fromFile) return fromFile;
  } catch {
    // globalSetup writes the file before tests run
  }
  return process.env.CLAREAD_R7_BASE_URL ?? "http://127.0.0.1:3410";
}

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  globalSetup: "./tests/e2e/ask-retry-r7-server-setup.ts",
  testMatch: /ask-retry-submission-r7\.spec\.ts$/,
  use: {
    baseURL: r7BaseUrl(),
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium-r7",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: r7BaseUrl(),
      },
    },
  ],
});
