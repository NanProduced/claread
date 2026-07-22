import { defineConfig, devices } from "@playwright/test";

/**
 * T4.2a-PUX-R4-R2.1D-Gate-R1 — E2E Spike 环境隔离修复。
 *
 * 架构：
 * - globalSetup starts TWO dev servers with isolated env:
 *   - spike-enabled (port 3100): CLAREAD_ENABLE_E2E_SPIKE=1 → 200
 *   - spike-disabled (port 3101): no flag → 404
 * - chromium-spike-enabled project: runs on port 3100, excludes gate-disabled
 * - chromium-spike-disabled project: runs on port 3101, only gate-disabled
 *
 * Why not Playwright's webServer option:
 * - per-project webServer (Playwright 1.60.0): never started, tests
 *   proceeded immediately with ERR_CONNECTION_REFUSED.
 * - top-level webServer (single): reuseExistingServer:true silently
 *   skipped server startup; reuseExistingServer:false conflicted with
 *   globalSetup because globalSetup runs first and its spawned process
 *   was detected as occupying the port.
 * - webServer array: started first server but silently skipped second.
 *
 * globalSetup gives full control: kill leftovers, start both servers
 * with explicit env vars, wait for readiness, clean shutdown.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  globalSetup: "./tests/e2e/gate-disabled-server-setup.ts",
  use: {
    baseURL: "http://127.0.0.1:3200",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium-spike-enabled",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: "http://127.0.0.1:3200",
      },
      testIgnore: /gate-disabled/,
    },
    {
      name: "chromium-spike-disabled",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: "http://127.0.0.1:3201",
      },
      testMatch: /gate-disabled/,
    },
  ],
});
