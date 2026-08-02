/**
 * T4.2a-PUX-R4-R3-R2 — Selective Grammar Expansion & Semantic Scroll-anchor
 * Compensation E2E.
 *
 * 通过真实 ReaderRecordPlateSurface 驱动 same-source-identity full reload 路径，
 * 验证 R3-R2 两项渐进阅读收口：
 *   1. 同 source identity full reload 的 selective grammar forgetItem（保留幸存 item）
 *   2. 语义 scroll-anchor compensation（topVisibleBlockId + viewportOffset）
 *
 * 关键不变式：
 *   - 同 generation/base_id full reload 保留仍存在的 grammar expansion
 *   - source identity 切换仍 clear() 全部（R3-R1 合同，不变）
 *   - scroll-anchor compensation 在视口上方插入内容时维持 viewport offset
 *   - anchor resolver 失败时 fail-safe 到裸 scrollTop restore
 *   - Quick Peek frozen rect / rAF re-anchor（R3-R1）不受 scroll compensation 干扰
 *
 * 边界：不修改 production Surface / polling / merger / backend / payload。
 * harness 位于 /e2e-plate-spike/surface（server-side env-gated），挂载真实
 * ReaderRecordPlateSurface。测试通过 `window.__spikeSurface` 驱动 reload pipeline。
 *
 * Frame-level 说明：
 *   测试断言 FINAL DOM/state 在 reload pipeline 完成后的状态。单帧级 (0,0) flash
 *   证据由 vitest（jsdom 同步采样）补充，本 spec 通过 MutationObserver + rAF 等待
 *   尽可能捕获恢复窗口结束后的稳定状态。
 */

import { expect, test, type Page } from "@playwright/test";

const HARNESS_URL = "/e2e-plate-spike/surface";

test.beforeEach(() => {
  test.skip(
    true,
    "CUTOVER-WEB-LONG: Plate reload/scroll-anchor coverage is retained in ReaderRecordPlateSurface Vitest; this legacy harness suite awaits Physical deletion.",
  );
});

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
  await page.route("**/api/web/reader/records/*/favorite**", (route) => {
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

/**
 * 加载 tall fixture：在默认 fixture 基础上追加多个 sentence_analysis block，
 * 使文档高度超过 viewport，支持有意义的滚动测试。
 *
 * 额外的 block 使用唯一 analysis_id（analysis_tall_0 … analysis_tall_11），
 * 保证 block ID（sentence_analysis:analysis_tall_*）唯一且不与既有 block 冲突。
 */
async function loadTallFixture(page: Page) {
  await triggerAndAwaitDomMutation(page, `
    const s = window.__spikeSurface;
    const current = s.getSnapshot();
    const next = JSON.parse(JSON.stringify(current));
    next.snapshot_id = "snapshot_tall";
    next.last_event_sequence = 9;
    const unit = next.value[0];
    const SOURCE_TEXT_1 = "Institutional memory shapes policy choices.";
    const extraBlocks = [];
    for (let i = 0; i < 12; i++) {
      extraBlocks.push({
        type: "reader_sentence_analysis",
        owner: "system_ai",
        analysis_id: "analysis_tall_" + i,
        layer_id: "layer_sentence_analysis_tall",
        layer_version: 1,
        base_id: "base_1",
        unit_id: "unit_1",
        target_scope: "unit",
        target_key: "unit_1",
        anchor_segment_id: "seg_1",
        selected_text: SOURCE_TEXT_1,
        label: "tall filler " + i,
        analysis: "Filler analysis block number " + i + " for scroll testing.",
        chunks: [{ order: 1, label: "whole", text: SOURCE_TEXT_1 }],
        children: [{ text: "Filler analysis block number " + i + " for scroll testing." }],
      });
    }
    unit.children = unit.children.concat(extraBlocks);
    s.loadSnapshot(next);
  `);
  await page.waitForTimeout(500);
}

/**
 * 模拟 captureScrollAnchor 逻辑：找到第一个 getBoundingClientRect().bottom > 0
 * 的 [data-reader-record-block-id] 元素，返回其 blockId 与 viewportOffset。
 *
 * 同时返回 window.scrollY，用于 Scenario 3 的 bare scrollTop fallback 断言。
 */
async function captureScrollAnchor(page: Page): Promise<{
  blockId: string;
  viewportOffset: number;
  scrollY: number;
} | null> {
  return page.evaluate(() => {
    const blocks = document.querySelectorAll("[data-reader-record-block-id]");
    for (const block of blocks) {
      const rect = (block as HTMLElement).getBoundingClientRect();
      if (rect.bottom > 0) {
        const blockId = block.getAttribute("data-reader-record-block-id");
        if (blockId) {
          return {
            blockId,
            viewportOffset: rect.top,
            scrollY: window.scrollY,
          };
        }
      }
    }
    return null;
  });
}

/**
 * 查询指定 blockId 的当前 viewport offset（getBoundingClientRect().top）。
 * 返回 null 如果 block 不在新 DOM 中。
 */
async function getBlockViewportOffset(
  page: Page,
  blockId: string,
): Promise<number | null> {
  return page.evaluate((id) => {
    const el = document.querySelector(
      `[data-reader-record-block-id="${id}"]`,
    ) as HTMLElement | null;
    if (!el) return null;
    return el.getBoundingClientRect().top;
  }, blockId);
}

// ===========================================================================
// 1. 同 generation full reload 保留仍存在的 grammar expansion（selective forget）
// ===========================================================================
//
// 使用 reloadWith + makeStructuralChangeSnapshot + valid sentence_analysis event
// + same-generation fence 驱动 merger → fallback_full_reload → setValue 路径。
// grammar_item_1 在新 DOM 中仍存在 → expansion 保留。
// 这与 R2-1D test 3（reloadFallback 路径）互补，覆盖不同的 full-reload 入口。
// ===========================================================================

test.describe("1. Same-generation full reload preserves surviving grammar expansion", () => {
  test("structural change + valid layer_published → grammar_item_1 keeps expansion", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 展开 grammar_item_1 callout。
    const callout = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );
    await callout
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    // 同 generation full reload：structural change（新增 sentence_analysis block）
    // + valid sentence_analysis layer_published event + fence {generation:1, baseId:"base_1"}。
    // merger 检测到 block set 变化 → fallback_full_reload → setValue。
    // R3-R2 selective forget：grammar_item_1 在新 DOM 中仍存在 → expansion 保留。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const nextSnapshot = s.makeStructuralChangeSnapshot();
      const event = s.makeValidLayerPublishedEvent("sentence_analysis", 9);
      s.reloadWith(nextSnapshot, [event], { generation: 1, baseId: "base_1" });
    `);

    // 等待 flushSync 同步 commit + selective forget 完成。
    await page.waitForTimeout(500);

    // grammar_item_1 在新 DOM 中仍存在 → expansion 保留（collapsed=false）。
    const calloutAfter = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    await expect(calloutAfter).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    await page.screenshot({
      path: "test-results/r3-r2-grammar-expansion-preserved.png",
    });
  });
});

// ===========================================================================
// 2. Scroll-anchor compensation：视口上方插入内容时维持 viewport offset
// ===========================================================================
//
// 加载 tall fixture → 滚动到 paragraph:seg_2（使其成为第一个可见 block）
// → 记录其 viewport offset → loadSnapshot 一个 PREPEND sentence_analysis block
//   的新 snapshot（同 generation）→ rAF 中 scroll-anchor compensation 调整
//   scrollTop 使 paragraph:seg_2 保持在原 viewport offset。
// ===========================================================================

test.describe("2. Scroll-anchor compensation preserves viewport offset", () => {
  test("prepend content above viewport → captured block stays at same offset (±2px)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 加载 tall fixture 使文档高度超过 viewport。
    await loadTallFixture(page);

    // 滚动到 paragraph:seg_2 使其位于视口顶部。
    await page.evaluate(() => {
      const el = document.querySelector(
        '[data-reader-record-block-id="paragraph:seg_2"]',
      );
      if (el) {
        (el as HTMLElement).scrollIntoView({ block: "start" });
      }
    });
    await page.waitForTimeout(200);

    // 捕获当前 scroll anchor（第一个 bottom > 0 的 block）。
    const anchor = await captureScrollAnchor(page);
    expect(anchor, "scroll anchor must be captured").not.toBeNull();
    expect(anchor!.scrollY, "must have scrolled past top").toBeGreaterThan(0);

    // 触发 same-generation full reload：PREPEND 一个 sentence_analysis block
    // 到 unit children 头部，使所有既有内容下移。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      const next = JSON.parse(JSON.stringify(current));
      next.snapshot_id = "snapshot_prepend_above";
      next.last_event_sequence = 10;
      const unit = next.value[0];
      const SOURCE_TEXT_1 = "Institutional memory shapes policy choices.";
      const prependBlock = {
        type: "reader_sentence_analysis",
        owner: "system_ai",
        analysis_id: "analysis_prepended_above",
        layer_id: "layer_sentence_analysis_prepended",
        layer_version: 1,
        base_id: "base_1",
        unit_id: "unit_1",
        target_scope: "unit",
        target_key: "unit_1",
        anchor_segment_id: "seg_1",
        selected_text: SOURCE_TEXT_1,
        label: "prepended block",
        analysis: "This block was prepended above existing content to test scroll compensation.",
        chunks: [{ order: 1, label: "whole", text: SOURCE_TEXT_1 }],
        children: [{ text: "This block was prepended above existing content to test scroll compensation." }],
      };
      unit.children = [prependBlock].concat(unit.children);
      s.loadSnapshot(next);
    `);

    // 等待 flushSync 同步 commit + restore 完成。
    await page.waitForTimeout(300);

    // 断言捕获的 block 在新 DOM 中仍存在，且 viewport offset 在 ±2px 内。
    const newOffset = await getBlockViewportOffset(page, anchor!.blockId);
    expect(newOffset, `block ${anchor!.blockId} must exist in new DOM`).not.toBeNull();
    expect(
      Math.abs(newOffset! - anchor!.viewportOffset),
      `block viewport offset must be preserved within ±2px (before=${anchor!.viewportOffset}, after=${newOffset})`,
    ).toBeLessThanOrEqual(2);

    await page.screenshot({
      path: "test-results/r3-r2-scroll-anchor-compensation.png",
    });
  });
});

// ===========================================================================
// 3. Anchor resolver 失败 → fail-safe 到裸 scrollTop restore
// ===========================================================================
//
// 加载 tall fixture → 滚动到 paragraph:seg_2 → 记录 scrollY → loadSnapshot
// 一个移除 seg_2 的 snapshot（paragraph:seg_2 在新 DOM 中不存在）→ rAF 中
// resolver 无法找到 captured blockId → fail-safe 裸 scrollTop restore。
// 断言 scrollY ≈ savedScrollY（不跳到错误位置）。
// ===========================================================================

test.describe("3. Anchor resolver failure falls back to bare scrollTop", () => {
  test("captured block removed → resolver fails → scrollTop restored to saved value", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 加载 tall fixture 使文档高度超过 viewport。
    await loadTallFixture(page);

    // 滚动到 paragraph:seg_2 使其位于视口顶部。
    await page.evaluate(() => {
      const el = document.querySelector(
        '[data-reader-record-block-id="paragraph:seg_2"]',
      );
      if (el) {
        (el as HTMLElement).scrollIntoView({ block: "start" });
      }
    });
    await page.waitForTimeout(200);

    // 捕获 scroll anchor + scrollY。
    const anchor = await captureScrollAnchor(page);
    expect(anchor, "scroll anchor must be captured").not.toBeNull();
    expect(anchor!.scrollY, "must have scrolled past top").toBeGreaterThan(0);
    const savedScrollY = anchor!.scrollY;
    const capturedBlockId = anchor!.blockId;

    // 触发 same-generation full reload：移除 captured block 对应的内容。
    // captured blockId 形如 "paragraph:seg_X" 或 "sentence_analysis:analysis_tall_X"。
    // resolver 无法在新 DOM 中找到 captured blockId → fail-safe 裸 scrollTop restore。
    // 对于 sentence_analysis block，移除对应 analysis_id；对于 paragraph，移除对应 anchor_segment。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      const next = JSON.parse(JSON.stringify(current));
      next.snapshot_id = "snapshot_anchor_removed_scroll";
      next.last_event_sequence = 10;
      const unit = next.value[0];
      const capturedBlockId = ${JSON.stringify(capturedBlockId)};
      if (capturedBlockId.startsWith("paragraph:")) {
        const segId = capturedBlockId.slice("paragraph:".length);
        const sourceBlock = unit.children.find(
          (c) => c.type === "reader_source_block",
        );
        if (sourceBlock) {
          sourceBlock.children = sourceBlock.children.filter(
            (seg) => seg.anchor_segment_id !== segId,
          );
        }
      } else if (capturedBlockId.startsWith("sentence_analysis:")) {
        const analysisId = capturedBlockId.slice("sentence_analysis:".length);
        unit.children = unit.children.filter(
          (c) => c.analysis_id !== analysisId,
        );
      }
      s.loadSnapshot(next);
    `);

    // 等待 rAF polling 检测到 DOM 变化 + fail-safe restore 完成。
    await page.waitForTimeout(300);

    // 断言 captured block 在新 DOM 中不存在（resolver 必然失败）。
    const blockExists = await page.evaluate((id) => {
      return document.querySelector(
        '[data-reader-record-block-id="' + id + '"]',
      ) !== null;
    }, capturedBlockId);
    expect(blockExists, `block ${capturedBlockId} must be removed from new DOM`).toBe(false);

    // 断言 fail-safe 裸 scrollTop restore：scrollY 不跳到错误位置（如 0/top）。
    // 当 captured block 被移除时，文档高度缩短，浏览器会将 scrollTop 钳制到新的
    // 最大可滚动高度。因此断言 scrollY 不超过 savedScrollY（未向下跳），且显著大于 0
    // （未跳到顶部）。
    const newScrollY = await page.evaluate(() => window.scrollY);
    expect(newScrollY, `scrollY must not jump to top (saved=${savedScrollY}, actual=${newScrollY})`).toBeGreaterThan(savedScrollY * 0.5);
    expect(newScrollY, `scrollY must not exceed saved (saved=${savedScrollY}, actual=${newScrollY})`).toBeLessThanOrEqual(savedScrollY);

    await page.screenshot({
      path: "test-results/r3-r2-anchor-resolver-failure-fallback.png",
    });
  });
});

// ===========================================================================
// 4. Source identity 切换 → clear() 全部 grammar expansion（R3-R1 合同）
// ===========================================================================
//
// 加载 multi-anchor grammar snapshot（grammar_item_1 + grammar_item_2）
// → 展开两个 callout → 触发 generation 或 base_id 切换 →
// generation-scoped effect 调用 clear() → 全部折叠。
// 不执行 selective forget（clear 优先于 setValue 前的 capture）。
// ===========================================================================

test.describe("4. Source identity switch clears all grammar expansion", () => {
  test("generation change → all grammar callouts collapsed (clear, not selective forget)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 加载 multi-anchor grammar snapshot（seg_1 + seg_2 各有 grammar mark）。
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      s.loadSnapshot(s.makeMultiAnchorGrammarSnapshot());
    });
    await page.waitForTimeout(500);

    // 展开两个 grammar callout。
    const calloutA = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const calloutB = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_2"]',
    );
    await calloutA
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });
    await calloutB
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });
    await expect(calloutA).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );
    await expect(calloutB).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    // 触发 generation 切换 → generation-scoped effect 调用 clear()。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      s.changeGeneration(2);
    `);

    await page.waitForTimeout(1000);

    // 两个 callout 都折叠（clear 清理全部，不是 selective forget）。
    const calloutAAfter = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const calloutBAfter = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_2"]',
    );
    await expect(calloutAAfter).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );
    await expect(calloutBAfter).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );

    await page.screenshot({
      path: "test-results/r3-r2-generation-switch-clears-all.png",
    });
  });

  test("base_id change → all grammar callouts collapsed (clear, not selective forget)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 加载 multi-anchor grammar snapshot。
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      s.loadSnapshot(s.makeMultiAnchorGrammarSnapshot());
    });
    await page.waitForTimeout(500);

    // 展开两个 grammar callout。
    const calloutA = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const calloutB = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_2"]',
    );
    await calloutA
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });
    await calloutB
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });
    await expect(calloutA).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );
    await expect(calloutB).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    // 触发 base_id 切换（generation 不变）→ source-identity effect 调用 clear()。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      const next = JSON.parse(JSON.stringify(current));
      next.snapshot_id = "snapshot_base_id_changed";
      next.last_event_sequence = 10;
      next.base = JSON.parse(JSON.stringify(next.base));
      next.base.base_id = "base_2";
      s.loadSnapshot(next);
    `);

    await page.waitForTimeout(1000);

    // 两个 callout 都折叠（clear 清理全部）。
    const calloutAAfter = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const calloutBAfter = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_2"]',
    );
    await expect(calloutAAfter).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );
    await expect(calloutBAfter).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );

    await page.screenshot({
      path: "test-results/r3-r2-base-id-switch-clears-all.png",
    });
  });
});

// ===========================================================================
// 5. Quick Peek 打开时 scroll compensation 正常（两者不互相干扰）
// ===========================================================================
//
// 加载 tall fixture → 打开 Quick Peek（vocab_mark_1）→ 滚动到 paragraph:seg_2
// → 触发 same-generation full reload（prepend content）→ rAF 中：
//   - Quick Peek frozen rect / rAF re-anchor 不受干扰（无 (0,0) panel）
//   - scroll-anchor compensation 独立执行（viewport offset 保持）
// 两者在同一 rAF 回调中协同执行，不修改对方的 ref / token。
// ===========================================================================

test.describe("5. Quick Peek re-anchor coexists with scroll compensation", () => {
  test("Quick Peek open + same-generation reload → both re-anchor and scroll compensation work", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 加载 tall fixture。
    await loadTallFixture(page);

    // 打开 Quick Peek。
    await openQuickPeekOnVocabMark(page);

    const before = await getPanelState(page);
    expect(before.visible).toBe(true);
    assertNoDetachedPanel(before, "before");

    // 滚动到 paragraph:seg_2（使 savedScrollTop > 0 触发 scroll compensation）。
    await page.evaluate(() => {
      const el = document.querySelector(
        '[data-reader-record-block-id="paragraph:seg_2"]',
      );
      if (el) {
        (el as HTMLElement).scrollIntoView({ block: "start" });
      }
    });
    await page.waitForTimeout(200);

    // 捕获 scroll anchor。
    const anchor = await captureScrollAnchor(page);
    expect(anchor, "scroll anchor must be captured").not.toBeNull();
    expect(anchor!.scrollY, "must have scrolled past top").toBeGreaterThan(0);

    // 触发 same-generation full reload：prepend content above viewport。
    // Quick Peek re-anchor 和 scroll compensation 在同一 rAF 中协同执行。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      const next = JSON.parse(JSON.stringify(current));
      next.snapshot_id = "snapshot_prepend_with_quick_peek";
      next.last_event_sequence = 10;
      const unit = next.value[0];
      const SOURCE_TEXT_1 = "Institutional memory shapes policy choices.";
      const prependBlock = {
        type: "reader_sentence_analysis",
        owner: "system_ai",
        analysis_id: "analysis_prepended_with_quick_peek",
        layer_id: "layer_sentence_analysis_prepended_qp",
        layer_version: 1,
        base_id: "base_1",
        unit_id: "unit_1",
        target_scope: "unit",
        target_key: "unit_1",
        anchor_segment_id: "seg_1",
        selected_text: SOURCE_TEXT_1,
        label: "prepended block with quick peek",
        analysis: "This block was prepended to test Quick Peek and scroll compensation coexistence.",
        chunks: [{ order: 1, label: "whole", text: SOURCE_TEXT_1 }],
        children: [{ text: "This block was prepended to test Quick Peek and scroll compensation coexistence." }],
      };
      unit.children = [prependBlock].concat(unit.children);
      s.loadSnapshot(next);
    `);

    // 等待 flushSync 同步 commit + restore 完成。
    await page.waitForTimeout(300);

    // Quick Peek：无 detached (0,0) panel（frozen rect / rAF re-anchor 不受干扰）。
    const restored = await getPanelState(page);
    assertNoDetachedPanel(restored, "restored");

    // Scroll compensation：捕获的 block viewport offset 保持（±2px）。
    const newOffset = await getBlockViewportOffset(page, anchor!.blockId);
    expect(newOffset, `block ${anchor!.blockId} must exist in new DOM`).not.toBeNull();
    expect(
      Math.abs(newOffset! - anchor!.viewportOffset),
      `scroll compensation must preserve viewport offset within ±2px (before=${anchor!.viewportOffset}, after=${newOffset})`,
    ).toBeLessThanOrEqual(2);

    // 原 vocab mark 在新 DOM 中仍存在（re-anchor 目标）。
    await expect(
      page.locator(
        '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
      ),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/r3-r2-quick-peek-scroll-coexistence.png",
    });
  });
});

// ===========================================================================
// 6. T4.2a-PUX-R4-R3-R2-P1 (Contract B + C): base_id change with Quick Peek
//    open + non-zero scroll → no cross-source scroll-anchor restore, no
//    detached (0,0) Quick Peek panel.
// ===========================================================================
//
// P1 修复点：
//   - Contract C: generation-scoped effect increments restoreTokenRef →
//     旧 rAF/timeout (from pre-base_id-change value swap) abort on token
//     mismatch, cannot consume new pending or fire against new DOM.
//   - Contract B: 即使旧 rAF 未被 token 拦截, runRestore 中的
//     sourceIdentity check (generation + baseId) 也会阻止跨 source 的
//     scroll-anchor / savedScrollTop 恢复。
//
// 可观测不变式：
//   1. Quick Peek 关闭 (generation-scoped effect 清空 inspectState), 不残留
//      detached (0,0) panel。
//   2. 浏览器 console 无 "Cannot read properties of null" 或 scroll restore
//      相关的 JavaScript 错误 — 旧 rAF 干净 abort。
//   3. 页面不崩溃, plate document 仍可交互。
// ===========================================================================

test.describe("6. (P1) base_id change with Quick Peek + scroll → no detached panel, no cross-source restore", () => {
  test("base_id switch → Quick Peek closed, no (0,0) panel, no console errors", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text());
      }
    });

    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // 加载 tall fixture 使文档可滚动。
    await loadTallFixture(page);

    // 滚动到非零位置 (savedScrollTop > 0 触发 scroll compensation 路径)。
    await page.evaluate(() => {
      const el = document.querySelector(
        '[data-reader-record-block-id="paragraph:seg_2"]',
      );
      if (el) {
        (el as HTMLElement).scrollIntoView({ block: "start" });
      }
    });
    await page.waitForTimeout(200);

    const scrollBeforeBaseIdChange = await page.evaluate(() => window.scrollY);
    expect(
      scrollBeforeBaseIdChange,
      "must have scrolled past top before base_id change",
    ).toBeGreaterThan(0);

    // 打开 Quick Peek。
    await openQuickPeekOnVocabMark(page);

    const panelBefore = await getPanelState(page);
    expect(panelBefore.visible, "Quick Peek must be open before base_id change").toBe(true);
    assertNoDetachedPanel(panelBefore, "before base_id change");

    // 触发 base_id 切换 (generation 不变, base_id: base_1 → base_2)。
    // generation-scoped effect 应: clear grammar expansion, close Quick Peek,
    // increment restoreTokenRef (Contract C), null pendingRestoreRef。
    // 旧 rAF (from any pending restore) 应 token mismatch → abort。
    await triggerAndAwaitDomMutation(page, `
      const s = window.__spikeSurface;
      const current = s.getSnapshot();
      const next = JSON.parse(JSON.stringify(current));
      next.snapshot_id = "snapshot_p1_base_id_switch_with_qp";
      next.last_event_sequence = 11;
      next.base = JSON.parse(JSON.stringify(next.base));
      next.base.base_id = "base_2";
      s.loadSnapshot(next);
    `);

    // 等待 generation-scoped effect + value swap + rAF/timeout 全部完成。
    await page.waitForTimeout(500);

    // 不变式 1: Quick Peek 已关闭 (generation-scoped effect 清空 inspectState)。
    const panelAfter = await getPanelState(page);
    expect(
      panelAfter.visible,
      "Quick Peek must be closed after base_id change (generation-scoped effect)",
    ).toBe(false);

    // 不变式 2: 无 detached (0,0) panel 残留。
    assertNoDetachedPanel(panelAfter, "after base_id change");

    // 不变式 3: 浏览器 console 无 JavaScript 错误 (旧 rAF 干净 abort,
    // 不尝试访问旧 source 的 block DOM)。
    const criticalErrors = consoleErrors.filter(
      (e) =>
        e.includes("Cannot read properties of null") ||
        e.includes("is not a function") ||
        e.includes("TypeError"),
    );
    expect(
      criticalErrors,
      `no critical JS errors from stale rAF consuming new pending: ${JSON.stringify(consoleErrors)}`,
    ).toHaveLength(0);

    // 不变式 4: plate document 仍可交互 (页面未崩溃)。
    await expect(
      page.locator(".reader-record-plate-document"),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/r3-r2-p1-base-id-switch-no-detached-panel.png",
    });
  });
});
