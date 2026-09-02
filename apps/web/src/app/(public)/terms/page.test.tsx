/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/layout", () => ({
  PublicSiteHeader: ({ currentHref }: { currentHref?: string }) => (
    <header data-testid="public-site-header">
      <a href={currentHref ?? "/"}>Claread</a>
    </header>
  ),
}));

import TermsPage, { metadata } from "./page";

afterEach(cleanup);

describe("服务条款草案页", () => {
  it("通过阅读型文档结构呈现目录、草案标识和核心条款", async () => {
    render(await TermsPage());

    expect(screen.getByRole("article")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "服务条款" })).toBeTruthy();
    expect(screen.getByText("测试期草案")).toBeTruthy();
    expect(screen.getByText("非最终法律意见")).toBeTruthy();
    expect(screen.getByText("v0.1（测试期草案）")).toBeTruthy();
    expect(screen.getByText("2026 年 9 月 2 日")).toBeTruthy();

    const toc = screen.getByRole("navigation", { name: "文档目录" });
    expect(toc.querySelectorAll("a").length).toBeGreaterThan(1);
    expect(screen.getByRole("link", { name: "禁止行为" }).getAttribute("href")).toBe(
      "#prohibited-use",
    );

    const documentText = screen.getByRole("article").textContent ?? "";
    for (const topic of [
      "服务",
      "账号",
      "用户内容许可",
      "知识产权",
      "AI 与翻译结果限制",
      "第三方服务",
      "Beta 可用性",
      "终止",
      "免责声明",
      "责任限制",
      "争议与联系",
    ]) {
      expect(documentText).toContain(topic);
    }
  });

  it("声明搜索引擎不应索引测试期草案", () => {
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });
});
