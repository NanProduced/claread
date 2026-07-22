/**
 * T4.2a-PUX-R4-R3-R1 — Quick Peek Interaction Snapshot & Re-anchor E2E。
 *
 * 通过真实 ReaderRecordPlateSurface 驱动 fallback_full_reload / full-reload-
 * without-merger 路径，验证 R3-R1 Quick Peek 重新锚定与确定性关闭行为。
 *
 * 每个场景采样三个阶段的 panel 状态：
 *   - before:    触发 reload 前的浮层状态
 *   - domReplaced: DOM 替换后（setValue 已执行）、rAF 恢复窗口中的浮层状态
 *   - restored:  rAF 恢复完成后的浮层状态
 *
 * 关键不变式（所有阶段）：
 *   - 浮层不得以 detached (0,0) 状态残留（visible=true 且 rect.x===0 && rect.y===0）
 *   - 保持打开时 rect 必须非零尺寸
 *   - 关闭时不得残留可见浮层
 *
 * 边界：不修改 production Surface / polling / merger / backend / payload。
 * harness 位于 /e2e-plate-spike/surface（server-side env-gated），挂载真实
 * ReaderRecordPlateSurface。测试通过 `window.__spikeSurface` 驱动 reload pipeline。
 *
 * Frame-level 说明：
 *   浏览器 E2E 无法确定性地捕获单帧 (0,0) flash（rAF 与 Playwright evaluate
 *   round-trip 存在固有竞态）。本 spec 通过 MutationObserver 在 DOM 替换后
 *   尽快采样 domReplaced 状态，证明 frozen-rect 策略在恢复窗口维持非零位置。
 *   单帧级证据由 vitest（jsdom 同步采样）补充。
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
  visible: boolean;
  rect: PanelRect | null;
  /** visible=true 且 rect 位于 (0,0) — detached anchor 的典型特征。 */
  isDetachedAtOrigin: boolean;
}

/**
 * 采集 Quick Peek 浮层的当前可见状态与 boundingBox。
 *
 * visible=true 仅当面板存在于 DOM 中、未被 display:none / visibility:hidden
 * 隐藏、且 getBoundingClientRect 返回非零尺寸。
 * isDetachedAtOrigin=true 表示浮层以零尺寸或位于 (0,0) 的 detached 状态残留。
 */
async function getPanelState(page: Page): Promise<PanelState> {
  return page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>(
      '[data-testid="reader-record-plate-lookup-panel"]',
    );
    if (!panel) {
      return { visible: false, rect: null, isDetachedAtOrigin: false };
    }
    const rect = panel.getBoundingClientRect();
    const style = window.getComputedStyle(panel);
    const hasSize = rect.width > 0 && rect.height > 0;
    const isRendered =
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.opacity !== "0" &&
      hasSize;
    if (!isRendered) {
      return { visible: false, rect: null, isDetachedAtOrigin: false };
    }
    return {
      visible: true,
      rect: {
        x: rect.left,
        y: rect.top,
        width: rect.width,
        height: rect.height,
      },
      isDetachedAtOrigin: rect.left === 0 && rect.top === 0,
    };
  });
}

/**
 * 断言浮层状态安全：不得以 detached (0,0) 残留。
 * visible=true 时 rect 必须非零且不在 (0,0)。
 */
function assertNoDetachedPanel(state: PanelState, phase: string) {
  if (state.visible) {
    const rect = state.rect!;
    expect(rect.width, `[${phase}] width must be > 0`).toBeGreaterThan(0);
    expect(rect.height, `[${phase}] height must be > 0`).toBeGreaterThan(0);
    expect(
      state.isDetachedAtOrigin,
      `[${phase}] panel must not be detached at (0,0)`,
    ).toBe(false);
  }
}

/**
 * 在同一个浏览器执行上下文中设置 MutationObserver 并触发 reload，避免
 * trigger 与 observer 分两次 evaluate 导致的竞态（observer 在 DOM 变化
 * 之后才注册 → 永不触发）。
 *
 * triggerFn 是一个无参函数的字符串形式，在浏览器中通过 eval 执行。
 * Observer 在 trigger 之前注册，在 DOM 子树变化后 resolve。
 */
async function triggerAndAwaitDomMutation(
  page: Page,
  triggerFnBody: string,
  timeoutMs = 8000,
): Promise<void> {
  await page.evaluate(
    async ({ code, timeout }) => {
      const doc = document.querySelector(".reader-record-plate-document");
      if (!doc) {
        eval(code);
        return;
      }
      const mutationDone = new Promise<void>((resolve) => {
        const observer = new MutationObserver(() => {
          observer.disconnect();
          resolve();
        });
        observer.observe(doc, { childList: true, subtree: true });
        window.setTimeout(() => {
          observer.disconnect();
          resolve();
        }, timeout);
      });
      eval(code);
      await mutationDone;
    },
    { code: triggerFnBody, timeout: timeoutMs },
  );
}

/**
 * 在 seg_1 的 vocabulary mark（"memory"）上点击打开 Quick Peek。
 * 走 handleActivateVocabulary 路径，设置 quickPeekAnchorMarkIdRef = mark.id。
 */
async function openQuickPeekOnVocabMark(page: Page) {
  const vocabMark = page.locator(
    '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
  );
  await vocabMark.click();
  await expect(
    page.locator('[data-testid="reader-record-plate-lookup-panel"]'),
  ).toBeVisible({ timeout: 10_000 });
}

// ===========================================================================
// 1. fallback full reload（C6-class）→ anchor 仍存在 → Quick Peek 保持打开
// ===========================================================================

test.describe("1. fallback full reload 重新锚定 Quick Peek（anchor 仍存在）", () => {
  test("structural change 触发 fallback_full_reload → 保持打开、rect 非 (0,0)、指向原词", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const nextSnapshot = s.makeStructuralChangeSnapshot();
      const event = s.makeValidLayerPublishedEvent("sentence_analysis", 9);
      s.reloadWith(nextSnapshot, [event], { generation: 1, baseId: "base_1" });
    `);
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(true);
    assertNoDetachedPanel(restored, "restored");

    // 原词汇 mark 在新 DOM 中仍存在（re-anchor 目标）。
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
      ),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/r3-r1-fallback-reload-anchor-persists.png",
    });
  });
});

// ===========================================================================
// 2. full reload without merger（localUserAssets re-projection）→ 保持打开
// ===========================================================================

test.describe("2. full reload without merger（loadSnapshot）→ Quick Peek 保持打开", () => {
  test("loadSnapshot 触发 setValue（无 merger）→ anchor 仍存在 → 重新锚定", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // loadSnapshot 设置 snapshot 但不提供 reloadContext → value-swap effect
    // 走 !appliedViaTargeted 路径 → setValue（full reload without merger）。
    // makeUpdatedSnapshot 添加 user_asset，文档拓扑不变，vocab mark 仍存在。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const nextSnapshot = s.makeUpdatedSnapshot({
        userAssetNote: "re-projection note",
        assetSegmentId: "seg_1",
        assetId: "asset_highlight_1",
      });
      s.loadSnapshot(nextSnapshot);
    `);
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(true);
    assertNoDetachedPanel(restored, "restored");

    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
      ),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/r3-r1-full-reload-without-merger.png",
    });
  });
});

// ===========================================================================
// 3. sibling 更新（targeted_apply）→ Quick Peek 不变
// ===========================================================================

test.describe("3. sibling 更新 → Quick Peek 不变", () => {
  test("seg_2 grammar 修订（targeted_apply）→ seg_1 Quick Peek 保持不变", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");
    const beforeRect = before.rect!;

    // seg_2 grammar 修订 → merger targeted_apply 仅替换 seg_2 的 paragraph。
    // seg_1（Quick Peek 锚点）不受影响。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const nextSnapshot = s.makeSeg2GrammarRevisionSnapshot({
        grammarNote: "Revised seg_2 grammar note.",
      });
      const event = s.makeValidLayerPublishedEvent("grammar_note", 9);
      s.reloadWith(nextSnapshot, [event], { generation: 1, baseId: "base_1" });
    `);
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(true);
    assertNoDetachedPanel(restored, "restored");

    // targeted_apply 不触动 seg_1 → 浮层位置应基本不变（容差 5px 应对亚像素）。
    const restoredRect = restored.rect!;
    expect(Math.abs(restoredRect.x - beforeRect.x)).toBeLessThanOrEqual(5);
    expect(Math.abs(restoredRect.y - beforeRect.y)).toBeLessThanOrEqual(5);

    await page.screenshot({
      path: "test-results/r3-r1-sibling-update-unchanged.png",
    });
  });
});

// ===========================================================================
// 4. anchor segment 被删除 → Quick Peek 确定性关闭，无 detached panel
// ===========================================================================

test.describe("4. anchor segment 被删除 → Quick Peek 确定性关闭", () => {
  test("seg_1 被移除 → resolver 返回 null → 关闭，无 detached panel", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // 构造 seg_1 被完全移除的 snapshot（仅保留 seg_2）。
    // loadSnapshot 触发 full reload without merger → resolver 找不到
    // [data-anchor-segment-id="seg_1"] → 返回 null → fail-safe close。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      const next = JSON.parse(JSON.stringify(current));
      next.snapshot_id = "snapshot_seg1_removed";
      next.last_event_sequence = 9;
      const unit = next.value[0];
      const sourceBlock = unit.children.find(
        (c) => c.type === "reader_source_block",
      );
      sourceBlock.children = sourceBlock.children.filter(
        (seg) => seg.anchor_segment_id !== "seg_1",
      );
      s.loadSnapshot(next);
    `);
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(false);
    assertNoDetachedPanel(restored, "restored");

    // seg_1 确实不在新 DOM 中。
    await expect(
      page.locator('[data-anchor-segment-id="seg_1"]'),
    ).toHaveCount(0);

    await page.screenshot({
      path: "test-results/r3-r1-anchor-deleted-closed.png",
    });
  });
});

// ===========================================================================
// 5. generation 切换 → Quick Peek 确定性关闭
// ===========================================================================

test.describe("5. generation 切换 → Quick Peek 确定性关闭", () => {
  test("changeGeneration(2) → generation-scoped effect 关闭 Quick Peek", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      s.changeGeneration(2);
    `);

    // generation 切换触发 generation-scoped effect（关闭 Quick Peek）和
    // value-swap effect（setValue full reload）。等待 DOM 变化。
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(false);
    assertNoDetachedPanel(restored, "restored");

    await page.screenshot({
      path: "test-results/r3-r1-generation-switch-closed.png",
    });
  });
});

// ===========================================================================
// 6. resolver 失败（vocab mark 移除，segment 保留）→ 关闭，可信旧 UI 不破坏
// ===========================================================================

test.describe("6. resolver 失败 → Quick Peek 关闭，可信旧 UI 不破坏", () => {
  test("seg_1 vocab mark 移除（segment 保留）→ resolver 返回 null → 关闭，文档仍渲染", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 通过直接点击 vocab mark 打开 Quick Peek（设置 markId = "vocab_mark_1"）。
    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // 构造 seg_1 vocab marks 被清空的 snapshot（segment 文本保留）。
    // resolver 查找 [data-reader-record-vocabulary-mark-id="vocab_mark_1"]
    // → 不存在 → 返回 null → fail-safe close。
    // seg_1 文本仍渲染 → 可信旧 UI 不破坏。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      const next = JSON.parse(JSON.stringify(current));
      next.snapshot_id = "snapshot_vocab_mark_removed";
      next.last_event_sequence = 9;
      const unit = next.value[0];
      const sourceBlock = unit.children.find(
        (c) => c.type === "reader_source_block",
      );
      for (const seg of sourceBlock.children) {
        if (seg.anchor_segment_id === "seg_1") {
          for (const leaf of seg.children) {
            leaf.reader_vocabulary_marks = [];
          }
        }
      }
      s.loadSnapshot(next);
    `);
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(false);
    assertNoDetachedPanel(restored, "restored");

    // 可信旧 UI 不破坏：seg_1 paragraph 仍渲染、vocab mark 已移除。
    await expect(
      page.locator(
        '[data-reader-record-node="paragraph"][data-anchor-segment-id="seg_1"]',
      ),
    ).toBeVisible();
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
      ),
    ).toHaveCount(0);

    await page.screenshot({
      path: "test-results/r3-r1-resolver-failure-closed.png",
    });
  });
});

// ===========================================================================
// P1 helpers: 两词汇 mark fixture（seg_1 上 vocab_mark_1 + vocab_mark_2）
// ===========================================================================

/**
 * 加载 seg_1 上有两个 vocabulary mark 的 fixture：
 *   - vocab_mark_1: "memory" (offset 14-20)
 *   - vocab_mark_2: "shapes" (offset 21-27)
 *
 * 用于 P1-E2E-3（切换 mark）和 P1-E2E-5（删除原 mark 保留 sibling）。
 */
async function loadTwoVocabMarksFixture(page: Page) {
  await triggerAndAwaitDomMutation(page, `
    const s = window.__spikeSurface;
    const current = s.getSnapshot();
    const next = JSON.parse(JSON.stringify(current));
    next.snapshot_id = "snapshot_two_vocab_marks";
    next.last_event_sequence = 9;
    const unit = next.value[0];
    const sourceBlock = unit.children.find(
      (c) => c.type === "reader_source_block",
    );
    const seg1 = sourceBlock.children.find(
      (seg) => seg.anchor_segment_id === "seg_1",
    );
    const leaf = seg1.children[0];
    leaf.reader_vocabulary_marks = [
      leaf.reader_vocabulary_marks[0],
      {
        mark_id: "vocab_mark_2",
        layer_id: "layer_vocab_1",
        item_type: "phrase_gloss",
        anchor_segment_id: "seg_1",
        start_offset: 21,
        end_offset: 27,
        selected_text: "shapes",
        segment_start_utf16: 21,
        segment_end_utf16: 27,
        starts_here: true,
        ends_here: true,
        phrase: "shapes",
        phrase_type: "fixed_collocation",
        gloss: "塑造",
        example: "Institutional memory shapes choices.",
      },
    ];
    s.loadSnapshot(next);
  `);
  await page.waitForTimeout(300);
}

// ===========================================================================
// P1-E2E-1: 连续两次 snapshot 更新 → 第一次 restore 不得覆盖第二次
// ===========================================================================

test.describe("P1-E2E-1: 连续两次 snapshot 更新 → 第一次 restore 不得覆盖第二次", () => {
  test("两次 reload → 第二次 rAF 胜出，浮层保持打开且 rect 非零", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // 第一次 reload：structural change → fallback_full_reload
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const snap1 = s.makeStructuralChangeSnapshot();
      const event1 = s.makeValidLayerPublishedEvent("sentence_analysis", 9);
      s.reloadWith(snap1, [event1], { generation: 1, baseId: "base_1" });
    `);
    const domReplaced1 = await getPanelState(page);
    assertNoDetachedPanel(domReplaced1, "domReplaced1");

    // 第二次 reload：user_asset re-projection → full reload without merger
    // 第一次 rAF 的 cleanup 被取消，第二次 rAF 以新 token 胜出。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const snap2 = s.makeUpdatedSnapshot({
        userAssetNote: "second update re-projection",
        assetSegmentId: "seg_1",
        assetId: "asset_highlight_2",
      });
      s.loadSnapshot(snap2);
    `);
    const domReplaced2 = await getPanelState(page);
    assertNoDetachedPanel(domReplaced2, "domReplaced2");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(true);
    assertNoDetachedPanel(restored, "restored");

    // 原 vocab mark 在最终 DOM 中仍存在（第二次 reload 未移除）。
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
      ),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/p1-e2e-1-consecutive-reloads.png",
    });
  });
});

// ===========================================================================
// P1-E2E-2: restore pending 时 dismiss Quick Peek → 保持关闭
// ===========================================================================

test.describe("P1-E2E-2: restore pending 时 dismiss Quick Peek → 保持关闭", () => {
  test("reload 后点击关闭 → token 失效 → 浮层关闭，无 detached panel", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // 触发 reload（创建 pending restore，rAF 已调度但可能尚未执行）。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const snap = s.makeStructuralChangeSnapshot();
      const event = s.makeValidLayerPublishedEvent("sentence_analysis", 9);
      s.reloadWith(snap, [event], { generation: 1, baseId: "base_1" });
    `);
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    // 在 rAF 恢复窗口中点击关闭按钮 → lookupState.kind 变 idle →
    // effect 增 token → pending rAF 的 token 不匹配 → abort。
    await page.getByRole("button", { name: "关闭预览卡片" }).click();

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(false);
    assertNoDetachedPanel(restored, "restored");

    await page.screenshot({
      path: "test-results/p1-e2e-2-dismiss-during-restore.png",
    });
  });
});

// ===========================================================================
// P1-E2E-3: restore pending 时切换到同段另一 vocabulary mark → 锚定新 mark
// ===========================================================================

test.describe("P1-E2E-3: restore pending 时切换到同段另一 vocabulary mark", () => {
  test("reload 后点击 vocab_mark_2 → token 失效 → 浮层锚定新 mark", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 加载两词汇 mark fixture。
    await loadTwoVocabMarksFixture(page);
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_2"]',
      ),
    ).toBeVisible({ timeout: 10_000 });

    // 在 vocab_mark_1 上打开 Quick Peek。
    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // 触发 reload（pending restore 的 markId = vocab_mark_1）。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const snap = s.makeStructuralChangeSnapshot();
      const event = s.makeValidLayerPublishedEvent("sentence_analysis", 9);
      s.reloadWith(snap, [event], { generation: 1, baseId: "base_1" });
    `);
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    // 在 rAF 恢复窗口中点击 vocab_mark_2 → handleActivateVocabulary
    // 增 token、设置 markId = vocab_mark_2 → 旧 rAF 的 markId 不匹配 → abort。
    await page
      .locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_2"]',
      )
      .click();

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    // 浮层应保持打开（锚定到 vocab_mark_2），rect 非零。
    expect(restored.visible).toBe(true);
    assertNoDetachedPanel(restored, "restored");

    await page.screenshot({
      path: "test-results/p1-e2e-3-mark-switch-during-restore.png",
    });
  });
});

// ===========================================================================
// P1-E2E-4: restore pending 时 generation 切换 → token 失效 → 关闭
// ===========================================================================

test.describe("P1-E2E-4: restore pending 时 generation 切换 → 关闭", () => {
  test("reload 后 changeGeneration(2) → generation-scoped effect 关闭", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // 先触发 reload（创建 pending restore）。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const snap = s.makeStructuralChangeSnapshot();
      const event = s.makeValidLayerPublishedEvent("sentence_analysis", 9);
      s.reloadWith(snap, [event], { generation: 1, baseId: "base_1" });
    `);
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    // 立即切换 generation → generation-scoped effect 增 token、
    // 清 anchorRef、关闭 Quick Peek。pending rAF token 不匹配 → abort。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      s.changeGeneration(2);
    `);
    const afterGenSwitch = await getPanelState(page);
    assertNoDetachedPanel(afterGenSwitch, "afterGenSwitch");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(false);
    assertNoDetachedPanel(restored, "restored");

    await page.screenshot({
      path: "test-results/p1-e2e-4-gen-switch-during-restore.png",
    });
  });
});

// ===========================================================================
// P1-E2E-5: 原 mark 删除（同段 sibling 保留）→ resolver 精确定位 → 关闭
// ===========================================================================

test.describe("P1-E2E-5: 原 mark 删除（sibling 保留）→ resolver 精确定位 → 关闭", () => {
  test("vocab_mark_1 删除、vocab_mark_2 保留 → resolver 不命中 sibling → 关闭", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 加载两词汇 mark fixture。
    await loadTwoVocabMarksFixture(page);
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_2"]',
      ),
    ).toBeVisible({ timeout: 10_000 });

    // 在 vocab_mark_1 上打开 Quick Peek（markId = vocab_mark_1）。
    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // 构造仅保留 vocab_mark_2 的 snapshot（vocab_mark_1 被删除）。
    // resolver 查找 [data-reader-record-vocabulary-mark-id="vocab_mark_1"]
    // → 不存在 → 返回 null → fail-safe close。
    // vocab_mark_2 仍在 DOM 中 → 证明 resolver 未按 anchor_segment_id
    // 命中同段其他词汇 mark。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      const next = JSON.parse(JSON.stringify(current));
      next.snapshot_id = "snapshot_vocab_mark_1_deleted";
      next.last_event_sequence = 10;
      const unit = next.value[0];
      const sourceBlock = unit.children.find(
        (c) => c.type === "reader_source_block",
      );
      const seg1 = sourceBlock.children.find(
        (seg) => seg.anchor_segment_id === "seg_1",
      );
      const leaf = seg1.children[0];
      leaf.reader_vocabulary_marks = leaf.reader_vocabulary_marks.filter(
        (m) => m.mark_id !== "vocab_mark_1",
      );
      s.loadSnapshot(next);
    `);
    const domReplaced = await getPanelState(page);
    assertNoDetachedPanel(domReplaced, "domReplaced");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(false);
    assertNoDetachedPanel(restored, "restored");

    // vocab_mark_1 已从 DOM 移除。
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
      ),
    ).toHaveCount(0);

    // vocab_mark_2 仍在 DOM 中（resolver 未命中 sibling）。
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_2"]',
      ),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/p1-e2e-5-mark-deleted-sibling-kept.png",
    });
  });
});

// ===========================================================================
// P1.1-E2E-1: base_id 改变（generation 不变）→ Quick Peek 关闭，无 (0,0) panel
// ===========================================================================

test.describe("P1.1-E2E-1: base_id 改变 → Quick Peek 关闭，无 detached panel", () => {
  test("restore pending 时 base_id 从 base_1 变为 base_2 → 不跨 source identity 恢复", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // 第一次 reload：structural change → fallback_full_reload
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const snap1 = s.makeStructuralChangeSnapshot();
      const event1 = s.makeValidLayerPublishedEvent("sentence_analysis", 9);
      s.reloadWith(snap1, [event1], { generation: 1, baseId: "base_1" });
    `);
    const domReplaced1 = await getPanelState(page);
    assertNoDetachedPanel(domReplaced1, "domReplaced1");

    // 第二次 reload：base_id 改为 base_2（generation 不变），必须使
    // pending restore 失效并关闭 Quick Peek。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      const next = JSON.parse(JSON.stringify(current));
      next.snapshot_id = "snapshot_base_changed";
      next.last_event_sequence = 10;
      next.base = JSON.parse(JSON.stringify(next.base));
      next.base.base_id = "base_2";
      s.loadSnapshot(next);
    `);
    const domReplaced2 = await getPanelState(page);
    assertNoDetachedPanel(domReplaced2, "domReplaced2");

    await page.waitForTimeout(600);
    const restored = await getPanelState(page);
    expect(restored.visible).toBe(false);
    assertNoDetachedPanel(restored, "restored");

    // 原 vocab mark 在最终 DOM 中仍存在
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
      ),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/p1.1-e2e-1-base-change-during-restore.png",
    });
  });
});

// ===========================================================================
// P1.1-E2E-2: duplicate accepted snapshot（targeted_apply 后同 snapshot_id reload）→ Quick Peek 保持
// ===========================================================================

test.describe("P1.1-E2E-2: duplicate accepted snapshot early-return → Quick Peek 保持", () => {
  test("targeted_apply 后同 snapshot_id loadSnapshot → early-return → Quick Peek 不变", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");
    const beforeRect = before.rect!;

    // 第一次 reload：seg_2 grammar 修订 → targeted_apply（仅替换 seg_2）
    // seg_1 Quick Peek 不受影响，保持打开。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const snap1 = s.makeSeg2GrammarRevisionSnapshot({
        grammarNote: "Revised seg_2 grammar note for stale test.",
      });
      const event1 = s.makeValidLayerPublishedEvent("grammar_note", 9);
      s.reloadWith(snap1, [event1], { generation: 1, baseId: "base_1" });
    `);
    const afterTargeted = await getPanelState(page);
    assertNoDetachedPanel(afterTargeted, "afterTargeted");
    expect(afterTargeted.visible).toBe(true);

    // 第二次 reload：同一 snapshot_id（新对象引用）→
    // value-swap effect 检测 lastTargetedApplySnapshotIdRef === snapshot_id
    // → early-return → 不 capture、不 setValue、不 schedule rAF
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      // 创建新对象引用但保持同一 snapshot_id
      const sameSnapshot = JSON.parse(JSON.stringify(current));
      s.loadSnapshot(sameSnapshot);
    `);
    const afterDuplicateReload = await getPanelState(page);
    assertNoDetachedPanel(afterDuplicateReload, "afterDuplicateReload");

    await page.waitForTimeout(400);
    const restored = await getPanelState(page);
    // Quick Peek 仍然打开，位置基本不变（early-return 未触发 DOM 重建）
    expect(restored.visible).toBe(true);
    assertNoDetachedPanel(restored, "restored");

    const restoredRect = restored.rect!;
    expect(Math.abs(restoredRect.x - beforeRect.x)).toBeLessThanOrEqual(5);
    expect(Math.abs(restoredRect.y - beforeRect.y)).toBeLessThanOrEqual(5);

    await page.screenshot({
      path: "test-results/p1.1-e2e-2-stale-snapshot-preserves-quick-peek.png",
    });
  });
});
