/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DailyReaderArticle } from "@/types/view/DailyReaderVm";

import { DailyArticleBody } from "./DailyArticleBody";
import { ReadingNoteExpander, TranslationExpander } from "./EditorialExpanders";

beforeEach(() => {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    },
  });
});

afterEach(cleanup);

function articleFixture(): DailyReaderArticle {
  const text = "Tech bosses keep sharing manifestos about AI.";
  const start = text.indexOf("manifestos");

  return {
    id: "daily-test",
    title: "科技掌门人为何争相发表 AI 宣言",
    subtitle: null,
    originalTitle: "Why tech bosses keep sharing their manifestos about AI",
    subtitleZh: "从公开信到舆论竞争，科技领袖正在争夺未来叙事。",
    source: "BBC",
    sourceUrl: "https://example.com/article",
    publishDate: "2026-08-15",
    difficulty: "C1",
    readTimeMinutes: 4,
    tags: ["人工智能"],
    coverImageUrl: null,
    coverTheme: "editorial_warm",
    body: {
      paragraphs: [
        {
          id: "p_0",
          text,
          translation: "科技领袖不断分享他们关于人工智能的宣言。",
          readingNote: {
            focusQuestion: "作者为何把公开信称为宣言？",
            microSummary: "作者借宣言一词强调科技领袖正在争夺公共叙事。",
          },
          highlights: [
            {
              id: "hl_0",
              type: "phrase_gloss",
              text: "manifestos",
              gloss: "公开表达立场的宣言",
              paragraphId: "p_0",
              start,
              end: start + "manifestos".length,
              detail: { pos: "n.", contextExplanation: "这里强调公开争夺叙事的意味。" },
            },
          ],
        },
      ],
    },
    highlights: [],
    footerAnalysis: { summary: "", keyExpressions: [], discussionQuestions: [] },
  };
}

describe("Daily Reader learning controls", () => {
  it("uses Chinese, explicit labels for the reading-note control", () => {
    render(
      <ReadingNoteExpander
        note={{
          focusQuestion: "作者为什么先提出反方观点？",
          microSummary: "作者先呈现争议，再引出自己的判断。",
        }}
        paragraphNumber={3}
      />,
    );

    const trigger = screen.getByRole("button", { name: "展开第 3 段导读" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(trigger);

    expect(
      screen.getByRole("button", { name: "收起第 3 段导读" }).getAttribute("aria-expanded"),
    ).toBe("true");
    expect(screen.getByText("作者先呈现争议，再引出自己的判断。")).toBeTruthy();
  });

  it("uses Chinese labels for the paragraph translation control", () => {
    render(<TranslationExpander translation="科技公司正在争夺叙事的主导权。" paragraphNumber={3} />);

    const trigger = screen.getByRole("button", { name: "显示第 3 段译文" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(trigger);

    expect(
      screen.getByRole("button", { name: "收起第 3 段译文" }).getAttribute("aria-expanded"),
    ).toBe("true");
    expect(screen.getByText("科技公司正在争夺叙事的主导权。")).toBeTruthy();
  });
});

describe("Daily Reader annotations", () => {
  it("opens a paragraph-anchored note that repeats the selected expression", () => {
    render(<DailyArticleBody article={articleFixture()} />);

    const paragraphNumber = screen.getByText("01");
    expect(paragraphNumber.className).not.toContain("hidden");
    expect(paragraphNumber.className).toContain("md:absolute");

    const trigger = screen.getByRole("button", { name: "查看“manifestos”注释" });
    fireEvent.click(trigger);

    const note = screen.getByRole("complementary", { name: "manifestos 注释" });
    expect(trigger.getAttribute("aria-controls")).toBe(note.id);
    expect(within(note).getByText("manifestos")).toBeTruthy();
    expect(within(note).getByText("公开表达立场的宣言")).toBeTruthy();
    expect(within(note).getByText("这里强调公开争夺叙事的意味。")).toBeTruthy();
  });

  it("persists learning mode and expands every guide and translation", async () => {
    window.localStorage.clear();
    const first = render(<DailyArticleBody article={articleFixture()} />);

    const modeSwitch = screen.getByRole("switch", { name: "学习模式" });
    expect(modeSwitch.getAttribute("aria-checked")).toBe("false");

    fireEvent.click(modeSwitch);

    await waitFor(() => {
      expect(modeSwitch.getAttribute("aria-checked")).toBe("true");
      expect(screen.getByText("学习模式：默认展开导读与译文")).toBeTruthy();
      expect(
        screen.getByRole("button", { name: "收起第 1 段导读" }).getAttribute("aria-expanded"),
      ).toBe("true");
      expect(
        screen.getByRole("button", { name: "收起第 1 段译文" }).getAttribute("aria-expanded"),
      ).toBe("true");
    });

    first.unmount();
    render(<DailyArticleBody article={articleFixture()} />);

    await waitFor(() => {
      expect(screen.getByRole("switch", { name: "学习模式" }).getAttribute("aria-checked")).toBe(
        "true",
      );
    });
  });
});
