import { expect, test } from "@playwright/test";

test("surface E2E harness is unavailable when its enable flag is absent", async ({
  page,
}) => {
  const response = await page.goto("/e2e-plate-spike/surface");

  expect(response?.status()).toBe(404);
  await expect(
    page.locator('[data-testid="e2e-surface-harness-root"]'),
  ).toHaveCount(0);
});