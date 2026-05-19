import { expect, test } from "@playwright/test";

test("reader asset recovery story supports create, reflect, and clickback", async ({ page }) => {
  await page.goto("/?story=reader--platereadersurface--asset-recovery");

  await expect(page.getByRole("button", { name: "Add Highlight" })).toBeVisible();

  await page.getByRole("button", { name: "Add Highlight" }).click();
  await expect(page.locator(".reader-user-range").first()).toBeVisible();

  await page.getByRole("button", { name: "Add Note" }).click();
  await expect(page.getByText("这里承接上句，强调政策路径延续。")).toBeVisible();

  await page.getByRole("button", { name: "Toggle Favorite" }).click();
  await expect(page.locator(".reader-annotation-gutter-marker--favorite")).toBeVisible();

  await page.locator(".reader-annotation-gutter-marker--favorite").click();
  await expect(page.getByTestId("asset-jump-target")).toContainText("record:record-1:range:s1:28:43:hash-policy");
  await expect(page.locator(".reader-route-focus-range").first()).toBeVisible();

  await page.getByText("这里承接上句，强调政策路径延续。").click();
  await expect(page.getByTestId("asset-jump-target")).toContainText("record:record-1:sentence:s2");
  await expect(page.locator("[data-sentence-id='s2']")).toHaveClass(/reader-route-focus-frame/);
});
