/** @vitest-environment jsdom */

"use client";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { appReadingRecordRoute, legacyAppReaderRoute } from "@/lib/routes";
import type { VocabularyItemVm } from "@/types/view/VocabularyItemVm";
import type { VocabularySourceRefDto } from "@/types/api/vocabulary";

import { resolveReaderSourceHref, VocabularyClient } from "./VocabularyClient";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

function installMatchMedia(matches = true) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function makeVocabularyItem(
  overrides: Partial<VocabularyItemVm> = {},
): VocabularyItemVm {
  return {
    id: "vocab_1",
    word: "memory",
    lookupKind: "word",
    lemma: "memory",
    phonetic: "/mem/",
    partOfSpeech: "noun",
    shortMeaning: "记忆",
    contextSentence: "Institutional memory shapes choices.",
    contextTranslation: "制度记忆会塑造选择。",
    sourceReadingRecordId: undefined,
    sourceRecordId: "legacy record 1",
    sourceRecordTitle: "Legacy Article",
    createdAt: "2026-06-22T00:00:00.000Z",
    updatedAt: "2026-06-22T00:00:00.000Z",
    mastered: false,
    masteryStatus: "learning",
    reviewCount: 0,
    tags: [],
    nextReviewAt: undefined,
    reviewStage: undefined,
    lastReviewedAt: undefined,
    sourceRefs: [],
    collectedForms: [],
    dictEntryId: null,
    audioUrl: undefined,
    detailMeanings: undefined,
    detailPhrases: undefined,
    detailExamples: undefined,
    totalSourceCount: 1,
    totalSourceArticleCount: 1,
    ...overrides,
  };
}

beforeEach(() => {
  installMatchMedia(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("VocabularyClient source links", () => {
  it("prefers sourceReadingRecordId when a Reading Record source exists", () => {
    // makeVocabularyItem defaults sourceRecordId to "legacy record 1", so
    // this fixture intentionally carries both ids at once. The contract:
    // when both ids are present the new Reading Record chain wins and the
    // legacy path must NOT leak into the rendered href.
    const item = makeVocabularyItem({
      sourceReadingRecordId: "reading_record_1",
    });

    render(
      <VocabularyClient
        items={[item]}
        status="ready"
        dueCount={0}
        learningCount={1}
        masteredCount={0}
        recentItems={[item]}
        multiContextItems={[]}
      />,
    );

    const sourceLink = screen.getByRole("link", { name: "查看来源语境" });
    const href = sourceLink.getAttribute("href");

    expect(href).toBe(appReadingRecordRoute("reading_record_1"));
    // Explicit guard: even with sourceRecordId present, the legacy path
    // segment must not appear in the rendered href.
    expect(href).not.toContain("/app/reader/");
    expect(href).not.toBe(legacyAppReaderRoute("legacy record 1"));
  });

  it("falls back to the legacy reader route when only sourceRecordId exists", () => {
    const item = makeVocabularyItem();

    render(
      <VocabularyClient
        items={[item]}
        status="ready"
        dueCount={0}
        learningCount={1}
        masteredCount={0}
        recentItems={[item]}
        multiContextItems={[]}
      />,
    );

    const sourceLink = screen.getByRole("link", { name: "查看来源语境" });

    expect(sourceLink.getAttribute("href")).toBe(
      legacyAppReaderRoute("legacy record 1"),
    );
  });

  it("renders no source link when both sourceReadingRecordId and sourceRecordId are missing", () => {
    const item = makeVocabularyItem({
      sourceReadingRecordId: undefined,
      sourceRecordId: undefined,
    });

    render(
      <VocabularyClient
        items={[item]}
        status="ready"
        dueCount={0}
        learningCount={1}
        masteredCount={0}
        recentItems={[item]}
        multiContextItems={[]}
      />,
    );

    // sourceHrefForItem returns null when neither id is present, so the
    // "查看来源语境" link must not be rendered at all.
    expect(screen.queryByRole("link", { name: "查看来源语境" })).toBeNull();
  });
});

describe("VocabularyClient - reader entry priority from source refs", () => {
  // jsdom 下 window.location.href 赋值不可直接观察，因此把 route 决策抽到
  // resolveReaderSourceHref 纯函数中独立测试。同时通过 href setter spy 做
  // 集成验证，确认点击 "在原文中定位" 按钮真的会触发新链 URL。
  describe("resolveReaderSourceHref - pure decision", () => {
    it("returns the new Reading Record URL when both readingRecordId and recordId are present", () => {
      // 单条 source ref 同时携带 reading_record_id 和 cloud_record_id 时，
      // VocabularyDetailPanel.handleGoToRef 会同时上抛 readingRecordId 和
      // recordId。resolveReaderSourceHref 必须优先 readingRecordId（新链），
      // 且绝不生成旧链 URL。
      const url = resolveReaderSourceHref({
        readingRecordId: "reading_record_42",
        recordId: "legacy_record_99",
      });

      expect(url).toBe(appReadingRecordRoute("reading_record_42"));
      // 显式护栏：双 id 同时存在时，绝不为旧链 URL。
      expect(url).not.toBe(legacyAppReaderRoute("legacy_record_99"));
      expect(url).not.toContain("/app/reader/");
    });

    it("returns the legacy route only when readingRecordId is absent", () => {
      const url = resolveReaderSourceHref({
        readingRecordId: null,
        recordId: "legacy_record_99",
      });

      expect(url).toBe(legacyAppReaderRoute("legacy_record_99"));
    });

    it("returns null when neither id is present", () => {
      const url = resolveReaderSourceHref({
        readingRecordId: null,
        recordId: null,
      });

      expect(url).toBeNull();
    });

    it("appends sentenceId as query when present", () => {
      const url = resolveReaderSourceHref({
        readingRecordId: "reading_record_42",
        recordId: "legacy_record_99",
        sentenceId: "sentence_7",
      });

      expect(url).toBe(
        `${appReadingRecordRoute("reading_record_42")}?sentenceId=sentence_7`,
      );
      // 绝不混入旧链路径段
      expect(url).not.toContain("/app/reader/");
    });
  });

  describe("clicking '在原文中定位' on a ref carrying both ids", () => {
    let hrefSetter: ReturnType<typeof vi.fn<(v: string) => void>>;
    let originalLocationDescriptor: PropertyDescriptor | undefined;

    beforeEach(() => {
      // jsdom 的 Location 实例上 href 属性是 non-configurable 的，无法直接
      // redefine；而直接替换整个 window.location 会破坏 Next.js Image 等
      // 组件内部依赖 Location 原型方法的 URL 解析。
      //
      // 这里用 Proxy 包装真实 Location 对象：所有属性/方法透传到原始实例
      // （保留 origin/hostname/pathname/assign/reload 等用于 URL 解析），
      // 仅拦截 href 的 set 操作以捕获 handleGoToSource 传入的最终 URL。
      hrefSetter = vi.fn<(v: string) => void>();
      const realLocation = window.location;
      const proxiedLocation = new Proxy(realLocation, {
        // 不拦截 get：让 Next.js Image 等组件读取 href/origin/hostname 等
        // 属性时拿到真实 Location 值，避免 `new URL(src, "")` 抛 Invalid URL。
        set(target, prop, value, receiver) {
          if (prop === "href") {
            hrefSetter(value);
            return true;
          }
          return Reflect.set(target, prop, value, receiver);
        },
      });
      originalLocationDescriptor = Object.getOwnPropertyDescriptor(
        window,
        "location",
      );
      Object.defineProperty(window, "location", {
        configurable: true,
        value: proxiedLocation,
      });
    });

    afterEach(() => {
      // 恢复原始 window.location 描述符，避免污染后续测试。
      if (originalLocationDescriptor) {
        Object.defineProperty(window, "location", originalLocationDescriptor);
      }
    });

    it("sets window.location.href to the new Reading Record route, never the legacy route", () => {
      // 构造一条同时带 reading_record_id 和 cloud_record_id 的 source ref。
      // 这是用户要求的最关键护栏场景：双 id 同时存在时，最终目标必须是
      // /app/reader-record/{reading_record_id}，且绝不为
      // /app/reader/{cloud_record_id}。
      const dualIdRef: VocabularySourceRefDto = {
        reading_record_id: "reading_record_42",
        cloud_record_id: "legacy_record_99",
        source_sentence: "Institutional memory shapes choices.",
      };
      const item = makeVocabularyItem({
        sourceReadingRecordId: undefined,
        sourceRecordId: undefined,
        sourceRefs: [dualIdRef],
      });

      render(
        <VocabularyClient
          items={[item]}
          status="ready"
          dueCount={0}
          learningCount={1}
          masteredCount={0}
          recentItems={[item]}
          multiContextItems={[]}
        />,
      );

      // 选中列表项让 VocabularyDetailPanel 渲染。列表项有 role="option"，
      // 用 getByRole 精确定位，避免 getByText 匹配到详情面板同名校验文本。
      fireEvent.click(screen.getByRole("option"));

      // VocabularyDetailPanel 的 "在原文中定位" 按钮使用 text-lens-blue 类名
      // 作为可识别标记。Radix Tooltip 把按钮渲染为常规 <button>，jsdom 下
      // 不依赖 hover，可以直接通过 className word-match 选择器精确定位。
      const locateButton = document.querySelector<HTMLButtonElement>(
        'button[class~="text-lens-blue"]',
      );
      expect(locateButton).not.toBeNull();

      fireEvent.click(locateButton!);

      // 最终 URL 必须是新链，且绝不能是旧链。
      expect(hrefSetter).toHaveBeenCalledTimes(1);
      expect(hrefSetter).toHaveBeenCalledWith(
        appReadingRecordRoute("reading_record_42"),
      );
      expect(hrefSetter).not.toHaveBeenCalledWith(
        legacyAppReaderRoute("legacy_record_99"),
      );
    });
  });
});
