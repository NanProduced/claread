import { expect, test, type Page } from "@playwright/test";

/**
 * Smoke test for the Web Reader Plate read-only surface.
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
                      phrase_type: "fixed_collocation",
                      gloss: "把热爱转成可持续结果",
                      example: "turn passion into a career",
                    },
                  ],
                  reader_grammar_note_marks: [
                    {
                      mark_id: "mark_smoke_grammar_1",
                      item_id: "grammar_smoke_1",
                      owner: "system_ai",
                      layer_id: "layer_smoke_grammar_1",
                      item_type: "grammar_note",
                      anchor_segment_id: "s1",
                      start_offset: 0,
                      end_offset: 8,
                      selected_text: "A scarce",
                      segment_start_utf16: 0,
                      segment_end_utf16: 8,
                      starts_here: true,
                      ends_here: true,
                      span_index: 0,
                      span_count: 1,
                      show_note_chip: true,
                      grammar_point: "前置强调",
                      pattern: "a scarce ...",
                      note: "先抬出稀少性，再引出真正动作。",
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
        {
          type: "reader_sentence_analysis",
          owner: "system_ai",
          analysis_id: "analysis_smoke_1",
          layer_id: "layer_smoke_sentence_1",
          layer_version: 1,
          base_id: "base_smoke",
          unit_id: "u1",
          target_scope: "unit",
          target_key: "u1",
          anchor_segment_id: "s1",
          selected_text: "A scarce few can turn passion into a stable income.",
          label: "fronted focus and main action",
          analysis: "先强调稀少性，再说明把热爱变成稳定收入这个核心动作。",
          chunks: [
            {
              order: 1,
              label: "focus",
              text: "A scarce few",
            },
            {
              order: 2,
              label: "main action",
              text: "can turn passion into a stable income",
            },
          ],
          children: [{ text: "先强调稀少性，再说明把热爱变成稳定收入这个核心动作。" }],
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
    record: {
      title: "Reader Plate Smoke Fixture",
      created_at: "2026-06-21T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      product_state: "readable_enhancing",
    },
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
    anchor_segments: [],
    enhancement_layers: [
      {
        layer_id: "layer_smoke_grammar_1",
        layer_type: "grammar_note",
        layer_subtype: null,
        owner: "system_ai",
        base_id: "base_smoke",
        target_scope: "unit",
        target_key: "u1",
        status: "published",
        schema_version: 1,
        output: {
          schema_version: 1,
          items: [
            {
              item_type: "grammar_note",
              spans: [
                {
                  anchor_type: "text_range",
                  base_id: "base_smoke",
                  unit_id: "u1",
                  anchor_segment_id: "s1",
                  sentence_id: "s1",
                  segment_type: "sentence",
                  offset_unit: "utf16",
                  start_offset: 0,
                  end_offset: 8,
                  selected_text: "A scarce",
                  text_hash: "abcd1234",
                  hash_algorithm: "fnv1a32-utf16",
                },
              ],
              grammar_point: "前置强调",
              pattern: "a scarce ...",
              note: "先抬出稀少性，再引出真正动作。",
            },
          ],
        },
        published_at: "2026-06-21T00:00:00Z",
      },
      {
        layer_id: "layer_smoke_sentence_1",
        layer_type: "sentence_analysis",
        layer_subtype: null,
        owner: "system_ai",
        base_id: "base_smoke",
        target_scope: "unit",
        target_key: "u1",
        status: "published",
        schema_version: 1,
        output: {
          schema_version: 1,
          items: [
            {
              item_type: "sentence_analysis",
              anchor: {
                anchor_type: "text_range",
                base_id: "base_smoke",
                unit_id: "u1",
                anchor_segment_id: "s1",
                sentence_id: "s1",
                segment_type: "sentence",
                offset_unit: "utf16",
                start_offset: 0,
                end_offset: 52,
                selected_text: "A scarce few can turn passion into a stable income.",
                text_hash: "abcd1234",
                hash_algorithm: "fnv1a32-utf16",
              },
              label: "fronted focus and main action",
              analysis: "先强调稀少性，再说明把热爱变成稳定收入这个核心动作。",
              chunks: [
                {
                  order: 1,
                  label: "focus",
                  text: "A scarce few",
                },
                {
                  order: 2,
                  label: "main action",
                  text: "can turn passion into a stable income",
                },
              ],
            },
          ],
        },
        published_at: "2026-06-21T00:00:00Z",
      },
      {
        layer_id: "layer_smoke_vocab_1",
        layer_type: "vocabulary",
        layer_subtype: null,
        owner: "system_ai",
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
              phrase_type: "fixed_collocation",
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

async function loginWithMockPhone(page: Page, nextPath = `/app/reader/${RECORD_ID}`) {
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

  await page.goto(`/login?next=${encodeURIComponent(nextPath)}`);
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL(`**${nextPath}`);
}

async function mockReaderPlateRoutes(page: Page) {
  await page.route(`**/api/web/reader/records/${RECORD_ID}/events**`, async (route) => {
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

  await page.route(`**/api/web/reader/records/${RECORD_ID}/snapshot`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, ...makeSnapshot() }),
    });
  });
}

test("reader plate smoke: record_id query loads an existing snapshot directly", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockReaderPlateRoutes(page);

  await loginWithMockPhone(page, `/app/reader/${RECORD_ID}`);

  await expect(page.getByText("A scarce few can turn passion into a stable income.", { exact: true })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "译文" })).toBeVisible();
  await expect(page.getByText("语法解析 · 1 条", { exact: true })).toBeVisible();
  await expect(page.getByRole("note").filter({ hasText: "fronted focus and main action" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "在此贴入或导入英文文章" })).toHaveCount(0);

  await expect(
    page.getByText("A scarce few can turn passion into a stable income.", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("body")).toContainText(/可以开始阅读\s*10 词/);
  await expect(page.getByText("语法解析 · 1 条", { exact: true })).toBeVisible();
});
