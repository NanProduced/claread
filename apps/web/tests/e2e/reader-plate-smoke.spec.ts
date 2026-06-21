import { expect, test, type Page } from "@playwright/test";

/**
 * Smoke test for the D5 Web Reader Plate read-only slice.
 *
 * The page requires a real authenticated session to call the BFF, but the
 * BFF rejects `mock_phone` sessions. To keep this a self-contained browser
 * smoke (no real backend required), we mock the BFF routes at the network
 * layer — same pattern used by `analysis-loading-state.spec.ts`.
 */

const RECORD_ID = "smoke-record-1";

function makeSnapshotValue() {
  return [
    {
      type: "reader_unit",
      owner: "stable",
      base_id: "base_smoke",
      unit_id: "u1",
      order_index: 1,
      unit_type: "body",
      boundary_quality: "normal",
      base_start_utf16: 0,
      base_end_utf16: 52,
      text_hash: "abcd1234",
      hash_algorithm: "fnv1a32-utf16",
      children: [
        {
          type: "reader_source_block",
          owner: "stable",
          base_id: "base_smoke",
          unit_id: "u1",
          base_start_utf16: 0,
          base_end_utf16: 52,
          children: [
            {
              type: "reader_anchor_segment",
              owner: "stable",
              base_id: "base_smoke",
              unit_id: "u1",
              anchor_segment_id: "s1",
              sentence_id: "s1",
              segment_type: "sentence",
              boundary_quality: "normal",
              base_start_utf16: 0,
              base_end_utf16: 52,
              unit_start_utf16: 0,
              unit_end_utf16: 52,
              text_hash: "abcd1234",
              hash_algorithm: "fnv1a32-utf16",
              children: [
                {
                  text: "A scarce few can turn passion into a stable income.",
                  owner: "stable",
                  lock_source: true,
                  source_role: "segment_text",
                  base_start_utf16: 0,
                  base_end_utf16: 52,
                  anchor_segment_id: "s1",
                  segment_start_utf16: 0,
                  segment_end_utf16: 52,
                  reader_vocabulary_marks: [
                    {
                      mark_id: "mark_smoke_vocab_1",
                      layer_id: "layer_smoke_vocab_1",
                      item_type: "phrase_gloss",
                      anchor_segment_id: "s1",
                      start_offset: 9,
                      end_offset: 29,
                      selected_text: "few can turn passion",
                      segment_start_utf16: 9,
                      segment_end_utf16: 29,
                      starts_here: true,
                      ends_here: true,
                      phrase: "turn passion into",
                      phrase_type: "collocation",
                      gloss: "把热爱转成可持续结果",
                      example: "turn passion into a career",
                    },
                  ],
                },
              ],
            },
          ],
        },
        {
          type: "reader_translation",
          owner: "system_ai",
          layer_id: "layer_smoke_1",
          layer_version: 1,
          base_id: "base_smoke",
          unit_id: "u1",
          target_scope: "unit",
          target_key: "u1",
          target_language: "zh",
          confidence: "normal",
          notes: [],
          children: [{ text: "很少有人能把热爱变成稳定收入。" }],
        },
      ],
    },
  ];
}

function makeSnapshot() {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: "reader_snapshot_smoke",
    snapshot_taken_at: "2026-06-21T00:00:00Z",
    last_event_sequence: 1,
    record_id: RECORD_ID,
    base: {
      base_id: "base_smoke",
      content_sha256: "a".repeat(64),
      canonicalizer_version: "1.0.0",
      builder_version: "1.0.0",
      segmenter_version: "1.0.0",
      text_length_utf16: 52,
      hash_algorithm: "fnv1a32-utf16",
    },
    navigation: { units: [] },
    enhancement_layers: [
      {
        layer_id: "layer_smoke_vocab_1",
        layer_type: "vocabulary",
        layer_subtype: null,
        base_id: "base_smoke",
        target_scope: "unit",
        target_key: "u1",
        status: "published",
        schema_version: 1,
        output: {
          schema_version: 1,
          items: [
            {
              item_type: "phrase_gloss",
              anchor: {
                anchor_type: "text_range",
                base_id: "base_smoke",
                unit_id: "u1",
                anchor_segment_id: "s1",
                sentence_id: "s1",
                segment_type: "sentence",
                offset_unit: "utf16",
                start_offset: 9,
                end_offset: 29,
                selected_text: "few can turn passion",
                text_hash: "abcd1234",
                hash_algorithm: "fnv1a32-utf16",
              },
              phrase: "turn passion into",
              phrase_type: "collocation",
              gloss: "把热爱转成可持续结果",
              example: "turn passion into a career",
            },
          ],
        },
        published_at: "2026-06-21T00:00:00Z",
      },
    ],
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: makeSnapshotValue(),
  };
}

async function loginWithMockPhone(page: Page) {
  await page.route("**/api/web/auth/phone/request-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "本地调试验证码已生成，请使用 888888。",
      }),
    });
  });

  await page.route("**/api/web/auth/phone/verify-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: {
        "set-cookie": "claread_web_phone=13800138000; Path=/; SameSite=Lax; HttpOnly",
      },
      body: JSON.stringify({
        ok: true,
        phone: "13800138000",
        message: "已进入本地调试登录态。",
      }),
    });
  });

  await page.goto("/login?next=/app/reader-plate");
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL("**/app/reader-plate");
}

test("reader plate smoke: submit renders source text, translation, and vocabulary, polling stays calm", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });

  // Mock the submit BFF route to return a valid snapshot.
  await page.route("**/api/web/reader-plate/submit", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        record_id: RECORD_ID,
        base_id: "base_smoke",
        article_ready_sequence: 1,
        snapshot: makeSnapshot(),
      }),
    });
  });

  // Mock the events polling route to return caught-up (no reload, no events).
  await page.route("**/api/web/reader-plate/*/events**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        reading_record_id: RECORD_ID,
        after_sequence: 1,
        next_after_sequence: 1,
        last_event_sequence: 1,
        has_more: false,
        truncated: false,
        reload_required: false,
        reload_reason: null,
        events: [],
      }),
    });
  });

  // Mock the snapshot reload route (in case polling triggers a reload).
  await page.route("**/api/web/reader-plate/*/snapshot", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, ...makeSnapshot() }),
    });
  });

  await loginWithMockPhone(page);

  // The submit form should be visible.
  await expect(page.getByRole("heading", { name: "透读新文章" })).toBeVisible();
  await expect(page.getByPlaceholder("Paste an English article here")).toBeVisible();

  // Fill and submit.
  await page.getByPlaceholder("Paste an English article here").fill("A scarce few can turn passion into a stable income.");
  await page.getByRole("button", { name: "开始解析" }).click();

  // The reader surface should render the source text and translation.
  await expect(page.locator('[data-reader-node="unit"]')).toBeVisible();
  await expect(page.locator('[data-reader-node="source-block"]')).toBeVisible();
  await expect(page.locator('[data-reader-node="anchor-segment"]')).toBeVisible();
  await expect(page.locator('[data-reader-node="translation"]')).toBeVisible();
  await expect(page.locator('[data-reader-vocabulary-chip="phrase_gloss"]')).toBeVisible();

  // Source text, translation, and vocabulary annotation should be present.
  await expect(page.getByText("A scarce few can turn passion into a stable income.")).toBeVisible();
  await expect(page.getByText("很少有人能把热爱变成稳定收入。")).toBeVisible();
  await expect(page.getByText("搭配 · 把热爱转成可持续结果")).toBeVisible();

  // No error states should be visible after caught-up polling.
  await expect(page.getByText("批注更新暂时中断")).toHaveCount(0);
  await expect(page.getByText("加载失败")).toHaveCount(0);

  await page.screenshot({
    path: "test-results/reader-plate-smoke.png",
    fullPage: false,
  });
});
