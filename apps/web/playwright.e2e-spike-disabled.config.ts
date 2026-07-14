import { defineConfig, devices } from "@playwright/test";

const disabledSpikeEnv: Record<string, string> = {};
for (const [key, value] of Object.entries(process.env)) {
  if (key !== "CLAREAD_ENABLE_E2E_SPIKE" && value !== undefined) {
    disabledSpikeEnv[key] = value;
  }
}

/**
 * Runs the public E2E-spike route gate without the enable flag. This is a
 * separate server so the normal reader E2E suite can continue to use its
 * explicitly enabled harness on port 3000.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "plate-surface-gate-disabled.spec.ts",
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:3001",
  },
  webServer: {
    command:
      "pnpm --filter=@claread/web exec next dev --hostname 127.0.0.1 --port 3001",
    env: disabledSpikeEnv,
    url: "http://127.0.0.1:3001",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});