import { expect, test, type Page } from "@playwright/test";

/**
 * ASK-UX-COT-COMPOSER-R3 P3 — Reading Record Ask composer selection
 * slots + plural focus_anchors transport acceptance.
 *
 * Drives the REAL /app/reader-record/{recordId} page (native selections
 * via the Selection API, real surface + composer + floating toolbar).
 * All BFF routes are mocked at the network layer — no backend.
 *
 * Scenario (per viewport, 1440×900 desktop + 390×844 mobile):
 *   1. open Ask → permanent current-article chip (same display title
 *      truth as the reader masthead);
 *   2. select A → auto chip A;
 *   3. Escape clears the browser highlight but the auto chip survives;
 *   4. select B → auto chip becomes B (A replaced);
 *   5. toolbar「加入 Ask Claread」→ B promoted to manual (auto empty);
 *   6. select C → manual B + auto C;
 *   7. select D, pin D, re-select C → manual B/D + auto C;
 *   8. send → the captured request body carries exactly THREE
 *      focus_anchors [C(auto), B, D] (auto first, then pinned order),
 *      the legacy singular anchor = primary, and the current-article
 *      page identity; chips persist after send.
 *
 * CoT streaming/settled reasoning + expand interactions are covered by
 * ask-chain-of-thought.spec.ts and ask-ux-streaming-delta-r2.spec.ts
 * (both viewports included there).
 */

const RECORD_ID = "ask-focus-rec-1";
const BASE_ID = "ask-focus-base-1";
const THREAD_ID = "ask-focus-thread-1";
const MESSAGE_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeee01";
const TURN_RUN_ID = "ask-focus-turn-1";
const EXECUTION_VERSION = "reader_record_ask_agentic_v2";
const RECORD_TITLE = "Focus Anchors Fixture";

const ARTICLE_TEXT =
  "Institutional memory shapes policy choices in subtle ways. " +
  "A scarce few can turn passion into a stable income, but most simply adapt.";
const HEADING_TEXT = "Markets reward patience over time.";
const BLOCKQUOTE_TEXT = "Patience is the quiet engine of sustainable growth.";
const LIST_ITEM_TEXT = "Compounding turns small gains into large outcomes.";

// Selection phrases (distinct stable segments s1..s4).
const PHRASE_A = "Institutional memory";
const PHRASE_B = "Markets reward patience";
const PHRASE_C = "quiet engine";
const PHRASE_D = "small gains";

// ---------------------------------------------------------------------------
// Snapshot fixture — four stable source units (paragraph/heading/
// blockquote/list_item), sequential non-overlapping UTF-16 offsets.
// ---------------------------------------------------------------------------

function buildStableSourceUnit(params: {
  unitId: string;
  orderIndex: number;
  segmentId: string;
  text: string;
  baseStart: number;
  stableBlockType: string;
  headingLevel?: number;
}) {
  const { unitId, orderIndex, segmentId, text, baseStart, stableBlockType, headingLevel } =
    params;
  const baseEnd = baseStart + text.length;

  const navigationUnit = {
    unit_id: unitId,
    order_index: orderIndex,
    unit_type: "body" as const,
    boundary_quality: "normal" as const,
    base_start_utf16: baseStart,
    base_end_utf16: baseEnd,
    text_hash: `hash_${unitId}`,
    hash_algorithm: "fnv1a32-utf16" as const,
  };

  const anchorSegment = {
    anchor_segment_id: segmentId,
    sentence_id: segmentId,
    paragraph_id: unitId,
    unit_id: unitId,
    order_index: orderIndex,
    unit_order_index: orderIndex,
    segment_type: "sentence" as const,
    boundary_quality: "normal" as const,
    base_start_utf16: baseStart,
    base_end_utf16: baseEnd,
    unit_start_utf16: 0,
    unit_end_utf16: text.length,
    text_hash: `hash_${segmentId}`,
    hash_algorithm: "fnv1a32-utf16" as const,
  };

  const sourceBlock: Record<string, unknown> = {
    type: "reader_source_block",
    owner: "stable",
    base_id: BASE_ID,
    unit_id: unitId,
    stableBlockType,
    base_start_utf16: baseStart,
    base_end_utf16: baseEnd,
  };
  if (headingLevel !== undefined) {
    sourceBlock.headingLevel = headingLevel;
  }
  sourceBlock.children = [
    {
      type: "reader_anchor_segment",
      owner: "stable",
      base_id: BASE_ID,
      unit_id: unitId,
      anchor_segment_id: segmentId,
      sentence_id: segmentId,
      segment_type: "sentence",
      boundary_quality: "normal",
      base_start_utf16: baseStart,
      base_end_utf16: baseEnd,
      unit_start_utf16: 0,
      unit_end_utf16: text.length,
      text_hash: `hash_${segmentId}`,
      hash_algorithm: "fnv1a32-utf16",
      children: [
        {
          text,
          owner: "stable",
          lock_source: true,
          source_role: "segment_text",
          base_start_utf16: baseStart,
          base_end_utf16: baseEnd,
          anchor_segment_id: segmentId,
          segment_start_utf16: 0,
          segment_end_utf16: text.length,
          reader_vocabulary_marks: [],
          reader_grammar_note_marks: [],
        },
      ],
    },
  ];

  const valueUnit = {
    type: "reader_unit",
    owner: "stable",
    base_id: BASE_ID,
    unit_id: unitId,
    order_index: orderIndex,
    unit_type: "body",
    boundary_quality: "normal",
    base_start_utf16: baseStart,
    base_end_utf16: baseEnd,
    text_hash: `hash_${unitId}`,
    hash_algorithm: "fnv1a32-utf16",
    children: [sourceBlock],
  };

  return { navigationUnit, anchorSegment, valueUnit };
}

function makeSnapshot() {
  const u1Start = 0;
  const u1End = u1Start + ARTICLE_TEXT.length;
  const u2Start = u1End;
  const u2End = u2Start + HEADING_TEXT.length;
  const u3Start = u2End;
  const u3End = u3Start + BLOCKQUOTE_TEXT.length;
  const u4Start = u3End;
  const u4End = u4Start + LIST_ITEM_TEXT.length;

  const heading = buildStableSourceUnit({
    unitId: "u2",
    orderIndex: 2,
    segmentId: "s2",
    text: HEADING_TEXT,
    baseStart: u2Start,
    stableBlockType: "heading",
    headingLevel: 2,
  });
  const blockquote = buildStableSourceUnit({
    unitId: "u3",
    orderIndex: 3,
    segmentId: "s3",
    text: BLOCKQUOTE_TEXT,
    baseStart: u3Start,
    stableBlockType: "blockquote",
  });
  const listItem = buildStableSourceUnit({
    unitId: "u4",
    orderIndex: 4,
    segmentId: "s4",
    text: LIST_ITEM_TEXT,
    baseStart: u4Start,
    stableBlockType: "list_item",
  });

  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: `snap_${RECORD_ID}`,
    snapshot_taken_at: "2026-07-30T00:00:00Z",
    last_event_sequence: 1,
    record_id: RECORD_ID,
    record: {
      title: RECORD_TITLE,
      // Steady-state real record: the masthead shows the generated
      // display title, and the Ask current-article chip must resolve
      // the SAME truth (never raw record.title, never thread title).
      display_title_zh: RECORD_TITLE,
      title_generation_status: "succeeded",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      created_at: "2026-07-30T00:00:00Z",
      source_type: "text",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: BASE_ID,
      content_sha256: "b".repeat(64),
      canonicalizer_version: "1.0.0",
      builder_version: "1.0.0",
      segmenter_version: "1.0.0",
      hash_algorithm: "fnv1a32-utf16",
      text_length_utf16: u4End,
    },
    navigation: {
      units: [
        {
          unit_id: "u1",
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          base_start_utf16: u1Start,
          base_end_utf16: u1End,
          text_hash: "hash_u1",
          hash_algorithm: "fnv1a32-utf16",
        },
        heading.navigationUnit,
        blockquote.navigationUnit,
        listItem.navigationUnit,
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
        base_start_utf16: u1Start,
        base_end_utf16: u1End,
        unit_start_utf16: 0,
        unit_end_utf16: ARTICLE_TEXT.length,
        text_hash: "hash_s1",
        hash_algorithm: "fnv1a32-utf16",
      },
      heading.anchorSegment,
      blockquote.anchorSegment,
      listItem.anchorSegment,
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
        base_start_utf16: u1Start,
        base_end_utf16: u1End,
        text_hash: "hash_u1",
        hash_algorithm: "fnv1a32-utf16",
        children: [
          {
            type: "reader_source_block",
            owner: "stable",
            base_id: BASE_ID,
            unit_id: "u1",
            stableBlockType: "paragraph",
            base_start_utf16: u1Start,
            base_end_utf16: u1End,
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
                base_start_utf16: u1Start,
                base_end_utf16: u1End,
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
                    base_start_utf16: u1Start,
                    base_end_utf16: u1End,
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
        ],
      },
      heading.valueUnit,
      blockquote.valueUnit,
      listItem.valueUnit,
    ],
    enhancement_layers: [],
    user_assets: [],
    ask_supplements: [],
  };
}

// ---------------------------------------------------------------------------
// Mocks + login + selection helpers
// ---------------------------------------------------------------------------

async function mockBff(page: Page, capturedStreamBodies: unknown[]) {
  const snapshot = makeSnapshot();

  await page.route(`**/api/web/reader-plate/${RECORD_ID}/snapshot`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, ...snapshot }),
    });
  });

  await page.route(`**/api/web/reader-plate/${RECORD_ID}/events**`, async (route) => {
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

  await page.route(
    `**/api/web/reader-plate/records/${RECORD_ID}/article-rag-index/status`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, status: "unavailable" }),
      });
    },
  );

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

  // RR Ask threads: list → empty, create → default thread, get → messages [].
  await page.route("**/api/web/reader-ask/threads", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: THREAD_ID,
        record_id: RECORD_ID,
        title: RECORD_TITLE,
        is_default: true,
        selected_model: null,
        archived_at: null,
        created_at: "2026-07-30T00:00:00Z",
        updated_at: "2026-07-30T00:00:00Z",
        last_message_at: null,
      }),
    });
  });

  await page.route("**/api/web/reader-ask/threads?**", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: THREAD_ID,
        record_id: RECORD_ID,
        title: RECORD_TITLE,
        is_default: true,
        selected_model: null,
        archived_at: null,
        created_at: "2026-07-30T00:00:00Z",
        updated_at: "2026-07-30T00:00:00Z",
        last_message_at: null,
      }),
    });
  });

  await page.route(`**/api/web/reader-ask/threads/${THREAD_ID}?**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: THREAD_ID,
        record_id: RECORD_ID,
        title: RECORD_TITLE,
        is_default: true,
        selected_model: null,
        archived_at: null,
        created_at: "2026-07-30T00:00:00Z",
        updated_at: "2026-07-30T00:00:00Z",
        last_message_at: null,
        messages: [],
      }),
    });
  });

  await page.route("**/api/web/reader-ask/model-options**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        default_key: "ask-fast",
        items: [
          { key: "ask-fast", label: "Ask Fast", price_multiplier: 1.0, is_default: true },
        ],
      }),
    });
  });

  // Capture the stream request body (the P2 transport under test) and
  // answer with a minimal completed SSE so the panel settles cleanly.
  await page.route(
    `**/api/web/reader-ask/threads/${THREAD_ID}/messages/stream**`,
    async (route) => {
      const body = route.request().postDataJSON();
      capturedStreamBodies.push(body);
      const sse =
        `event: message.started\ndata: ${JSON.stringify({ message_id: MESSAGE_ID })}\n\n` +
        `event: message.completed\ndata: ${JSON.stringify({
          execution_version: EXECUTION_VERSION,
          final_status: "ok",
          answer_text: "已结合选区回答。",
          answer_blocks: [{ text: "已结合选区回答。", citation_ids: [] }],
          citations: [],
          knowledge_mode: null,
          source_status: null,
          web_search: null,
          message_id: MESSAGE_ID,
          thread_id: THREAD_ID,
          turn_run_id: TURN_RUN_ID,
        })}\n\n`;
      await route.fulfill({
        status: 200,
        headers: {
          "content-type": "text/event-stream; charset=utf-8",
          "x-accel-buffering": "no",
        },
        body: sse,
      });
    },
  );
}

async function loginAndNavigate(page: Page) {
  await page.route("**/api/web/auth/phone/request-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, message: "mock" }),
    });
  });
  await page.route("**/api/web/auth/phone/verify-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: {
        "set-cookie": "claread_web_phone=13800138000; Path=/; SameSite=Lax; HttpOnly",
      },
      body: JSON.stringify({ ok: true, phone: "13800138000", message: "ok" }),
    });
  });

  const targetPath = `/app/reader-record/${RECORD_ID}`;
  await page.goto(`/login?next=${encodeURIComponent(targetPath)}`);
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL(`**${targetPath}`);
  await expect(page.locator('[data-testid="reader-record-plate-surface"]')).toBeVisible();
  await expect(page.getByText(ARTICLE_TEXT, { exact: true })).toBeVisible();
}

/** Create a real native selection over a phrase inside the Reader document. */
async function selectPhrase(page: Page, phrase: string): Promise<void> {
  const selected = await page.evaluate((target) => {
    const doc = document.querySelector(".reader-record-plate-document");
    if (!doc) return "";
    const walker = document.createTreeWalker(doc, NodeFilter.SHOW_TEXT);
    let node: Text | null = null;
    while (walker.nextNode()) {
      const text = walker.currentNode as Text;
      if (text.data.includes(target)) {
        node = text;
        break;
      }
    }
    if (!node) return "";
    const start = node.data.indexOf(target);
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, start + target.length);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    return sel.toString();
  }, phrase);
  expect(selected).toBe(phrase);
}

/** Wait for the selection-actions floating toolbar, then pin directly. */
async function pinCurrentSelectionToAsk(page: Page): Promise<void> {
  const toolbar = page.locator(
    '[data-reader-record-floating-toolbar="selection-actions"]',
  );
  await expect(toolbar).toBeVisible({ timeout: 10_000 });
  const pinButton = toolbar.getByRole("button", {
    name: "加入 Ask Claread",
  });
  await expect(pinButton).toBeVisible({ timeout: 10_000 });
  await pinButton.click();
}

async function expectChipSet(
  page: Page,
  expectation: { auto: string[]; manual: string[] },
): Promise<void> {
  const strip = page.locator("[data-ask-context-strip]");
  await expect(strip).toBeVisible();
  const autoChips = strip.locator("[data-ask-selection-slot='auto']");
  const manualChips = strip.locator("[data-ask-selection-slot='manual']");
  await expect(autoChips).toHaveCount(expectation.auto.length);
  await expect(manualChips).toHaveCount(expectation.manual.length);
  for (let i = 0; i < expectation.auto.length; i += 1) {
    await expect(autoChips.nth(i)).toContainText(expectation.auto[i]);
  }
  for (let i = 0; i < expectation.manual.length; i += 1) {
    await expect(manualChips.nth(i)).toContainText(expectation.manual[i]);
  }
}

// ---------------------------------------------------------------------------
// Tests — desktop + mobile
// ---------------------------------------------------------------------------

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test.describe(`ASK-UX-COT-COMPOSER-R3 P3 focus anchors @ ${viewport.width}x${viewport.height}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport);
    });

    test("auto/manual selection slots drive plural focus_anchors on send", async ({
      page,
    }) => {
      const capturedStreamBodies: Array<Record<string, unknown>> = [];
      await mockBff(page, capturedStreamBodies);
      await loginAndNavigate(page);

      // 1. Open Ask → permanent current-article chip from the record title.
      await page.getByRole("button", { name: "打开 Ask Claread" }).click();
      await expect(page.locator("[data-ask-composer-textarea]")).toBeVisible();
      const articleChip = page.locator("[data-ask-current-article-chip]");
      await expect(articleChip).toBeVisible();
      await expect(articleChip).toContainText(RECORD_TITLE);
      // Non-removable: no remove button inside the article chip.
      expect(await articleChip.locator("button").count()).toBe(0);
      // No "基于：当前文章" provenance — the article is implicit context.
      await expect(page.getByText("基于：当前文章")).toHaveCount(0);

      // 2. Select A → auto chip A.
      await selectPhrase(page, PHRASE_A);
      await expectChipSet(page, { auto: [PHRASE_A], manual: [] });

      // 3. Escape clears the browser highlight — the auto chip survives.
      await page.keyboard.press("Escape");
      await expect(page.locator("[data-ask-selection-slot='auto']")).toHaveCount(1);
      await expectChipSet(page, { auto: [PHRASE_A], manual: [] });

      // 4. Select B → auto becomes B (A replaced).
      await selectPhrase(page, PHRASE_B);
      await expectChipSet(page, { auto: [PHRASE_B], manual: [] });

      // 5. Pin → B promoted to manual, auto slot empty (no duplicate chip).
      await pinCurrentSelectionToAsk(page);
      await expectChipSet(page, { auto: [], manual: [PHRASE_B] });

      // 6. Select C → manual B + auto C.
      await selectPhrase(page, PHRASE_C);
      await expectChipSet(page, { auto: [PHRASE_C], manual: [PHRASE_B] });

      // 7. Select D (auto D replaces C), pin D (manual B/D), re-select C
      //    → manual B/D + auto C.
      await selectPhrase(page, PHRASE_D);
      await expectChipSet(page, { auto: [PHRASE_D], manual: [PHRASE_B] });
      await pinCurrentSelectionToAsk(page);
      await expectChipSet(page, { auto: [], manual: [PHRASE_B, PHRASE_D] });
      await selectPhrase(page, PHRASE_C);
      await expectChipSet(page, { auto: [PHRASE_C], manual: [PHRASE_B, PHRASE_D] });

      // Explicit selections surface in provenance (3 处选区); the implicit
      // article never does.
      await expect(page.getByText("基于：3 处选区")).toBeVisible();

      // 8. Send → the request carries exactly three focus_anchors
      //    [C(auto) → B → D], the legacy singular anchor = primary (C),
      //    and the current-article page identity.
      await page.fill("[data-ask-composer-textarea='true']", "比较这三处选区");
      await page.click("button[aria-label='发送']");

      await expect
        .poll(() => capturedStreamBodies.length, { timeout: 15_000 })
        .toBeGreaterThan(0);
      const body = capturedStreamBodies[0] as {
        content: string;
        attachments: Array<{
          selected_text?: string | null;
          metadata: {
            reading_record_anchor?: {
              anchor_segment_id: string;
              record_id: string;
              base_id: string;
              generation: number;
              selected_text: string;
            } | null;
          };
        }>;
        page_identity: { record_id: string; title: string | null };
      };

      // Browser → BFF keeps the public attachment DTO. The BFF adapter
      // deterministically projects every reading_record_anchor into the
      // upstream plural focus_anchors contract (covered by
      // src/services/api/reader-ask.test.ts).
      const focusAttachments = body.attachments.filter(
        (attachment) => attachment.metadata.reading_record_anchor != null,
      );
      expect(focusAttachments).toHaveLength(3);
      const focusAnchors = focusAttachments.map(
        (attachment) => attachment.metadata.reading_record_anchor!,
      );
      // Auto first, then manuals in pin order: C(s3), B(s2), D(s4).
      expect(focusAnchors.map((a) => a.anchor_segment_id)).toEqual([
        "s3",
        "s2",
        "s4",
      ]);
      for (const anchor of focusAnchors) {
        expect(anchor.record_id).toBe(RECORD_ID);
        expect(anchor.base_id).toBe(BASE_ID);
        expect(anchor.generation).toBe(1);
      }
      // Current-article page identity rides along on the browser→BFF body.
      expect(body.page_identity.record_id).toBe(RECORD_ID);
      expect(body.page_identity.title).toBe(RECORD_TITLE);

      // Draft selections persist after send — chips are NOT cleared.
      await expect(page.locator("[data-ask-selection-slot='auto']")).toHaveCount(1);
      await expect(page.locator("[data-ask-selection-slot='manual']")).toHaveCount(2);
      await expect(page.locator("[data-ask-current-article-chip]")).toBeVisible();
    });

    test("current-article chip follows the masthead title truth, never the import placeholder", async ({
      page,
    }) => {
      await mockBff(page, []);
      // Override the snapshot route (later registration wins): an
      // import-era record whose generated title is still pending and
      // whose stored title is the "Untitled Reading" placeholder.
      const pendingSnapshot = {
        ...makeSnapshot(),
        record: {
          ...makeSnapshot().record,
          title: "Untitled Reading",
          display_title_zh: null,
          title_generation_status: "pending",
        },
      };
      await page.route(
        `**/api/web/reader-plate/${RECORD_ID}/snapshot`,
        async (route) => {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ ok: true, ...pendingSnapshot }),
          });
        },
      );
      await loginAndNavigate(page);

      await page.getByRole("button", { name: "打开 Ask Claread" }).click();
      await expect(page.locator("[data-ask-composer-textarea]")).toBeVisible();
      const articleChip = page.locator("[data-ask-current-article-chip]");
      await expect(articleChip).toBeVisible();
      // Pending title with a placeholder source title falls back to the
      // generic learner-facing label — same truth as the masthead.
      await expect(articleChip).toContainText("当前文章");
      // The import placeholder never surfaces anywhere on the page.
      await expect(articleChip).not.toContainText("Untitled Reading");
      await expect(page.getByText("Untitled Reading")).toHaveCount(0);
    });
  });
}
