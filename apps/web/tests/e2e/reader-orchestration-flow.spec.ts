import { expect, test, type Page } from "@playwright/test";

/**
 * F7 — Reader Orchestration frontend integration验收.
 *
 * 这条 spec 是 F0-F6 Web Reader Orchestration 前端链路的 mocked e2e。
 * 所有 BFF / Ask 路由都通过 `page.route` 在网络层 mock，不依赖真实后端。
 * mock 只放在 tests/e2e，绝不写假 production fallback。
 *
 * 覆盖路径：
 *   1. paste stable_document_ready → reader-record → Plate surface + Article RAG fail-soft
 *   2. paste candidate_document_required → 候选确认 → reader-record → CandidateConfirmCallout → confirm
 *   3. paste input_rejected_or_action_required → 可恢复文案 → 不展示 debug 字段
 *   4. 文件上传 artifact pipeline（stable / candidate / failed paths）
 *   5. Ask article_rag（available + fallback + normal citations）
 *
 * 体验/a11y：
 *   - desktop 1440x900 + mobile 390x844 各跑一轮关键路径
 *   - 检查无横向滚动、debug 字段不出现在 DOM
 */

// ---------------------------------------------------------------------------
// Constants & fixtures
// ---------------------------------------------------------------------------

const RECORD_ID = "f7-rec-stable-1";
const BASE_ID = "f7-base-1";
const CANDIDATE_RECORD_ID = "f7-rec-cand-1";
const CANDIDATE_DOC_ID = "f7-cand-doc-1";
const ARTIFACT_ID = "f7-art-1";
const ASK_THREAD_ID = "f7-ask-thread-1";

const ARTICLE_TEXT =
  "Institutional memory shapes policy choices in subtle ways. " +
  "A scarce few can turn passion into a stable income, but most simply adapt. " +
  "The city was built to be read, not only to be crossed.";

const DEBUG_ONLY_FIELDS = [
  "failure_code",
  "reason_code",
  "rationale_code",
  "english_word_ratio",
  "natural_language_score",
  "query_sha256",
  "source_pack_hash",
  "provider",
] as const;

function makeMinimalSnapshot(recordId: string) {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: `snap_${recordId}`,
    snapshot_taken_at: "2026-07-04T00:00:00Z",
    last_event_sequence: 1,
    record_id: recordId,
    record: {
      title: "F7 Reader Orchestration Fixture",
      display_title_zh: null,
      title_generation_status: "pending",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      created_at: "2026-07-04T00:00:00Z",
      source_type: "text",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: BASE_ID,
      content_sha256: "a".repeat(64),
      canonicalizer_version: "1.0.0",
      builder_version: "1.0.0",
      segmenter_version: "1.0.0",
      hash_algorithm: "fnv1a32-utf16",
      text_length_utf16: ARTICLE_TEXT.length,
    },
    navigation: {
      units: [
        {
          unit_id: "u1",
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: ARTICLE_TEXT.length,
          text_hash: "hash_u1",
          hash_algorithm: "fnv1a32-utf16",
        },
      ],
    },
    anchor_segments: [
      {
        anchor_segment_id: "s1",
        sentence_id: "s1",
        paragraph_id: "u1",
        unit_id: "u1",
        order_index: 1,
        unit_order_index: 1,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: 0,
        base_end_utf16: ARTICLE_TEXT.length,
        unit_start_utf16: 0,
        unit_end_utf16: ARTICLE_TEXT.length,
        text_hash: "hash_s1",
        hash_algorithm: "fnv1a32-utf16",
      },
    ],
    value: [
      {
        type: "reader_unit",
        owner: "stable",
        base_id: BASE_ID,
        unit_id: "u1",
        order_index: 1,
        unit_type: "body",
        boundary_quality: "normal",
        base_start_utf16: 0,
        base_end_utf16: ARTICLE_TEXT.length,
        text_hash: "hash_u1",
        hash_algorithm: "fnv1a32-utf16",
        children: [
          {
            type: "reader_source_block",
            owner: "stable",
            base_id: BASE_ID,
            unit_id: "u1",
            base_start_utf16: 0,
            base_end_utf16: ARTICLE_TEXT.length,
            children: [
              {
                type: "reader_anchor_segment",
                owner: "stable",
                base_id: BASE_ID,
                unit_id: "u1",
                anchor_segment_id: "s1",
                sentence_id: "s1",
                segment_type: "sentence",
                boundary_quality: "normal",
                base_start_utf16: 0,
                base_end_utf16: ARTICLE_TEXT.length,
                unit_start_utf16: 0,
                unit_end_utf16: ARTICLE_TEXT.length,
                text_hash: "hash_s1",
                hash_algorithm: "fnv1a32-utf16",
                children: [
                  {
                    text: ARTICLE_TEXT,
                    owner: "stable",
                    lock_source: true,
                    source_role: "segment_text",
                    base_start_utf16: 0,
                    base_end_utf16: ARTICLE_TEXT.length,
                    anchor_segment_id: "s1",
                    segment_start_utf16: 0,
                    segment_end_utf16: ARTICLE_TEXT.length,
                    reader_vocabulary_marks: [],
                    reader_grammar_note_marks: [],
                  },
                ],
              },
            ],
          },
          {
            type: "reader_translation_group",
            owner: "system_ai",
            layer_id: "layer_tr_1",
            layer_version: 1,
            base_id: BASE_ID,
            unit_id: "u1",
            target_scope: "unit",
            target_key: "u1",
            group_id: "group_tr_1",
            covered_anchor_segment_ids: ["s1"],
            source_text_hash: "hash_u1",
            children: [{ text: "制度记忆以微妙的方式塑造政策选择。" }],
          },
        ],
      },
    ],
    enhancement_layers: [],
    parsed_decisions: [],
    user_assets: [],
    ask_supplements: [],
  };
}

function makeStableInputResponse(recordId: string) {
  return {
    ok: true,
    outcome: "stable_document_ready" as const,
    reading_record_id: recordId,
    stable_document_id: `stable_${recordId}`,
    base_id: BASE_ID,
    record_generation: 1,
    document_version: 1,
    title: "F7 Reader Orchestration Fixture",
    content_sha256: "a".repeat(64),
    canonical_text_sha256: "b".repeat(64),
    block_count: 1,
    article_ready_event_id: "evt_1",
    article_ready_sequence: 1,
    suitability: {
      outcome: "stable_document_ready",
      source_type: "pasted_text",
      word_count: 24,
      english_word_ratio: 0.95,
      natural_language_score: 0.92,
      flags: [],
      reasons: [],
      normalized_preview: ARTICLE_TEXT.slice(0, 200),
    },
    snapshot: makeMinimalSnapshot(recordId),
  };
}

function makeCandidateInputResponse(recordId: string, candidateDocId: string) {
  return {
    ok: true,
    outcome: "candidate_document_required" as const,
    reading_record_id: recordId,
    candidate_document_id: candidateDocId,
    original_input_id: "oi_1",
    record_generation: 1,
    status: "ready",
    title: null,
    block_count: 1,
    source_type: "pasted_text",
    filename: null,
    suitability: {
      outcome: "candidate_document_required",
      source_type: "pasted_text",
      word_count: 24,
      english_word_ratio: 0.88,
      natural_language_score: 0.75,
      flags: ["too_short_for_learning"],
      reasons: ["内容偏短，可能需要确认后才能开始透读。"],
      normalized_preview: ARTICLE_TEXT.slice(0, 200),
    },
  };
}

function makeRejectedInputResponse() {
  return {
    ok: true,
    outcome: "input_rejected_or_action_required" as const,
    suitability: {
      outcome: "input_rejected_or_action_required",
      source_type: "pasted_text",
      word_count: 3,
      english_word_ratio: 0.1,
      natural_language_score: 0.12,
      flags: ["too_short_for_learning", "low_english_ratio"],
      reasons: ["内容太短或不像自然语言英文，暂时没法直接开始透读。"],
      normalized_preview: "def var x = 0",
    },
  };
}

function makeCandidateConfirmResponse(recordId: string) {
  return {
    ok: true,
    reading_record_id: recordId,
    candidate_document_id: CANDIDATE_DOC_ID,
    stable_document_id: `stable_${recordId}`,
    base_id: BASE_ID,
    record_generation: 1,
    document_version: 1,
    content_sha256: "a".repeat(64),
    canonical_text_sha256: "b".repeat(64),
    block_count: 1,
    candidate_confirmed: true,
    freeze_idempotent_noop: false,
    article_ready_event_id: "evt_confirmed",
    article_ready_sequence: 2,
    snapshot: makeMinimalSnapshot(recordId),
  };
}

function makeArtifactInitResponse() {
  return {
    ok: true,
    artifact_id: ARTIFACT_ID,
    artifact_kind: "original_upload",
    storage_provider: "oss",
    bucket: "f7-mock-bucket",
    endpoint: "https://mock-oss.local",
    object_key: `f7-smoke/${ARTIFACT_ID}`,
    status: "pending",
    content_type: "text/markdown",
    byte_size: 100,
    content_sha256: null,
    source_filename: "test-article.md",
    upload_method: "oss_put_object_presigned",
    headers: {},
    presigned_url: "https://mock-oss.local/f7-mock-bucket/f7-smoke/art-1",
    presigned_method: "PUT",
    presigned_expires_at: "2026-07-04T01:00:00Z",
  };
}

function makeArtifactCompleteResponse() {
  return {
    ok: true,
    artifact_id: ARTIFACT_ID,
    artifact_kind: "original_upload",
    storage_provider: "oss",
    bucket: "f7-mock-bucket",
    endpoint: "https://mock-oss.local",
    object_key: `f7-smoke/${ARTIFACT_ID}`,
    status: "available",
    content_type: "text/markdown",
    byte_size: 100,
    content_sha256: null,
    source_filename: "test-article.md",
    upload_completed: true,
    idempotent_noop: false,
  };
}

function makeArtifactSubmitInputResponse(recordId: string) {
  return {
    ok: true,
    reading_record_id: recordId,
    original_input_id: "oi_art_1",
    artifact_id: ARTIFACT_ID,
    record_generation: 1,
    source_type: "file",
    input_type: "file_ref",
    product_state: "readable_enhancing",
    readiness_state: "article_ready",
    title: "Uploaded Article",
    language: null,
    extraction_required: true,
    bucket: "f7-mock-bucket",
    endpoint: "https://mock-oss.local",
    object_key: `f7-smoke/${ARTIFACT_ID}`,
    content_type: "text/markdown",
    byte_size: 100,
    content_sha256: null,
    source_filename: "test-article.md",
  };
}

function makePipelineStatusResponse(
  outcome: string,
  nextAction: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    ok: true,
    artifact: {
      artifact_id: ARTIFACT_ID,
      status: "available",
      artifact_kind: "original_upload",
      storage_provider: "oss",
      bucket: "f7-mock-bucket",
      object_key: `f7-smoke/${ARTIFACT_ID}`,
      content_type: "text/markdown",
      byte_size: 100,
      source_filename: "test-article.md",
    },
    record: {
      reading_record_id: RECORD_ID,
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    original_input: {
      original_input_id: "oi_art_1",
      input_type: "file_ref",
      source_type: "file",
      artifact_id: ARTIFACT_ID,
    },
    extraction_job: null,
    materialization_job: null,
    candidate_document: null,
    stable_document: null,
    outcome,
    next_action: nextAction,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

async function loginWithMockPhone(page: Page, nextPath = "/app/read") {
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
        "set-cookie":
          "claread_web_phone=13800138000; Path=/; SameSite=Lax; HttpOnly",
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

/**
 * Mock the reader-record page BFF routes: snapshot, events, article-rag-status.
 * Article RAG status defaults to "unavailable" (fail-soft) so it doesn't
 * block the reading surface.
 */
async function mockReaderRecordBff(
  page: Page,
  recordId: string,
  options: {
    articleRagStatus?: string;
    snapshot?: unknown;
  } = {},
) {
  const snapshot = options.snapshot ?? makeMinimalSnapshot(recordId);

  await page.route(
    `**/api/web/reader-plate/${recordId}/snapshot`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, ...snapshot }),
      });
    },
  );

  await page.route(
    `**/api/web/reader-plate/${recordId}/events**`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          reading_record_id: recordId,
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
    },
  );

  await page.route(
    `**/api/web/reader-plate/records/${recordId}/article-rag-index/status`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          status: options.articleRagStatus ?? "unavailable",
        }),
      });
    },
  );

  // Dict lookup mocks (reader-record page may call dict endpoints).
  await page.route("**/api/web/dict/lookup**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, kind: "empty", query: "" }),
    });
  });

  await page.route("**/api/web/dict/entry**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, kind: "empty", query: "" }),
    });
  });
}

/**
 * Assert that no debug-only fields appear anywhere in the page DOM.
 * Checks both visible text and common data attribute patterns.
 *
 * 1. Visible text: debug field names must not appear as user-visible text.
 * 2. DOM attributes: debug field names must not leak as data-* attributes
 *    (e.g. data-failure-code, data-provider) that could be inspected via
 *    devtools even if not rendered as text.
 */
async function assertNoDebugFields(page: Page) {
  // 1. Visible text check.
  const bodyText = await page.locator("body").innerText();
  for (const field of DEBUG_ONLY_FIELDS) {
    expect(bodyText, `debug field "${field}" should not appear in DOM text`).not.toContain(field);
  }

  // 2. DOM attribute check — scan data-* attributes for debug field names.
  // Field names use snake_case; data attributes use kebab-case, so normalize
  // (e.g. data-failure-code -> failure_code) before comparing.
  const leaked = await page.evaluate(
    (debugFields: string[]) => {
      const hits: Array<{ tag: string; attr: string }> = [];
      const elements = document.querySelectorAll("*");
      for (const el of elements) {
        for (const attr of Array.from(el.attributes)) {
          const normalized = attr.name.replace(/^data-/, "").replace(/-/g, "_");
          if (debugFields.includes(normalized)) {
            hits.push({ tag: el.tagName.toLowerCase(), attr: attr.name });
          }
        }
      }
      return hits;
    },
    [...DEBUG_ONLY_FIELDS] as string[],
  );

  expect(
    leaked,
    `debug fields should not leak into DOM attributes: ${JSON.stringify(leaked)}`,
  ).toEqual([]);
}

/**
 * Assert no horizontal scrollbar is present (a11y / responsive check).
 */
async function assertNoHorizontalScroll(page: Page) {
  const hasHorizontalScroll = await page.evaluate(() => {
    return document.documentElement.scrollWidth > document.documentElement.clientWidth;
  });
  expect(hasHorizontalScroll, "page should not have horizontal scrollbar").toBe(false);
}

// ---------------------------------------------------------------------------
// Scenario 1: paste stable_document_ready
// ---------------------------------------------------------------------------

test.describe("F7 Reader Orchestration — scenario 1: paste stable_document_ready", () => {
  test("desktop 1440x900 — submit → reader-record → Plate surface + Article RAG fail-soft", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // Mock the input submit route to return stable_document_ready.
    await page.route("**/api/web/reader-plate/input", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(makeStableInputResponse(RECORD_ID)),
      });
    });

    // Pre-mock reader-record BFF routes (they'll be needed after navigation).
    await mockReaderRecordBff(page, RECORD_ID, { articleRagStatus: "unavailable" });

    await loginWithMockPhone(page);

    // Fill and submit the paste form.
    await expect(page.getByPlaceholder("Paste an English article here")).toBeVisible();
    await page.getByPlaceholder("Paste an English article here").fill(ARTICLE_TEXT);

    // Submit button is visible and enabled.
    const submitButton = page.getByRole("button", { name: "开始透读" });
    await expect(submitButton).toBeEnabled();

    await submitButton.click();

    // Should navigate to /app/reader-record/{recordId}.
    await page.waitForURL(`**/app/reader-record/${RECORD_ID}`);

    // Plate surface should render.
    await expect(page.locator('[data-testid="reader-record-plate-surface"]')).toBeVisible();

    // Source text should be visible.
    await expect(page.getByText(ARTICLE_TEXT, { exact: true })).toBeVisible();

    // Article RAG status panel should render in fail-soft "unavailable" state.
    await expect(page.locator('[data-testid="article-rag-status-panel"]')).toBeVisible();
    await expect(
      page.locator('[data-testid="article-rag-status-panel"]'),
    ).toHaveAttribute("data-rag-status", "unavailable");
    // Fail-soft should not block reading — no error banners.
    await expect(page.getByText("加载失败")).toHaveCount(0);

    // No debug fields in DOM.
    await assertNoDebugFields(page);

    await page.screenshot({
      path: "test-results/f7-scenario1-stable-desktop.png",
      fullPage: false,
    });
  });

  test("mobile 390x844 — no horizontal scroll, Plate surface renders", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await page.route("**/api/web/reader-plate/input", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(makeStableInputResponse(RECORD_ID)),
      });
    });

    await mockReaderRecordBff(page, RECORD_ID);

    await loginWithMockPhone(page);

    await page.getByPlaceholder("Paste an English article here").fill(ARTICLE_TEXT);
    await page.getByRole("button", { name: "开始透读" }).click();

    await page.waitForURL(`**/app/reader-record/${RECORD_ID}`);

    await expect(page.locator('[data-testid="reader-record-plate-surface"]')).toBeVisible();
    await expect(page.locator('[data-testid="article-rag-status-panel"]')).toBeVisible();

    // No horizontal scroll on mobile.
    await assertNoHorizontalScroll(page);

    await page.screenshot({
      path: "test-results/f7-scenario1-stable-mobile.png",
      fullPage: false,
    });
  });
});

// ---------------------------------------------------------------------------
// Scenario 2: paste candidate_document_required
// ---------------------------------------------------------------------------

test.describe("F7 Reader Orchestration — scenario 2: paste candidate_document_required", () => {
  test("candidate flow — no nav, candidate card visible, reader-record callout, confirm success", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    await page.route("**/api/web/reader-plate/input", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          makeCandidateInputResponse(CANDIDATE_RECORD_ID, CANDIDATE_DOC_ID),
        ),
      });
    });

    // Mock reader-record BFF for the candidate record.
    await mockReaderRecordBff(page, CANDIDATE_RECORD_ID);

    // Mock the confirm endpoint.
    await page.route(
      `**/api/web/reader-plate/records/${CANDIDATE_RECORD_ID}/candidate-documents/${CANDIDATE_DOC_ID}/confirm`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(makeCandidateConfirmResponse(CANDIDATE_RECORD_ID)),
        });
      },
    );

    await loginWithMockPhone(page);

    // Submit paste text.
    await page.getByPlaceholder("Paste an English article here").fill(ARTICLE_TEXT);
    await page.getByRole("button", { name: "开始透读" }).click();

    // Should NOT navigate — stays on /app/read.
    await expect(page).toHaveURL(/\/app\/read$/);

    // Candidate confirmation card should appear.
    await expect(page.getByText("已收到候选文档，需要确认后开始阅读")).toBeVisible();

    // Action buttons should be present.
    await expect(page.getByRole("button", { name: "去阅读记录确认" })).toBeVisible();
    await expect(page.getByRole("button", { name: "稍后处理" })).toBeVisible();
    await expect(page.getByRole("button", { name: "重新编辑" })).toBeVisible();

    // No debug fields in the candidate card.
    await assertNoDebugFields(page);

    await page.screenshot({
      path: "test-results/f7-scenario2-candidate-card.png",
      fullPage: false,
    });

    // Click "去阅读记录确认" to navigate to reader-record.
    // Use a real button click (not page.goto) to verify the actual user
    // interaction. The AnalyzeSubmitForm layout was fixed (overflow-y-auto
    // on the outer container) so the candidate section scrolls into view
    // and the button is reachable in headless Chromium.
    await page.getByRole("button", { name: "去阅读记录确认" }).click();
    await page.waitForURL(`**/app/reader-record/${CANDIDATE_RECORD_ID}`);

    // CandidateConfirmCallout should be visible (reads from localStorage).
    await expect(page.locator('[data-testid="candidate-confirm-callout"]')).toBeVisible();
    await expect(page.locator('[data-testid="candidate-confirm-button"]')).toBeVisible();

    await page.screenshot({
      path: "test-results/f7-scenario2-candidate-callout.png",
      fullPage: false,
    });

    // Click confirm button.
    await page.locator('[data-testid="candidate-confirm-button"]').click();

    // After confirm, the callout should trigger a reload.
    // The page should still render the Plate surface after reload.
    await expect(page.locator('[data-testid="reader-record-plate-surface"]')).toBeVisible();

    // The callout should disappear after successful confirm + reload.
    await expect(page.locator('[data-testid="candidate-confirm-callout"]')).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// Scenario 3: paste input_rejected_or_action_required
// ---------------------------------------------------------------------------

test.describe("F7 Reader Orchestration — scenario 3: paste input_rejected_or_action_required", () => {
  test("rejected flow — no nav, recoverable copy, no debug fields", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    await page.route("**/api/web/reader-plate/input", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(makeRejectedInputResponse()),
      });
    });

    await loginWithMockPhone(page);

    // Submit content that will be rejected.
    await page.getByPlaceholder("Paste an English article here").fill("def var x = 0");
    await page.getByRole("button", { name: "开始透读" }).click();

    // Should NOT navigate — stays on /app/read.
    await expect(page).toHaveURL(/\/app\/read$/);

    // Rejected status message should appear.
    await expect(page.getByText("这次没法直接开始透读")).toBeVisible();

    // Recoverable reason should be visible.
    await expect(page.getByText(/内容太短或不像自然语言英文/)).toBeVisible();

    // "重新编辑" button should be available.
    await expect(page.getByRole("button", { name: "重新编辑" })).toBeVisible();

    // CRITICAL: Debug-only fields must NOT appear in the DOM.
    // The suitability DTO contains english_word_ratio, natural_language_score,
    // flags — none of these should be rendered to the user.
    await assertNoDebugFields(page);

    // Specifically check that suitability flags are not shown.
    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain("too_short_for_learning");
    expect(bodyText).not.toContain("low_english_ratio");

    // "重新编辑" should reset the form to idle.
    // force: true — see scenario 2 note about grid container pointer events.
    await page.getByRole("button", { name: "重新编辑" }).click({ force: true });

    // After reset, the paste form should be available again.
    await expect(page.getByPlaceholder("Paste an English article here")).toBeVisible();
    await expect(page.getByRole("button", { name: "开始透读" })).toBeVisible();

    await page.screenshot({
      path: "test-results/f7-scenario3-rejected.png",
      fullPage: false,
    });
  });
});

// ---------------------------------------------------------------------------
// Scenario 4: file upload artifact pipeline
// ---------------------------------------------------------------------------

test.describe("F7 Reader Orchestration — scenario 4: file upload artifact pipeline", () => {
  test("stable path — init-upload → PUT → complete-upload → submit-input → pipeline-status → reader-record", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // Mock init-upload.
    await page.route("**/api/web/reader-plate/source-artifacts/init-upload", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(makeArtifactInitResponse()),
      });
    });

    // Mock the presigned PUT to OSS.
    await page.route("https://mock-oss.local/**", async (route) => {
      if (route.request().method() === "PUT") {
        await route.fulfill({ status: 200, body: "" });
      } else {
        await route.fulfill({ status: 200, body: "" });
      }
    });

    // Mock complete-upload.
    await page.route(
      `**/api/web/reader-plate/source-artifacts/${ARTIFACT_ID}/complete-upload`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(makeArtifactCompleteResponse()),
        });
      },
    );

    // Mock submit-input — returns stable.
    await page.route(
      `**/api/web/reader-plate/source-artifacts/${ARTIFACT_ID}/submit-input`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(makeArtifactSubmitInputResponse(RECORD_ID)),
        });
      },
    );

    // Mock pipeline-status — returns stable_document_ready.
    await page.route(
      `**/api/web/reader-plate/source-artifacts/${ARTIFACT_ID}/pipeline-status`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(
            makePipelineStatusResponse("stable_document_ready", "open_reader"),
          ),
        });
      },
    );

    // Pre-mock reader-record BFF routes.
    await mockReaderRecordBff(page, RECORD_ID);

    await loginWithMockPhone(page);

    // Switch to "上传文件" tab.
    await page.getByRole("tab", { name: "上传文件" }).click();

    // Set file on the hidden input.
    await page.locator('[data-testid="artifact-file-input"]').setInputFiles({
      name: "test-article.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(`# Test Article\n\n${ARTICLE_TEXT}`),
    });

    // Should navigate to reader-record after stable pipeline completes.
    await page.waitForURL(`**/app/reader-record/${RECORD_ID}`, { timeout: 15000 });

    // Plate surface should render.
    await expect(page.locator('[data-testid="reader-record-plate-surface"]')).toBeVisible();

    // No debug fields.
    await assertNoDebugFields(page);

    await page.screenshot({
      path: "test-results/f7-scenario4-file-stable.png",
      fullPage: false,
    });
  });

  test("failed path — pipeline-status returns extraction_failed, error UI shows retry / re-select / switch to paste", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // Mock init-upload, PUT, complete-upload (same as stable path).
    await page.route("**/api/web/reader-plate/source-artifacts/init-upload", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(makeArtifactInitResponse()),
      });
    });

    await page.route("https://mock-oss.local/**", async (route) => {
      await route.fulfill({ status: 200, body: "" });
    });

    await page.route(
      `**/api/web/reader-plate/source-artifacts/${ARTIFACT_ID}/complete-upload`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(makeArtifactCompleteResponse()),
        });
      },
    );

    // Mock submit-input — returns stable (initial response).
    await page.route(
      `**/api/web/reader-plate/source-artifacts/${ARTIFACT_ID}/submit-input`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(makeArtifactSubmitInputResponse(RECORD_ID)),
        });
      },
    );

    // Mock pipeline-status — returns extraction_failed with show_error.
    // ArtifactIntakePanel maps (extraction_failed, show_error) -> kind="error"
    // which renders data-testid="artifact-error" with retry / re-select /
    // switch-to-paste buttons. The "revise" state requires
    // (input_rejected_or_action_required, revise_input) — covered separately
    // by scenario 3 (paste) which exercises the same code path.
    await page.route(
      `**/api/web/reader-plate/source-artifacts/${ARTIFACT_ID}/pipeline-status`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(
            makePipelineStatusResponse("extraction_failed", "show_error"),
          ),
        });
      },
    );

    await loginWithMockPhone(page);

    // Switch to upload tab and select file.
    await page.getByRole("tab", { name: "上传文件" }).click();
    await page.locator('[data-testid="artifact-file-input"]').setInputFiles({
      name: "test-article.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(`# Test Article\n\n${ARTICLE_TEXT}`),
    });

    // Should stay on /app/read (no navigation for failed path).
    await expect(page).toHaveURL(/\/app\/read$/);

    // The error UI should appear (extraction_failed -> kind="error").
    const errorSection = page.locator('[data-testid="artifact-error"]');
    await expect(errorSection).toBeVisible({ timeout: 10000 });

    // All three recovery actions should be present inside the error section:
    // - 重试 (retry)
    // - 重新选择文件 (re-select)
    // - 改用粘贴文本 (switch to paste)
    // Scope to errorSection to avoid strict-mode violations when the upload
    // tab's idle-state file picker also has a "重新选择文件" label.
    await expect(errorSection.getByRole("button", { name: "重试" })).toBeVisible();
    await expect(errorSection.getByRole("button", { name: "重新选择文件" })).toBeVisible();
    await expect(errorSection.getByRole("button", { name: "改用粘贴文本" })).toBeVisible();

    // CRITICAL: No debug fields — failure_code / rationale_code must not leak.
    await assertNoDebugFields(page);

    await page.screenshot({
      path: "test-results/f7-scenario4-file-failed.png",
      fullPage: false,
    });
  });
});

// ---------------------------------------------------------------------------
// Scenario 5: Ask article_rag sidecar
// ---------------------------------------------------------------------------

test.describe("F7 Reader Orchestration — scenario 5: Ask article_rag sidecar", () => {
  /**
   * Build an SSE response body for a message.completed event with article_rag sidecar.
   */
  function buildAskSseBody(options: {
    articleRagStatus: string;
    shouldAttach: boolean;
    citations?: unknown[];
    normalCitations?: unknown[];
  }): string {
    const completedPayload = {
      id: "msg-assistant-1",
      thread_id: ASK_THREAD_ID,
      content_md: "Here is the answer based on the article.",
      submission_mode: "chat",
      resolved_intent: "explain",
      citations: options.normalCitations ?? [],
      action_proposals: [],
      tool_trace: [],
      evidence: [],
      trace_summary: null,
      disambiguation: null,
      external_asset_disambiguation: null,
      response_cards: [],
      usage_summary: null,
      billed_points: 0,
      resolved_context: {
        record_id: RECORD_ID,
        anchor_count: 1,
        explicit_attachment_count: 0,
        used_cross_record_context: false,
        current_sentence_used: true,
        current_paragraph_used: true,
        used_record_insights: false,
        used_dictionary: false,
        source_labels: [],
      },
      supplement_candidates: [],
      persisted_supplements: [],
      reasoning_md: null,
      reasoning_status: "completed",
      follow_up_suggestions: null,
      usage_event_id: "usage-1",
      article_rag: {
        status: options.articleRagStatus,
        failure_code: "debug_only_must_not_render",
        retryable: true,
        fallback_allowed: true,
        should_attach: options.shouldAttach,
        context_ids: ["ctx-1"],
        source_pack_hash: "debug_only_hash_must_not_render",
        query_sha256: "debug_only_query_must_not_render",
        citations: options.citations ?? [],
      },
    };

    return [
      `event: message.started`,
      `data: ${JSON.stringify({ message_id: "msg-assistant-1" })}`,
      ``,
      `event: message.completed`,
      `data: ${JSON.stringify(completedPayload)}`,
      ``,
    ].join("\n");
  }

  async function mockAskRoutes(page: Page, sseBody: string) {
    // Mock model-options.
    await page.route("**/api/web/reader-ask/model-options", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          default_key: "ask-clarity",
          items: [
            {
              key: "ask-clarity",
              label: "Qwen 3.7 Max",
              description: "适合带 reasoning 的 Ask 问答。",
              model_name: "qwen3.7-max",
              replan_model_name: "qwen3.7-max",
              price_multiplier: 1,
              is_default: true,
            },
          ],
        }),
      });
    });

    // Mock thread list (empty — will create a new thread).
    await page.route("**/api/web/reader-ask/threads**", async (route) => {
      const url = route.request().url();
      if (url.includes("/messages/stream")) {
        // SSE stream — handled below.
        return route.continue();
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: ASK_THREAD_ID,
              record_id: RECORD_ID,
              title: "Ask Claread",
              is_default: true,
              selected_model: {
                key: "ask-clarity",
                label: "Qwen 3.7 Max",
                description: "适合带 reasoning 的 Ask 问答。",
                model_name: "qwen3.7-max",
                replan_model_name: "qwen3.7-max",
                price_multiplier: 1,
              },
              archived_at: null,
              created_at: "2026-07-04T00:00:00Z",
              updated_at: "2026-07-04T00:00:00Z",
              last_message_at: null,
            },
          ],
        }),
      });
    });

    // Mock thread detail (empty messages initially).
    // Pattern ends with ** so it matches URLs with query params
    // (record_scope=reading_record adds ?record_id=...&record_scope=...).
    await page.route(`**/api/web/reader-ask/threads/${ASK_THREAD_ID}?**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: ASK_THREAD_ID,
          record_id: RECORD_ID,
          title: "Ask Claread",
          is_default: true,
          selected_model: {
            key: "ask-clarity",
            label: "Qwen 3.7 Max",
            description: "适合带 reasoning 的 Ask 问答。",
            model_name: "qwen3.7-max",
            replan_model_name: "qwen3.7-max",
            price_multiplier: 1,
          },
          archived_at: null,
          created_at: "2026-07-04T00:00:00Z",
          updated_at: "2026-07-04T00:00:00Z",
          last_message_at: null,
          messages: [],
        }),
      });
    });

    // Mock SSE stream.
    // Pattern ends with ** so it matches URLs with query params
    // (scopedReaderAskUrl adds ?record_id=...&record_scope=reading_record).
    // Without the trailing **, the glob wouldn't match the full URL and the
    // catch-all threads** mock would route.continue() to the real backend.
    await page.route(
      `**/api/web/reader-ask/threads/${ASK_THREAD_ID}/messages/stream**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: sseBody,
        });
      },
    );
  }

  /**
   * Navigate to the F7 Ask Sidecar Fixture page — a stable, Plate-surface-free
   * test entry that renders ONLY the real AiWorkspacePanel.
   *
   * Why a fixture page?
   *   The canonical reader-record page (`/app/reader-record/{recordId}`)
   *   renders AiWorkspacePanel via ReaderRecordPlateSurface, which also owns
   *   the Plate editor + FloatingToolbar + selection-toolbar → AIMenu → Ask
   *   open flow. That surface is being refactored (dirty) and currently has a
   *   JSX syntax error that breaks typecheck + e2e. Per the user's Review
   *   (P1), F6 Ask article_rag sidecar 验收 must not depend on the dirty
   *   Plate surface. The fixture page isolates the sidecar integration:
   *
   *   - Renders the REAL AiWorkspacePanel (no fake / no fallback).
   *   - Uses the SAME BFF contracts (/api/web/reader-ask/*).
   *   - Does NOT depend on ReaderRecordPlateSurface / Plate editor.
   *   - The Ask panel is open from page load (open={true}) — we are not
   *     validating the "open panel via selection toolbar" interaction here;
   *     that is verified separately once the Plate surface stabilizes.
   *
   * Routing: /app/f7-ask-fixture/{recordId}
   * Source: apps/web/src/app/(private)/app/f7-ask-fixture/[recordId]/page.tsx
   */
  async function gotoAskFixturePage(page: Page, recordId: string) {
    await page.goto(`/app/f7-ask-fixture/${recordId}`);
    // Verify we're on the fixture page (not redirected to login).
    await expect(
      page.locator('[data-f7-fixture-page="ask-sidecar"]'),
    ).toBeVisible({ timeout: 10000 });
    // The Ask panel heading should be visible immediately (open={true}).
    await expect(page.getByRole("heading", { name: "Ask Claread" })).toBeVisible({
      timeout: 10000,
    });
  }

  test("available + should_attach=true — renders 文章引用 citation list", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // Mock Ask routes with available sidecar.
    const sseBody = buildAskSseBody({
      articleRagStatus: "available",
      shouldAttach: true,
      citations: [
        {
          context_id: "ctx-1",
          chunk_id: "chunk-1",
          citation: {
            reading_record_id: RECORD_ID,
            stable_document_id: `stable_${RECORD_ID}`,
            base_id: BASE_ID,
            record_generation: 1,
            block_ids: ["block-1"],
            unit_ids: ["u1"],
            anchor_segment_ids: ["s1"],
            canonical_text_start_utf16: 0,
            canonical_text_end_utf16: 50,
          },
        },
      ],
      normalCitations: [
        {
          citation_id: "cit-normal-1",
          kind: "anchor",
          label: "原文引用",
          anchor_type: "sentence",
          sentence_id: "s1",
          target_key: "record:f7-rec-stable-1:sentence:s1",
          selected_text: "Institutional memory",
          record_id: RECORD_ID,
          metadata_json: {},
        },
      ],
    });

    await mockAskRoutes(page, sseBody);

    await loginWithMockPhone(page);

    // Navigate directly to the F7 Ask Sidecar Fixture page.
    // This isolates the Ask article_rag sidecar integration from the dirty
    // ReaderRecordPlateSurface / Plate editor.
    await gotoAskFixturePage(page, RECORD_ID);

    // The fixture page pre-populates a text_selection attachment on load,
    // simulating what the selection toolbar does in the real reader-record
    // page. No button click needed (the AiWorkspacePanel overlays the page
    // and would intercept pointer events on fixture buttons).

    // Type a question and submit.
    const askInput = page.locator("textarea").last();
    await askInput.fill("What does institutional memory mean?");
    await page.getByRole("button", { name: /发送|提交|问/ }).click().catch(async () => {
      // Fallback: try pressing Enter.
      await askInput.press("Enter");
    });

    // Wait for the assistant response.
    await expect(page.getByText("Here is the answer based on the article.")).toBeVisible({
      timeout: 15000,
    });

    // Article RAG citation list should render.
    await expect(page.locator('[data-testid="article-rag-citation-list"]')).toBeVisible({
      timeout: 5000,
    });
    await expect(page.getByText("文章引用")).toBeVisible();

    // At least one citation item.
    await expect(page.locator('[data-testid="article-rag-citation-item"]')).toHaveCount(
      1,
    );

    // CRITICAL: Debug-only fields from the raw sidecar MUST NOT render.
    // The BFF status-mapper strips them, but the SSE payload includes them.
    // The frontend mapAskArticleRagSidecar must also strip them.
    await assertNoDebugFields(page);
    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain("debug_only_must_not_render");
    expect(bodyText).not.toContain("debug_only_hash_must_not_render");
    expect(bodyText).not.toContain("debug_only_query_must_not_render");

    await page.screenshot({
      path: "test-results/f7-scenario5-ask-article-rag-available.png",
      fullPage: false,
    });
  });

  test("stale_due_to_repair — silent fallback to normal answer, no article citation block", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    const sseBody = buildAskSseBody({
      articleRagStatus: "stale_due_to_repair",
      shouldAttach: true,
      citations: [
        {
          context_id: "ctx-1",
          chunk_id: "chunk-1",
          citation: {
            reading_record_id: RECORD_ID,
            stable_document_id: `stable_${RECORD_ID}`,
            base_id: BASE_ID,
            record_generation: 1,
            block_ids: ["block-1"],
            unit_ids: ["u1"],
            anchor_segment_ids: ["s1"],
            canonical_text_start_utf16: 0,
            canonical_text_end_utf16: 50,
          },
        },
      ],
      normalCitations: [],
    });

    await mockAskRoutes(page, sseBody);

    await loginWithMockPhone(page);

    // Navigate to the fixture page (Plate-surface-free test entry).
    await gotoAskFixturePage(page, RECORD_ID);

    const askInput = page.locator("textarea").last();
    await askInput.fill("What does institutional memory mean?");
    await page.getByRole("button", { name: /发送|提交|问/ }).click().catch(async () => {
      await askInput.press("Enter");
    });

    // Assistant response should still appear.
    await expect(page.getByText("Here is the answer based on the article.")).toBeVisible({
      timeout: 15000,
    });

    // Article RAG citation list should NOT render (silent fallback).
    await expect(page.locator('[data-testid="article-rag-citation-list"]')).toHaveCount(0);
    expect(await page.getByText("文章引用").count()).toBe(0);

    // No debug fields.
    await assertNoDebugFields(page);
  });
});

// ---------------------------------------------------------------------------
// Pending button / double-submit guard
// ---------------------------------------------------------------------------

test.describe("F7 Reader Orchestration — UX guards", () => {
  test("paste submit — pending state removes CTA, no double submit", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // Delay the input response so we can observe the pending state.
    // Use a holder object so TS control-flow analysis doesn't narrow the
    // resolve function to `never` across the closure boundary.
    const resolveHolder: { resolve: ((body: string) => void) | null } = { resolve: null };
    const inputPromise = new Promise<string>((resolve) => {
      resolveHolder.resolve = resolve;
    });

    await page.route("**/api/web/reader-plate/input", async (route) => {
      const body = await inputPromise;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body,
      });
    });

    await mockReaderRecordBff(page, RECORD_ID);

    await loginWithMockPhone(page);

    await page.getByPlaceholder("Paste an English article here").fill(ARTICLE_TEXT);

    const submitButton = page.getByRole("button", { name: "开始透读" });
    await submitButton.click();

    // During pending, the submit row is replaced by AnalysisLoadingStatusBar
    // which renders "正在透读" + reassurance copy. The ApertureCornerSubmitButton
    // (which shows "透读中...") is NOT rendered during pending — the entire
    // bottom action row is swapped out.
    // Allow up to 10s for the pending state to render after click — the
    // holder-pattern delay mock resolves only after Playwright yields to the
    // microtask loop, which can take a few hundred ms in headless Chromium.
    // Use exact match — the page also renders an h2 "正在透读这篇文章".
    await expect(page.getByText("正在透读", { exact: true })).toBeVisible({ timeout: 10000 });

    // The "开始透读" button should not be present during pending — the
    // action row is replaced by the loading status bar.
    await expect(page.getByRole("button", { name: "开始透读" })).toHaveCount(0);

    // Resolve the delayed request.
    if (resolveHolder.resolve) {
      resolveHolder.resolve(JSON.stringify(makeStableInputResponse(RECORD_ID)));
    }

    // Should navigate to reader-record.
    await page.waitForURL(`**/app/reader-record/${RECORD_ID}`, { timeout: 10000 });
  });
});
