/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DailyReaderArticle } from "@/types/view/DailyReaderVm";

import { DailyArticleBody } from "./DailyArticleBody";
import { ReadingNoteExpander, TranslationExpander } from "./EditorialExpanders";

afterEach(cleanup);

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
    const text = "Tech bosses keep sharing manifestos about AI.";
    const start = text.indexOf("manifestos");
    const article: DailyReaderArticle = {
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

    render(<DailyArticleBody article={article} />);

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
});
