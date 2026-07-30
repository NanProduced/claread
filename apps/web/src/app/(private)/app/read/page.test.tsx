/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PasteToReadPage from "./page";

const mocks = vi.hoisted(() => ({
  fetchDailyReaderToday: vi.fn(),
  fetchDailyReaderList: vi.fn(),
  getProfileSettings: vi.fn(),
}));

vi.mock("@/services/api/daily-reader", () => ({
  fetchDailyReaderToday: mocks.fetchDailyReaderToday,
  fetchDailyReaderList: mocks.fetchDailyReaderList,
}));

vi.mock("@/services/bff/profile", () => ({
  getProfileSettings: mocks.getProfileSettings,
}));

vi.mock("./ReadPageIntake", () => ({
  ReadPageIntake: () => <div data-testid="read-page-intake" />,
}));

vi.mock("@/components/primitives", () => ({
  ScrollArea: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const TODAY_ARTICLE = {
  id: "daily-1",
  title: "The Quiet Power of Reading Slowly",
  subtitle: "Why deep reading still matters",
  source: "Claread Editorial",
  sourceUrl: "https://example.com/a",
  publishDate: "2026-07-29",
  difficulty: "B2",
  readTimeMinutes: 6,
  tags: ["reading"],
  coverImageUrl: null,
  coverTheme: "paper",
  body: { paragraphs: [] },
  highlights: [],
  footerAnalysis: { summary: "", keyExpressions: [], discussionQuestions: [] },
};

const LIST_ITEM = {
  id: "archive-1",
  title: "Archive Pick",
  subtitle: null,
  source: "Claread Editorial",
  publishDate: "2026-07-28",
  difficulty: "B1",
  readTimeMinutes: 4,
  tags: [],
  coverImageUrl: null,
  coverTheme: "paper",
};

beforeEach(() => {
  mocks.getProfileSettings.mockResolvedValue({ profile: { settings: {} } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("/app/read daily pick panel", () => {
  it("renders the lead pick with 查看全部 when today data is available", async () => {
    mocks.fetchDailyReaderToday.mockResolvedValue({ ok: true, data: [TODAY_ARTICLE] });
    mocks.fetchDailyReaderList.mockResolvedValue({ ok: true, data: { items: [LIST_ITEM] } });

    render(await PasteToReadPage());

    const panels = screen.getAllByTestId("daily-pick-panel");
    expect(panels).toHaveLength(2);
    for (const panel of panels) {
      expect(panel.getAttribute("data-state")).toBe("ready");
    }
    expect(screen.getAllByText("The Quiet Power of Reading Slowly").length).toBeGreaterThan(0);
    // 杂志栏次要稿：往期补位一篇，与头条同栏编号呈现。
    expect(screen.getAllByText("Archive Pick").length).toBeGreaterThan(0);
    const viewAll = screen.getAllByRole("link", { name: /查看全部/ });
    expect(viewAll[0]?.getAttribute("href")).toBe("/daily");
    expect(screen.queryByText(/今日内容稍后更新/)).toBeNull();
  });

  it("keeps a stable fallback entry when today and list requests both fail", async () => {
    mocks.fetchDailyReaderToday.mockResolvedValue({ ok: false, status: 500, error: "boom" });
    mocks.fetchDailyReaderList.mockResolvedValue({ ok: false, status: 500, error: "boom" });

    render(await PasteToReadPage());

    const panels = screen.getAllByTestId("daily-pick-panel");
    expect(panels).toHaveLength(2);
    for (const panel of panels) {
      expect(panel.getAttribute("data-state")).toBe("fallback");
    }
    expect(screen.getAllByText(/今日内容稍后更新/).length).toBeGreaterThan(0);
    const archiveLinks = screen.getAllByRole("link", { name: /浏览往期/ });
    expect(archiveLinks[0]?.getAttribute("href")).toBe("/daily");
    const viewAll = screen.getAllByRole("link", { name: /查看全部/ });
    expect(viewAll.length).toBeGreaterThan(0);
  });

  it("keeps the workbench grid width identical with and without daily data", async () => {
    mocks.fetchDailyReaderToday.mockResolvedValue({ ok: true, data: [TODAY_ARTICLE] });
    mocks.fetchDailyReaderList.mockResolvedValue({ ok: true, data: { items: [] } });
    const ready = render(await PasteToReadPage());
    const readyGrid = ready.container.querySelector("div.grid");
    const readyClass = readyGrid?.getAttribute("class");
    ready.unmount();

    mocks.fetchDailyReaderToday.mockResolvedValue({ ok: false, status: 500, error: "boom" });
    mocks.fetchDailyReaderList.mockResolvedValue({ ok: false, status: 500, error: "boom" });
    const fallback = render(await PasteToReadPage());
    const fallbackGrid = fallback.container.querySelector("div.grid");

    expect(readyClass).toContain("xl:grid-cols-[minmax(0,1fr)_24rem]");
    expect(fallbackGrid?.getAttribute("class")).toBe(readyClass);
  });
});
