/**
 * T4.2a-PUX-R4-R2.1D-Gate-R1 — E2E Spike 环境隔离修复。
 *
 * 验证 CLAREAD_ENABLE_E2E_SPIKE 未设置时：
 * - /e2e-plate-spike/surface 返回 404，surface harness 不渲染
 * - /e2e-plate-spike 返回 404，editor harness 不渲染
 * - window spike handles 不泄露（__spikeSurface, __spikeSurfaceReady,
 *   __spikeEditor, __spikeReady, __spikeHelpers）
 *
 * 此 spec 跑在 chromium-spike-disabled project（port 3001，无 flag）上，
 * 不依赖 reuseExistingServer 继承的环境变量。
 */

import { expect, test } from "@playwright/test";

test.describe("E2E spike gate — CLAREAD_ENABLE_E2E_SPIKE absent", () => {
  test("/e2e-plate-spike/surface returns 404 and does not expose surface harness", async ({
    page,
  }) => {
    const response = await page.goto("/e2e-plate-spike/surface");

    expect(response?.status()).toBe(404);

    // Surface harness root 不渲染
    await expect(
      page.locator('[data-testid="e2e-surface-harness-root"]'),
    ).toHaveCount(0);

    // window spike handles 不泄露
    const handles = await page.evaluate(() => {
      const w = window as unknown as {
        __spikeSurface?: unknown;
        __spikeSurfaceReady?: unknown;
      };
      return {
        spikeSurface: w.__spikeSurface,
        spikeSurfaceReady: w.__spikeSurfaceReady,
      };
    });
    expect(handles.spikeSurface).toBeUndefined();
    expect(handles.spikeSurfaceReady).toBeUndefined();
  });

  test("/e2e-plate-spike returns 404 and does not expose editor harness", async ({
    page,
  }) => {
    const response = await page.goto("/e2e-plate-spike");

    expect(response?.status()).toBe(404);

    // Editor harness root 不渲染
    await expect(
      page.locator('[data-testid="e2e-surface-harness-root"]'),
    ).toHaveCount(0);

    // window spike handles 不泄露
    const handles = await page.evaluate(() => {
      const w = window as unknown as {
        __spikeEditor?: unknown;
        __spikeReady?: unknown;
        __spikeHelpers?: unknown;
      };
      return {
        spikeEditor: w.__spikeEditor,
        spikeReady: w.__spikeReady,
        spikeHelpers: w.__spikeHelpers,
      };
    });
    expect(handles.spikeEditor).toBeUndefined();
    expect(handles.spikeReady).toBeUndefined();
    expect(handles.spikeHelpers).toBeUndefined();
  });
});
