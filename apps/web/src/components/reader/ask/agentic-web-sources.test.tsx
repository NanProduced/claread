/** @vitest-environment jsdom */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { AgenticWebSources } from "./agentic-web-sources";
import type { AgenticCitationDisplayItem } from "./agentic-evidence";
import type { ReaderAskWebSearchSummaryDto } from "@/types/api/reader-ask";

afterEach(cleanup);

function articleCitation(
  citationId: string,
  snippet = "article snippet",
): AgenticCitationDisplayItem {
  return {
    citationId,
    sourceKind: "article",
    title: "文章依据",
    snippet,
    url: null,
    sourceTitle: null,
    description: null,
  };
}

function webCitation(
  citationId: string,
  overrides: Partial<
    AgenticCitationDisplayItem & { url: string }
  > = {},
): AgenticCitationDisplayItem {
  const url = overrides.url ?? "https://example.com/article-1";
  return {
    citationId,
    sourceKind: "web",
    title: "网络来源",
    snippet: overrides.snippet ?? "web snippet",
    url,
    sourceTitle: overrides.sourceTitle ?? "Example Title",
    description: overrides.description ?? null,
  };
}

/**
 * Query helper: prompt-kit SourceTrigger renders an `<a>` tagged with
 * `data-slot="prompt-kit-source-trigger"`. Use this instead of the old
 * `web-source-pill` testid now that we delegate to prompt-kit primitives.
 */
function getSourceTriggers(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      '[data-slot="prompt-kit-source-trigger"]',
    ),
  );
}

describe("AgenticWebSources", () => {
  it("returns null when there are no web citations and no outcome notice", () => {
    const { container } = render(
      <AgenticWebSources
        citations={[articleCitation("c1")]}
        webSearchSummary={null}
      />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("returns null when webSearchSummary is completed with no web citations", () => {
    // completed + 0 cited sources is a valid state — must not produce a
    // misleading "no results" notice (product rule §5.3).
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "completed",
      cited_source_count: 0,
    };
    const { container } = render(
      <AgenticWebSources citations={[]} webSearchSummary={summary} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders web source pills when web citations exist", () => {
    const citations = [
      articleCitation("c1"),
      webCitation("c2", { url: "https://example.com/path" }),
      webCitation("c3", { url: "https://other.org/page" }),
    ];
    const { container } = render(
      <AgenticWebSources citations={citations} webSearchSummary={null} />,
    );

    expect(screen.getByTestId("agentic-web-sources")).toBeTruthy();
    expect(screen.getByTestId("web-source-list")).toBeTruthy();
    expect(screen.getByText("网页来源")).toBeTruthy();
    // Domain extraction strips www. prefix.
    expect(screen.getByText("example.com")).toBeTruthy();
    expect(screen.getByText("other.org")).toBeTruthy();
    // Each pill is an anchor with the href.
    const pills = getSourceTriggers(container);
    expect(pills).toHaveLength(2);
    expect(pills[0].getAttribute("href")).toBe("https://example.com/path");
    expect(pills[1].getAttribute("href")).toBe("https://other.org/page");
  });

  it("filters out web citations with missing or empty url", () => {
    const citations = [
      webCitation("c2", { url: "https://example.com/valid" }),
      // Manually craft an invalid web citation with empty url.
      {
        ...webCitation("c3"),
        url: "",
      },
    ];
    const { container } = render(
      <AgenticWebSources citations={citations} webSearchSummary={null} />,
    );
    const pills = getSourceTriggers(container);
    expect(pills).toHaveLength(1);
    expect(pills[0].getAttribute("href")).toBe("https://example.com/valid");
  });

  it("deduplicates web citations by canonical URL (first occurrence wins, order preserved)", () => {
    const citations = [
      webCitation("c1", { url: "https://example.com/dup" }),
      webCitation("c2", { url: "https://other.org/unique" }),
      // Duplicate of c1 — must be dropped, not rendered twice.
      webCitation("c3", { url: "https://example.com/dup" }),
      // Duplicate of c2 — must be dropped.
      webCitation("c4", { url: "https://other.org/unique" }),
    ];
    const { container } = render(
      <AgenticWebSources citations={citations} webSearchSummary={null} />,
    );
    const pills = getSourceTriggers(container);
    expect(pills).toHaveLength(2);
    expect(pills.map((p) => p.getAttribute("href"))).toEqual([
      "https://example.com/dup",
      "https://other.org/unique",
    ]);
  });

  it("renders outcome notice for no_results", () => {
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "no_results",
      cited_source_count: 0,
    };
    render(<AgenticWebSources citations={[]} webSearchSummary={summary} />);
    expect(screen.getByTestId("web-search-outcome-notice").textContent).toBe(
      "未找到可用网页来源",
    );
    // No web source list when no citations.
    expect(screen.queryByTestId("web-source-list")).toBeNull();
  });

  it("renders outcome notice for unavailable", () => {
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "unavailable",
      cited_source_count: 0,
    };
    render(<AgenticWebSources citations={[]} webSearchSummary={summary} />);
    expect(screen.getByTestId("web-search-outcome-notice").textContent).toBe(
      "网页搜索暂不可用",
    );
  });

  it("renders outcome notice for failed", () => {
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "failed",
      cited_source_count: 0,
    };
    render(<AgenticWebSources citations={[]} webSearchSummary={summary} />);
    expect(screen.getByTestId("web-search-outcome-notice").textContent).toBe(
      "网页搜索未完成",
    );
  });

  it("renders both outcome notice and web citations when outcome is non-completed but citations exist", () => {
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "failed",
      cited_source_count: 1,
    };
    const citations = [webCitation("c1", { url: "https://example.com/x" })];
    const { container } = render(
      <AgenticWebSources citations={citations} webSearchSummary={summary} />,
    );
    expect(screen.getByTestId("web-search-outcome-notice").textContent).toBe(
      "网页搜索未完成",
    );
    expect(getSourceTriggers(container)).toHaveLength(1);
  });

  it("does not render outcome notice when outcome is completed even with citations", () => {
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "completed",
      cited_source_count: 2,
    };
    const citations = [
      webCitation("c1", { url: "https://example.com/a" }),
      webCitation("c2", { url: "https://example.com/b" }),
    ];
    const { container } = render(
      <AgenticWebSources citations={citations} webSearchSummary={summary} />,
    );
    expect(screen.queryByTestId("web-search-outcome-notice")).toBeNull();
    expect(getSourceTriggers(container)).toHaveLength(2);
  });

  it("treats undefined webSearchSummary the same as null", () => {
    const { container } = render(
      <AgenticWebSources citations={[]} webSearchSummary={undefined} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("strips www. prefix from domain label", () => {
    const citations = [webCitation("c1", { url: "https://www.example.com/page" })];
    render(<AgenticWebSources citations={citations} webSearchSummary={null} />);
    expect(screen.getByText("example.com")).toBeTruthy();
  });

  it("falls back to raw href when URL is malformed", () => {
    // The backend validator enforces http/https, but the frontend is defensive.
    // prompt-kit sourceDomain falls back to the last path segment or raw href.
    const citations = [
      webCitation("c1", { url: "not-a-valid-url" }),
    ];
    render(<AgenticWebSources citations={citations} webSearchSummary={null} />);
    // Domain extraction falls back to raw href (no slashes → returns itself).
    expect(screen.getByText("not-a-valid-url")).toBeTruthy();
  });

  it("sets rel=noopener noreferrer and target=_blank on every pill", () => {
    const citations = [
      webCitation("c1", { url: "https://example.com/a" }),
      webCitation("c2", { url: "https://example.com/b" }),
    ];
    const { container } = render(
      <AgenticWebSources citations={citations} webSearchSummary={null} />,
    );
    const pills = getSourceTriggers(container);
    for (const pill of pills) {
      expect(pill.getAttribute("rel")).toBe("noopener noreferrer");
      expect(pill.getAttribute("target")).toBe("_blank");
    }
  });

  it("uses domain as accessibility label suffix", () => {
    const citations = [
      webCitation("c1", { url: "https://example.com/page" }),
    ];
    const { container } = render(
      <AgenticWebSources citations={citations} webSearchSummary={null} />,
    );
    const pill = getSourceTriggers(container)[0];
    expect(pill.getAttribute("aria-label")).toBe("查看网页来源 example.com");
  });

  it("preserves citation order in the rendered pill list", () => {
    const citations = [
      webCitation("c1", { url: "https://first.com/a" }),
      webCitation("c2", { url: "https://second.com/b" }),
      webCitation("c3", { url: "https://third.com/c" }),
    ];
    const { container } = render(
      <AgenticWebSources citations={citations} webSearchSummary={null} />,
    );
    const pills = getSourceTriggers(container);
    expect(pills.map((p) => p.getAttribute("href"))).toEqual([
      "https://first.com/a",
      "https://second.com/b",
      "https://third.com/c",
    ]);
  });
});
