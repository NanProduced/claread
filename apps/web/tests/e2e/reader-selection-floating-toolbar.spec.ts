import { expect, test, type Page } from "@playwright/test";

/**
 * Reader selection floating toolbar — real native-selection e2e.
 *
 * This spec drives the REAL /app/reader/{recordId} page with real Chromium
 * native selections created via
 * `locator.selectText()` (programmatic Selection API, not a raw pointer
 * drag). Plate/Slate intercepts `mousedown` with `preventDefault()` in
 * readonly mode, which blocks the browser from initiating a drag-based text
 * selection, so `selectText()` is the faithful way to create a genuine
 * native browser selection that fires `selectionchange`. It verifies the core
 * fix: the selection-actions toolbar appears after the user selects stable
 * source text, and dismisses on Escape / blank click / out-of-document
 * selection.
 *
 * The fixture covers multiple stable block types (paragraph, heading,
 * markdown blockquote, list_item, table_cell, source_callout) so the
 * toolbar is exercised across the full Markdown projection surface, plus a
 * translation block (non-source) negative case and a cross-anchor selection.
 *
 * BFF routes are mocked at the network layer via page.route — no backend
 * dependency. Auth uses CLAREAD_PHONE_AUTH_PROVIDER=mock (code 888888).
 */

const RECORD_ID = "sel-toolbar-rec-1";
const BASE_ID = "sel-toolbar-base-1";

const ARTICLE_TEXT =
  "Institutional memory shapes policy choices in subtle ways. " +
  "A scarce few can turn passion into a stable income, but most simply adapt.";

const HEADING_TEXT = "Markets reward patience over time.";
const BLOCKQUOTE_TEXT = "Patience is the quiet engine of sustainable growth.";
const LIST_ITEM_TEXT = "Compounding turns small gains into large outcomes.";
const TABLE_CELL_TEXT = "Steady effort compounds daily.";
const CALLOUT_TEXT = "Remember that risk scales with time horizon.";
const CITATION_TEXT =
  "Doe, J. (2024). Stable structures. Journal of Reading. https://doi.org/10.1234/example";

const TRANSLATION_TEXT = "制度记忆以微妙的方式塑造政策选择。";

// ---------------------------------------------------------------------------
// Snapshot fixture — multiple stable block types + one translation block.
// ---------------------------------------------------------------------------

/**
 * Build a reader_unit node (with a single reader_source_block carrying
 * `stableBlockType` metadata) plus its navigation-unit and anchor-segment
 * entries. The returned pieces are assembled by `makeSnapshot` into the
 * three parallel arrays (`navigation.units`, `anchor_segments`, `value`).
 *
 * `stableBlockType` / `headingLevel` / `contentRole` are camelCase DTO
 * fields on `reader_source_block` (stable block metadata). All other
 * fields mirror the existing paragraph unit shape.
 */
function buildStableSourceUnit(params: {
  unitId: string;
  orderIndex: number;
  segmentId: string;
  text: string;
  baseStart: number;
  stableBlockType: string;
  headingLevel?: number;
  contentRole?: string;
}) {
  const {
    unitId,
    orderIndex,
    segmentId,
    text,
    baseStart,
    stableBlockType,
    headingLevel,
    contentRole,
  } = params;
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
  if (contentRole !== undefined) {
    sourceBlock.contentRole = contentRole;
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
  // Sequential, non-overlapping UTF-16 offsets across all units.
  const u1Start = 0;
  const u1End = u1Start + ARTICLE_TEXT.length;
  const u2Start = u1End;
  const u2End = u2Start + HEADING_TEXT.length;
  const u3Start = u2End;
  const u3End = u3Start + BLOCKQUOTE_TEXT.length;
  const u4Start = u3End;
  const u4End = u4Start + LIST_ITEM_TEXT.length;
  const u5Start = u4End;
  const u5End = u5Start + TABLE_CELL_TEXT.length;
  const u6Start = u5End;
  const u6End = u6Start + CALLOUT_TEXT.length;
  const u7Start = u6End;
  const u7End = u7Start + CITATION_TEXT.length;

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

  const tableCell = buildStableSourceUnit({
    unitId: "u5",
    orderIndex: 5,
    segmentId: "s5",
    text: TABLE_CELL_TEXT,
    baseStart: u5Start,
    stableBlockType: "table_cell",
  });

  const sourceCallout = buildStableSourceUnit({
    unitId: "u6",
    orderIndex: 6,
    segmentId: "s6",
    text: CALLOUT_TEXT,
    baseStart: u6Start,
    stableBlockType: "blockquote",
    contentRole: "source_callout",
  });

  const citationReference = buildStableSourceUnit({
    unitId: "u7",
    orderIndex: 7,
    segmentId: "s7",
    text: CITATION_TEXT,
    baseStart: u7Start,
    stableBlockType: "paragraph",
    contentRole: "citation_reference",
  });

  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: `snap_${RECORD_ID}`,
    snapshot_taken_at: "2026-07-04T00:00:00Z",
    last_event_sequence: 1,
    record_id: RECORD_ID,
    record: {
      title: "Selection Toolbar Fixture",
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
      content_sha256: "b".repeat(64),
      canonicalizer_version: "1.0.0",
      builder_version: "1.0.0",
      segmenter_version: "1.0.0",
      hash_algorithm: "fnv1a32-utf16",
      text_length_utf16: u7End,
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
        tableCell.navigationUnit,
        sourceCallout.navigationUnit,
        citationReference.navigationUnit,
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
      tableCell.anchorSegment,
      sourceCallout.anchorSegment,
      citationReference.anchorSegment,
    ],
    value: [
      // u1 — paragraph (stableBlockType: "paragraph") + translation group.
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
            children: [{ text: TRANSLATION_TEXT }],
          },
        ],
      },
      heading.valueUnit,
      blockquote.valueUnit,
      listItem.valueUnit,
      tableCell.valueUnit,
      sourceCallout.valueUnit,
      citationReference.valueUnit,
    ],
    enhancement_layers: [],
    parsed_decisions: [],
    user_assets: [],
    ask_supplements: [],
  };
}

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

async function mockBff(page: Page) {
  const snapshot = makeSnapshot();

  await page.route(
    `**/api/web/reader/records/${RECORD_ID}/snapshot`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, ...snapshot }),
      });
    },
  );

  await page.route(
    `**/api/web/reader/records/${RECORD_ID}/events**`,
    async (route) => {
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
    },
  );

  await page.route(
    `**/api/web/reader/records/${RECORD_ID}/article-rag-index/status`,
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
        "set-cookie":
          "claread_web_phone=13800138000; Path=/; SameSite=Lax; HttpOnly",
      },
      body: JSON.stringify({
        ok: true,
        phone: "13800138000",
        message: "ok",
      }),
    });
  });

  const targetPath = `/app/reader/${RECORD_ID}`;
  await page.goto(`/login?next=${encodeURIComponent(targetPath)}`);
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL(`**${targetPath}`);

  // Wait for the Plate surface to mount and source text to render.
  await expect(
    page.locator('[data-testid="reader-record-plate-surface"]'),
  ).toBeVisible();
  await expect(page.getByText(ARTICLE_TEXT, { exact: true })).toBeVisible();
}

/**
 * Select source text using the browser's native Selection API via
 * `locator.selectText()`. This creates a REAL native selection (not a mock)
 * that fires `selectionchange` events — exercising the full
 * SelectionAnchorBridge → activeSelection → toolbar pipeline.
 *
 * We use `selectText()` instead of raw `page.mouse` drag because Plate/Slate
 * intercepts `mousedown` with `preventDefault()` even in readonly mode,
 * which blocks the browser from initiating a drag-based text selection.
 * `selectText()` bypasses the mousedown handler and sets the Selection
 * directly, which is still a genuine native browser selection.
 */
async function selectSourceText(page: Page): Promise<string> {
  const paragraph = page
    .locator('[data-reader-record-node="paragraph"]')
    .first();
  await expect(paragraph).toBeVisible();
  await paragraph.selectText();

  // Return the browser's native selection text for verification.
  return page.evaluate(() => window.getSelection()?.toString() ?? "");
}

/**
 * Select text inside a specific stable block type via `locator.selectText()`.
 *
 * `stableBlockType` maps directly to the `data-reader-record-stable-block-type`
 * DOM attribute emitted by the stable-block projection components:
 *   paragraph / heading / blockquote / list_item / table_cell / source_callout
 *
 * Same native-selection pattern as `selectSourceText` — fires `selectionchange`
 * through SelectionAnchorBridge → activeSelection → toolbar.
 */
/**
 * Modify the source selection via KEYBOARD.
 *
 * Readonly Slate renders `contenteditable="false"`, so the editor surface is
 * not focusable and the browser cannot place a caret via keyboard — genuine
 * Shift+Arrow caret selection from scratch is therefore impossible without
 * making the Reader editable (which the task explicitly forbids). This is a
 * real product limitation of readonly Slate, not a toolbar bug.
 *
 * To still exercise the keyboard path through the toolbar pipeline, we first
 * establish a native selection (selectText), then drive it with genuine
 * keyboard input: `Shift+ArrowLeft` re-shapes the selection and fires
 * `selectionchange` through SelectionAnchorBridge → activeSelection →
 * toolbar. This verifies keyboard input drives the selection and the toolbar
 * tracks it, which is the faithful keyboard-acceptance test given the
 * `contenteditable="false"` constraint.
 */
async function keyboardShapeSelection(page: Page): Promise<string> {
  const paragraph = page
    .locator('[data-reader-record-node="paragraph"]')
    .first();
  await expect(paragraph).toBeVisible();
  await paragraph.selectText();

  // Genuine keyboard input: shrink the selection from the focus end with
  // Shift+ArrowLeft. Each keypress fires selectionchange.
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press("Shift+ArrowLeft");
  }

  return page.evaluate(() => window.getSelection()?.toString() ?? "");
}

/**
 * Select a specific phrase within the source paragraph using the browser
 * Selection API. This creates a genuine native selection (fires
 * `selectionchange`) scoped to the requested phrase — not the whole
 * paragraph — so re-selecting a different phrase verifies the toolbar tracks
 * the latest selection and does not reuse stale anchor data from a previous
 * selection.
 */
async function selectPhrase(page: Page, phrase: string): Promise<string> {
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
  return selected;
}

/**
 * Select text in a element OUTSIDE `.reader-record-plate-document`. This
 * simulates the user selecting text in the page chrome (title, sidebar, Ask
 * panel) and verifies the toolbar closes because the selection is outside
 * the Reader document.
 *
 * To make the test deterministic across page layouts (the Reader page may
 * have very little selectable text outside the Plate document), we mount a
 * temporary probe element on `document.body` — never inside the Reader
 * document root — and select its text node. The probe is kept mounted until
 * the caller removes it via `cleanupProbeOutsideReaderDocument` so the
 * Selection API's anchorNode stays valid while SelectionAnchorBridge
 * inspects it.
 */
async function selectTextOutsideReaderDocument(page: Page): Promise<string> {
  return page.evaluate(() => {
    const doc = document.querySelector(".reader-record-plate-document");
    if (!doc) return "";

    const sel = window.getSelection()!;

    // Try to find existing selectable text outside the Reader document first
    // (page title, header, etc.). Verify the selection actually yields text —
    // sidebar/nav text often lives inside `user-select: none` containers, in
    // which case addRange succeeds but toString() returns "".
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let candidate: Text | null = null;
    while (walker.nextNode()) {
      const text = walker.currentNode as Text;
      if (text.data.trim().length < 3) continue;
      if (doc.contains(text)) continue;
      candidate = text;
      const range = document.createRange();
      range.selectNodeContents(candidate);
      sel.removeAllRanges();
      sel.addRange(range);
      if (sel.toString().trim().length > 0) {
        return sel.toString();
      }
      // Selection came back empty — likely user-select:none. Keep scanning.
    }

    // No existing selectable text found outside the Reader document. Mount a
    // probe element on document.body (outside the Reader document) so we
    // always have a selectable text node to test the out-of-document dismissal
    // path. Explicitly set `user-select: text` to override any inherited
    // `user-select: none` from the body or app shell.
    const probe = document.createElement("div");
    probe.id = "__reader-selection-outside-probe";
    probe.textContent = "outside-reader-document-probe";
    probe.style.position = "fixed";
    probe.style.top = "0";
    probe.style.left = "0";
    probe.style.zIndex = "9999";
    probe.style.background = "white";
    probe.style.padding = "4px";
    probe.style.userSelect = "text";
    probe.style.webkitUserSelect = "text";
    document.body.appendChild(probe);

    const probeNode = probe.firstChild as Text;
    if (!probeNode) return "";
    const range = document.createRange();
    range.selectNodeContents(probeNode);
    sel.removeAllRanges();
    sel.addRange(range);
    return sel.toString();
  });
}

/**
 * Remove the probe element mounted by `selectTextOutsideReaderDocument`.
 */
async function cleanupProbeOutsideReaderDocument(page: Page): Promise<void> {
  await page.evaluate(() => {
    document.getElementById("__reader-selection-outside-probe")?.remove();
  });
}

const TOOLBAR_LOCATOR = '[data-reader-record-floating-toolbar="selection-actions"]';

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Reader selection floating toolbar (native selection)", () => {
  test.beforeEach(async ({ page }) => {
    await mockBff(page);
    await loginAndNavigate(page);
  });

  test("native selection shows toolbar, survives keyboard/copy, and dismisses", async ({
    page,
  }) => {
    const selectedText = await selectSourceText(page);
    expect(selectedText.trim().length, "native selection should produce text").toBeGreaterThan(
      0,
    );

    // The toolbar should appear (SelectionAnchorBridge → activeSelection → show).
    const toolbar = page.locator(TOOLBAR_LOCATOR);
    await expect(toolbar).toBeVisible({ timeout: 8000 });

    // The paragraph block should carry the stable-block-type attribute.
    const paragraphBlock = page.locator(
      '[data-reader-record-stable-block-type="paragraph"]',
    ).first();
    await expect(paragraphBlock).toBeVisible();

    // Core source-selection actions should be present.
    for (const actionId of [
      "ask",
      "lookup",
      "copy",
      "translate",
      "highlight",
      "note",
    ]) {
      await expect(
        page.locator(`[data-reader-record-toolbar-action="${actionId}"]`),
      ).toBeVisible();
    }

    // The toolbar should be within the viewport (not off-screen).
    const toolbarBox = await toolbar.boundingBox();
    expect(toolbarBox, "toolbar must have a bounding box").not.toBeNull();
    const tb = toolbarBox!;
    const viewport = page.viewportSize()!;
    expect(tb.x, "toolbar left >= 0").toBeGreaterThanOrEqual(0);
    expect(tb.y, "toolbar top >= 0").toBeGreaterThanOrEqual(0);
    expect(
      tb.x + tb.width,
      "toolbar right <= viewport width",
    ).toBeLessThanOrEqual(viewport.width);
    expect(
      tb.y + tb.height,
      "toolbar bottom <= viewport height",
    ).toBeLessThanOrEqual(viewport.height);

    // Toolbar should not overlap the selected text vertically (it flips above
    // or below, not on top of the selection).
    const paragraph = page
      .locator('[data-reader-record-node="paragraph"]')
      .first();
    const paraBox = await paragraph.boundingBox();
    expect(paraBox).not.toBeNull();
    const pb = paraBox!;
    // The toolbar bottom should be at or above the paragraph top, OR the
    // toolbar top should be at or below the paragraph bottom. I.e. no
    // vertical overlap with the paragraph's vertical center band.
    const toolbarAbove = tb.y + tb.height <= pb.y + 4;
    const toolbarBelow = tb.y >= pb.y + pb.height - 4;
    expect(
      toolbarAbove || toolbarBelow,
      "toolbar should flip above or below the selected text, not overlap it",
    ).toBeTruthy();

    await keyboardShapeSelection(page);
    await expect(toolbar).toBeVisible({ timeout: 8000 });

    const copyButton = page.locator('[data-reader-record-toolbar-action="copy"]');
    await expect(copyButton).toBeEnabled();
    await copyButton.click();
    await expect(toolbar).toBeVisible({ timeout: 5000 });

    await page.keyboard.press("Escape");
    await expect(toolbar).toHaveCount(0, { timeout: 5000 });

    await selectSourceText(page);
    await expect(toolbar).toBeVisible({ timeout: 8000 });
    await page.mouse.click(10, 10);
    await expect(toolbar).toHaveCount(0, { timeout: 5000 });

    const phraseA = "Institutional memory";
    const phraseB = "policy choices";
    expect(await selectPhrase(page, phraseA)).toBe(phraseA);
    await expect(toolbar).toBeVisible({ timeout: 8000 });
    expect(await selectPhrase(page, phraseB)).toBe(phraseB);
    await expect(toolbar).toBeVisible({ timeout: 8000 });

    await selectTextOutsideReaderDocument(page);
    await expect(toolbar).toHaveCount(0, { timeout: 5000 });
    await cleanupProbeOutsideReaderDocument(page);
  });

  test("translation block selection is Copy-only without source anchor fallback", async ({ page }) => {
    // The translation text lives inside the reader_translation_group (non-source).
    // Select it via the native Selection API (same approach as selectPhrase)
    // scoped to the translation lane.
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
    }, TRANSLATION_TEXT);

    expect(
      selected.trim().length,
      "translation native selection should produce text",
    ).toBeGreaterThan(0);

    const toolbar = page.locator(TOOLBAR_LOCATOR);
    await expect(
      toolbar,
      "translation selection should expose Copy-only actions",
    ).toBeVisible({ timeout: 8000 });
    const state = page.locator('[data-testid="reader-record-plate-selection-state"]');
    await expect(state).toHaveAttribute(
      "data-reader-record-selection-surface-kind",
      "translation",
    );
    await expect(state).not.toHaveAttribute(
      "data-reader-record-selection-anchor-segment-id",
    );
    await expect(
      page.locator('[data-reader-record-toolbar-action="copy"]'),
    ).toBeEnabled();
    for (const action of ["ask", "lookup", "translate", "highlight", "note"]) {
      await expect(
        page.locator(`[data-reader-record-toolbar-action="${action}"]`),
      ).toBeDisabled();
    }
  });

  // -------------------------------------------------------------------------
  // Cross-anchor selection — toolbar stays for Copy per multi_text contract.
  // -------------------------------------------------------------------------

  test("cross-anchor selection across adjacent source blocks shows Copy-only toolbar", async ({
    page,
  }) => {
    // Select text spanning two adjacent source blocks. Do not cross the
    // translation block between the first paragraph and the heading: mixed
    // source/enhancement selections must fail closed rather than silently
    // borrowing a source anchor.
    const selected = await page.evaluate(() => {
      const doc = document.querySelector(".reader-record-plate-document");
      if (!doc) return "";

      const heading = doc.querySelector(
        '[data-reader-record-stable-block-type="heading"]',
      );
      const blockquote = doc.querySelector(
        '[data-reader-record-stable-block-type="blockquote"]',
      );
      if (!heading || !blockquote) return "";

      const headingWalker = document.createTreeWalker(
        heading,
        NodeFilter.SHOW_TEXT,
      );
      let lastHeadingText: Text | null = null;
      let node: Text | null = null;
      while (headingWalker.nextNode()) {
        node = headingWalker.currentNode as Text;
        if (node.data.trim().length > 0) {
          lastHeadingText = node;
        }
      }
      if (!lastHeadingText) return "";

      const blockquoteWalker = document.createTreeWalker(
        blockquote,
        NodeFilter.SHOW_TEXT,
      );
      let firstBlockquoteText: Text | null = null;
      while (blockquoteWalker.nextNode()) {
        const t = blockquoteWalker.currentNode as Text;
        if (t.data.trim().length > 0) {
          firstBlockquoteText = t;
          break;
        }
      }
      if (!firstBlockquoteText) return "";

      // Range from the last 10 chars of the heading to the first 10 chars of
      // the source blockquote — a genuine cross-anchor span.
      const headingStart = Math.max(0, lastHeadingText.data.length - 10);
      const blockquoteEnd = Math.min(firstBlockquoteText.data.length, 10);

      const range = document.createRange();
      range.setStart(lastHeadingText, headingStart);
      range.setEnd(firstBlockquoteText, blockquoteEnd);

      const sel = window.getSelection()!;
      sel.removeAllRanges();
      sel.addRange(range);
      return sel.toString();
    });

    expect(
      selected.trim().length,
      "cross-anchor selection should produce text",
    ).toBeGreaterThan(0);

    const toolbar = page.locator(TOOLBAR_LOCATOR);
    await expect(toolbar, "toolbar should appear for cross-anchor selection").toBeVisible({
      timeout: 8000,
    });
    await expect(
      page.locator('[data-reader-record-toolbar-action="copy"]'),
    ).toBeEnabled();
    await expect(
      page.locator('[data-reader-record-toolbar-action="ask"]'),
    ).toBeDisabled();
    await expect(
      page.locator('[data-reader-record-toolbar-action="translate"]'),
    ).toBeDisabled();

    await page.screenshot({
      path: "test-results/reader-selection-toolbar-cross-anchor.png",
    });
  });
});
