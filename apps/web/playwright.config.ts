import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry",
  },
  webServer: {
    command: "pnpm --filter=@claread/web dev",
    env: {
      ...process.env,
      CLAREAD_PHONE_AUTH_PROVIDER: "mock",
      // T4.2a-PUX-R4-R2-S2-P1 — enable the /e2e-plate-spike harness route
      // for Playwright runs. Without this, the route returns 404 and the
      // spike E2E tests cannot reach the mounted Plate editor.
      CLAREAD_ENABLE_E2E_SPIKE: "1",
    },
    url: "http://127.0.0.1:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
