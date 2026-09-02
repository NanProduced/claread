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

import PrivacyPage, { metadata } from "./page";

afterEach(cleanup);

describe("隐私政策草案页", () => {
  it("说明当前代码涉及的数据、处理方和用户权利", async () => {
    render(await PrivacyPage());

    expect(screen.getByRole("article")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "隐私政策" })).toBeTruthy();
    expect(screen.getByText("测试期草案")).toBeTruthy();
    expect(screen.getByText("非最终法律意见")).toBeTruthy();
    expect(screen.getByText("v0.1（测试期草案）")).toBeTruthy();
    expect(screen.getByText("2026 年 9 月 2 日")).toBeTruthy();

    const toc = screen.getByRole("navigation", { name: "文档目录" });
    expect(toc.querySelectorAll("a").length).toBeGreaterThan(1);
    expect(screen.getByRole("link", { name: "数据类别" }).getAttribute("href")).toBe(
      "#data-categories",
    );

    const documentText = screen.getByRole("article").textContent ?? "";
    for (const fact of [
      "邮箱",
      "密码哈希",
      "一次性验证码（OTP）",
      "session",
      "阅读材料",
      "网页链接",
      "文件",
      "阅读记录",
      "词汇",
      "笔记",
      "Ask 内容",
      "IP 地址",
      "设备信息",
      "必要日志",
      "HttpOnly",
      "Resend",
      "HIBP",
      "k-anonymity",
      "境外处理",
      "访问、更正、删除和注销",
      "未成年人",
      "政策更新",
    ]) {
      expect(documentText).toContain(fact);
    }

    expect(documentText).not.toContain("Google");
    expect(documentText).toContain("不等于“绝对安全”");
    expect(documentText).not.toContain("完全合规");
  });

  it("声明搜索引擎不应索引测试期草案", () => {
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });
});
