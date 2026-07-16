/**
 * T4.2a-PUX-R4-R2.2-P2c-R1 — Grammar First-Publish 语义插入 + Quick Peek 安全回退 E2E。
 *
 * 通过真实 ReaderRecordPlateSurface 驱动 grammar 首发 reload pipeline，
 * 验证 P2c-R1 的两个关键不变式：
 *
 * - Test 1: vocabulary Quick Peek 锚定 seg_1 → grammar 首发到达 seg_2 →
 *   panel 保持正确锚定（targeted_apply 路径）或安全关闭（fallback_full_reload 路径）。
 *   关键不变式：panel 不会以零尺寸或位于 (0,0) 的 detached 状态残留。
 * - Test 2: T4.2a-PUX-R4-R3-R1: structural change 触发 fallback_full_reload →
 *   Quick Peek 保持打开并重新锚定到原词汇（anchor 仍存在），无 detached (0,0) panel。
 *
 * 行为说明（real projection）：
 *   真实 projection 中，anchor paragraph 的 text leaf 在首发时会获得
 *   reader_grammar_note_marks，导致 paragraph 语义内容在 prev/next 间变化，
 *   触发 merger C6 check fail-closed → fallback_full_reload。
 *   这是正确且安全的行为。测试同时处理两种终态：
 *   - targeted_apply：断言 insertNodes 使用、Quick Peek 保留、rect 有效
 *   - fallback_full_reload（R3-R1）：断言 Quick Peek 保持打开、重新锚定、rect 有效
 *
 * harness 位于 /e2e-plate-spike/surface（server-side env-gated），挂载真实
 * ReaderRecordPlateSurface。测试通过 `window.__spikeSurface` 驱动 reload pipeline。
 *
 * 边界：不修改 production Surface / polling / merger / backend / payload。
 */

import { expect, test, type Page } from "@playwright/test";

const HARNESS_URL = "/e2e-plate-spike/surface";

// 拦截 Surface 可能发起的 API 调用（词典查询、收藏、反馈），
// 返回良性 mock 响应，使测试自包含、不依赖后端。
async function mockApiRoutes(page: Page) {
  await page.route("**/api/web/dict/lookup*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, entries: [] }),
    });
  });
  await page.route("**/api/web/favorites**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, favorited: false }),
    });
  });
  await page.route("**/api/web/feedback**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
}

async function waitForHarnessReady(page: Page) {
  await page.goto(HARNESS_URL);
  await page.waitForFunction(
    () =>
      (window as unknown as { __spikeSurfaceReady?: boolean })
        .__spikeSurfaceReady === true,
    undefined,
    { timeout: 15_000 },
  );
}

interface PanelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface PanelState {
  /** 面板是否在 DOM 中可见且有非零尺寸。 */
  visible: boolean;
  /** 面板的 boundingBox（仅在 visible=true 时有意义）。 */
  rect: PanelRect | null;
}

/**
 * 采集 Quick Peek 浮层的当前可见状态与 boundingBox。
 *
 * 仅当面板元素存在于 DOM 中、未被 display:none / visibility:hidden 隐藏、
 * 且 getBoundingClientRect 返回非零尺寸时，才判定为 visible=true。
 * 这避免了 detached anchor 导致的零尺寸浮层被误判为"可见"。
 */
async function getPanelState(page: Page): Promise<PanelState> {
  return page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>(
      '[data-testid="reader-record-plate-lookup-panel"]',
    );
    if (!panel) return { visible: false, rect: null };
    const rect = panel.getBoundingClientRect();
    const style = window.getComputedStyle(panel);
    const hasSize = rect.width > 0 && rect.height > 0;
    const isRendered =
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.opacity !== "0" &&
      hasSize;
    if (!isRendered) return { visible: false, rect: null };
    return {
      visible: true,
      rect: {
        x: rect.left,
        y: rect.top,
        width: rect.width,
        height: rect.height,
      },
    };
  });
}

/**
 * 断言 Quick Peek 浮层处于安全终态。
 *
 * 两种可接受终态：
 *   - 安全关闭（visible=false）：fallback_full_reload 路径，Quick Peek 被确定性关闭。
 *   - 有效锚定（visible=true 且 rect 有效）：targeted_apply 路径，Quick Peek 保留。
 *
 * 关键不变式：浮层不以零尺寸或位于 (0,0) 的 detached 状态残留。
 * detached anchor 会让 Floating UI 将浮层定位到 (0,0)，此时 rect.x === 0 && rect.y === 0。
 */
function assertPanelSafe(state: PanelState) {
  if (!state.visible) {
    // fallback_full_reload 路径：Quick Peek 已安全关闭，符合预期。
    return;
  }
  // targeted_apply 路径：Quick Peek 保留，必须有有效 rect。
  const rect = state.rect!;
  expect(rect.width).toBeGreaterThan(0);
  expect(rect.height).toBeGreaterThan(0);
  // 不在左上角 (0,0) — detached anchor 会让浮层落到 (0,0)。
  // 允许 x 或 y 任一为 0（如贴边），但不允许两者同时为 0。
  const isAtOrigin = rect.x === 0 && rect.y === 0;
  expect(isAtOrigin).toBe(false);
}

// ===========================================================================
// 1. vocabulary Quick Peek → grammar 首发 → panel 保持锚定或安全关闭
// ===========================================================================

test.describe("1. R2.2-P2c grammar 首发 Quick Peek 安全终态", () => {
  test("打开 vocabulary Quick Peek → grammar 首发到达 → panel 保持锚定或安全关闭", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);

    // 先加载与 next 完全同形、但尚未发布 grammar marks/group 的 prev。
    // 这样真实 Surface 路径只面对 P2c 允许的 paragraph mark + group 插入变化。
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const next = s.makeGrammarFirstPublishSnapshot({
        anchorSegmentId: "seg_2",
        grammarNote: "Test grammar note.",
      });
      const prev = JSON.parse(JSON.stringify(next));
      prev.snapshot_id = "snapshot_grammar_first_publish_prev";
      prev.last_event_sequence = 8;
      const unit = prev.value[0];
      for (const child of unit.children) {
        if (child.type !== "reader_source_block") continue;
        for (const segment of child.children) {
          if (segment.type !== "reader_anchor_segment") continue;
          for (const leaf of segment.children) {
            leaf.reader_grammar_note_marks = [];
          }
        }
      }
      s.loadSnapshot(prev);
    });
    await page.waitForTimeout(250);

    // 等待 Plate 文档渲染完成。
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 在 seg_1 的 vocabulary mark（"memory"）上点击打开 Quick Peek。
    // 初始 snapshot 无 user_assets，vocab mark 点击不会被 user_highlight 拦截。
    const vocabMark = page.locator(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    await vocabMark.click();

    // 等待 Quick Peek 浮层可见。
    await expect(
      page.locator('[data-testid="reader-record-plate-lookup-panel"]'),
    ).toBeVisible({ timeout: 10_000 });

    // 采集 reload 前的浮层状态（应非零、在 vocab mark 附近）。
    const beforeState = await getPanelState(page);
    expect(beforeState.visible).toBe(true);
    assertPanelSafe(beforeState);

    // 触发 grammar 首发 reload：seg_2 的 paragraph 获得 grammar marks 并插入
    // callout-group；Quick Peek 锚定在未触及的 seg_1。此处必须 targeted_apply，
    // 不能接受 fallback_full_reload.
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const nextSnapshot = s.makeGrammarFirstPublishSnapshot({
        anchorSegmentId: "seg_2",
        grammarNote: "Test grammar note.",
      });
      const event = s.makeGrammarFirstPublishEvent({
        anchorSegmentId: "seg_2",
      });
      s.reloadWith(nextSnapshot, [event], {
        generation: 1,
        baseId: "base_1",
      });
    });

    // 等待 reload pipeline 完成。
    await page.waitForTimeout(1000);

    // 真实成功路径：Quick Peek 必须仍可见并保有有效锚点；grammar group 已插入。
    const afterState = await getPanelState(page);
    expect(afterState.visible).toBe(true);
    assertPanelSafe(afterState);
    await expect(
      page.locator(
        '[data-reader-record-block-id="callout-group:unit_1:seg_2"]',
      ),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/p2c-grammar-first-publish-quick-peek-safe.png",
    });
  });
});

// ===========================================================================
// 2. T4.2a-PUX-R4-R3-R1: fallback full reload → Quick Peek 保持打开并重新锚定
// ===========================================================================

test.describe("2. R3-R1 fallback full reload 重新锚定 Quick Peek", () => {
  test("structural change 触发 fallback_full_reload → Quick Peek 保持打开、重新锚定到原词汇", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);

    // 等待 Plate 文档渲染完成。
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 在 seg_1 的 vocabulary mark（"memory"）上点击打开 Quick Peek。
    const vocabMark = page.locator(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    await vocabMark.click();

    // 等待 Quick Peek 浮层可见。
    const panel = page.locator(
      '[data-testid="reader-record-plate-lookup-panel"]',
    );
    await expect(panel).toBeVisible({ timeout: 10_000 });

    // 采集 reload 前的浮层状态（应非零、在 vocab mark 附近）。
    const beforeState = await getPanelState(page);
    expect(beforeState.visible).toBe(true);
    assertPanelSafe(beforeState);

    // 触发 structural change（新增 sentence_analysis block）→
    // merger 检测拓扑变化（unit_block_set_changed）→ fallback_full_reload →
    // setValue。R3-R1: Surface 在 setValue 前捕获 interaction snapshot
    // （anchorSegmentId + markId + frozenRect），setValue 后 rAF 重新解析
    // anchor element 并 setPositionReference。原词汇 mark 仍存在 → 保持打开。
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const nextSnapshot = s.makeStructuralChangeSnapshot();
      const event = s.makeValidLayerPublishedEvent("sentence_analysis", 9);
      s.reloadWith(nextSnapshot, [event], {
        generation: 1,
        baseId: "base_1",
      });
    });

    // 等待 reload pipeline + rAF re-anchor 完成。
    await page.waitForTimeout(1000);

    // R3-R1: fallback_full_reload 路径，anchor 仍存在 → Quick Peek 保持打开。
    await expect(panel).toBeVisible({ timeout: 5_000 });

    // 采集 reload 后的浮层状态：非零尺寸、不在 (0,0)。
    const afterState = await getPanelState(page);
    expect(afterState.visible).toBe(true);
    assertPanelSafe(afterState);

    // 原词汇 mark 在新 DOM 中仍存在（re-anchor 目标）。
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
      ),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/p2c-fallback-full-reload-quick-peek-reanchored.png",
    });
  });
});
