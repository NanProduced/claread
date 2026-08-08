import { expect, test } from "@playwright/test";

/**
 * Live product acceptance for the Stable Document display-order contract
 * on the frozen multi-translation-group fixture record: source spans stay
 * interleaved with their translation lanes, and the navigation
 * data-attribute contract survives wrapper composition.
 *
 * Read-only against an existing fixture record — nothing is created.
 */

const FIXTURE_RECORD_ID = "927edede-33e0-4320-9a5b-3e00643ca763";
const FIXTURE_PHONE = "13900000000";

async function loginWithPhoneAuth(page: import("@playwright/test").Page) {
  await page.goto("/login?next=%2Fapp%2Fread");
  await page.getByLabel("手机号").fill(FIXTURE_PHONE);
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL("**/app/read");
}

/**
 * Ordered signature of outermost navigable blocks
 * (`node-kind:unit-id`, translation lanes marked).
 */
async function plateBlockSignature(
  page: import("@playwright/test").Page,
): Promise<string[]> {
  return page.evaluate(() => {
    const root = document.querySelector(".reader-record-plate-document");
    if (!root) return [];
    const sequence: string[] = [];
    root
      .querySelectorAll("[data-reader-record-node][data-unit-id]")
      .forEach((el) => {
        if (el.parentElement?.closest("[data-reader-record-node][data-unit-id]")) {
          return;
        }
        const kind = el.getAttribute("data-reader-record-node");
        const unitId = el.getAttribute("data-unit-id");
        const lane = el.hasAttribute("data-reader-record-translation-lane");
        sequence.push(`${kind}:${unitId}${lane ? "(lane)" : ""}`);
      });
    return sequence;
  });
}

test.describe("Stable Document display order (live fixture record)", () => {
  test("multi-translation-group unit renders source spans interleaved with their translations", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1280, height: 900 });
    const consoleProblems: string[] = [];
    page.on("pageerror", (error) => {
      consoleProblems.push(`pageerror: ${error.message}`);
    });
    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleProblems.push(`console.error: ${message.text()}`);
      }
    });

    await loginWithPhoneAuth(page);
    await page.goto(`/app/reader/${FIXTURE_RECORD_ID}`);
    await expect(page.locator(".reader-record-plate-document")).toBeVisible({
      timeout: 30_000,
    });
    // Enhancement layers materialize after the base snapshot; wait for the
    // translation lanes of the fixture record before reading the order.
    await expect(
      page.locator("[data-reader-record-translation-lane]").first(),
    ).toBeVisible({ timeout: 30_000 });

    // The frozen fixture's third unit spans s5–s8 with two translation
    // groups (s5–s7, s8) and per-sentence analyses. The rendered order must
    // keep each translation/annotation at its own anchor position instead of
    // hoisting every source span to the front.
    const signature = await plateBlockSignature(page);
    const u3First = signature.indexOf("paragraph:u3");
    expect(u3First, "first u3 source span present").toBeGreaterThanOrEqual(0);
    expect(signature[u3First + 1], "g(s5-s7) translation follows its span").toBe(
      "blockquote:u3(lane)",
    );

    const u3Second = signature.indexOf("paragraph:u3", u3First + 1);
    expect(u3Second, "second u3 source span present").toBeGreaterThan(u3First);
    const between = signature.slice(u3First + 2, u3Second);
    expect(
      between.length,
      "per-sentence analyses stay between the u3 spans",
    ).toBeGreaterThanOrEqual(2);
    for (const entry of between) {
      expect(entry.startsWith("paragraph:"), "no source span hoisted forward").toBe(
        false,
      );
    }
    expect(
      signature[u3Second + 1],
      "g(s8) translation follows the second span",
    ).toBe("blockquote:u3(lane)");
    expect(
      signature.indexOf("paragraph:u4"),
      "u4 starts only after the second u3 translation",
    ).toBe(u3Second + 2);

    // Navigation contract: both u3 source spans stay navigable and the
    // first one carries the unit-start marker.
    const u3SourceNodes = page.locator(
      '.reader-record-plate-document [data-reader-record-node="paragraph"][data-unit-id="u3"]',
    );
    await expect(u3SourceNodes).toHaveCount(2);
    await expect(u3SourceNodes.first()).toHaveAttribute(
      "data-reader-record-unit-start",
      "true",
    );

    expect(consoleProblems, "no app-level console errors / page errors").toEqual(
      [],
    );
  });
});
