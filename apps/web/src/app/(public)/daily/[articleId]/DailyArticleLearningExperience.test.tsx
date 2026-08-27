/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DailyReaderArticle } from "@/types/view/DailyReaderVm";

import { DailyArticleBody } from "./DailyArticleBody";

afterEach(cleanup);

function articleFixture(): DailyReaderArticle {
  return {
    id: "daily-test",
    title: "科技掌门人为何争相发表 AI 宣言",
    subtitle: null,
    originalTitle: "Why tech bosses keep sharing their manifestos about AI",
    subtitleZh: "从公开信到舆论竞争。",
    source: "BBC",
    sourceUrl: "https://example.com/article",
    publishDate: "2026-08-15",
    difficulty: "B2",
    articleType: "news_report",
    readTimeMinutes: 4,
    tags: ["科技"],
    coverImageUrl: null,
    coverTheme: "editorial_warm",
    mission: { reading: "理解科技领袖的叙事竞争。", objectives: ["识别论点结构"] },
    units: [
      {
        id: "u01",
        text: "Tech bosses keep sharing manifestos about AI.",
        translation: "科技掌门人不断分享关于 AI 的宣言。",
        isHighDifficulty: false,
      },
      {
        id: "u02",
        text: "The trend shows no sign of slowing.",
        translation: null,
        isHighDifficulty: true,
      },
    ],
    structureMap: [{ label: "现象", role: "引出宣言竞争现象", unitIds: ["u01", "u02"] }],
    languageTargets: [
      {
        expression: "show no sign of",
        unitId: "u02",
        targetKind: "idiom",
        teachingPurpose: "表达趋势延续",
        meaningZh: "没有……的迹象",
        usageNote: "常用于趋势判断。",
        reusablePattern: "show no sign of doing",
      },
    ],
    sentenceMaps: [
      {
        sentence: "The trend shows no sign of slowing.",
        unitId: "u02",
        translation: "这一趋势没有放缓的迹象。",
        complexityKind: "complex_syntax",
        teachingPurpose: "主谓宾简洁表达趋势。",
      },
    ],
    checkpoints: [
      {
        skill: "detail",
        prompt: "宣言竞争体现在哪些方面？",
        promptSubject: null,
        referenceAnswer: "科技领袖通过公开信争夺叙事。",
        answerSubject: null,
        evidenceUnitIds: ["u01"],
        answerEvidenceUnitIds: [],
      },
    ],
    transferTask: {
      taskKind: "retell",
      prompt: "用一段话复述宣言竞争的现象。",
      scaffold: "注意保持因果顺序。",
      referencePoints: ["宣言竞争", "趋势延续"],
      contentRequirement: "fact_chain",
    },
    postReadSummary: "宣言竞争是科技领袖争夺未来的方式。",
    translationCoverage: { translated: 1, total: 2 },
  };
}

describe("DailyArticleBody（v2 教学阅读体）", () => {
  it("渲染正文流全部单元，译文收在 details 中", () => {
    render(<DailyArticleBody article={articleFixture()} />);

    expect(screen.getByText("Tech bosses keep sharing manifestos about AI.")).toBeTruthy();
    expect(screen.getByText("The trend shows no sign of slowing.")).toBeTruthy();

    const translationText = screen.getByText("科技掌门人不断分享关于 AI 的宣言。");
    const details = translationText.closest("details");
    expect(details).not.toBeNull();
    expect(details!.open).toBe(false);

    // u02 无译文：details 总数 = 1 译文 + 1 长难句 + 1 自测
    expect(document.querySelectorAll("details").length).toBe(3);
  });

  it("按单元内联长难句精讲卡", () => {
    render(<DailyArticleBody article={articleFixture()} />);

    expect(screen.getByText("长难句精讲")).toBeTruthy();
    expect(screen.getByText("复杂句法")).toBeTruthy();
    expect(screen.getByText("这一趋势没有放缓的迹象。")).toBeTruthy();
  });

  it("渲染结构提纲 / 语言精讲 / 自测 / 迁移任务 / 收束", () => {
    render(<DailyArticleBody article={articleFixture()} />);

    expect(screen.getByText("文章结构")).toBeTruthy();
    expect(screen.getByText("现象")).toBeTruthy();

    expect(screen.getByText("语言精讲")).toBeTruthy();
    expect(screen.getByText("show no sign of")).toBeTruthy();
    expect(screen.getByText("可复用句型")).toBeTruthy();

    expect(screen.getByText("证据自测")).toBeTruthy();
    expect(screen.getByText((content) => content.startsWith("自测 01"))).toBeTruthy();
    expect(screen.getByText("参考答案")).toBeTruthy();

    expect(screen.getByText("迁移任务")).toBeTruthy();
    expect(screen.getByText("复述")).toBeTruthy();
    expect(screen.getByText("用一段话复述宣言竞争的现象。")).toBeTruthy();

    expect(screen.getByText("本篇收束")).toBeTruthy();
  });

  it("空教学字段时仅渲染正文流", () => {
    const bare = articleFixture();
    bare.structureMap = [];
    bare.languageTargets = [];
    bare.sentenceMaps = [];
    bare.checkpoints = [];
    bare.transferTask = null;
    bare.postReadSummary = null;

    render(<DailyArticleBody article={bare} />);

    expect(screen.getByText("Tech bosses keep sharing manifestos about AI.")).toBeTruthy();
    expect(screen.queryByText("文章结构")).toBeNull();
    expect(screen.queryByText("语言精讲")).toBeNull();
    expect(screen.queryByText("证据自测")).toBeNull();
    expect(screen.queryByText("迁移任务")).toBeNull();
    expect(screen.queryByText("本篇收束")).toBeNull();
  });
});
