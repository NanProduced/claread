/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { WebSources } from "./source";

afterEach(cleanup);

describe("WebSources", () => {
  it("does not create a disclosure when the typed web-source list is empty", () => {
    const { container } = render(<WebSources sources={[]} />);

    expect(container.firstChild).toBeNull();
  });

  it("renders Prompt Kit source links from typed web citation fields", () => {
    render(
      <WebSources
        sources={[
          {
            citationId: "w1",
            href: "https://example.com/research",
            title: "Example research",
            description: "A concise web-source preview.",
          },
        ]}
      />,
    );

    const trigger = screen.getByRole("link", { name: "查看网页来源 example.com" });
    expect(trigger.getAttribute("href")).toBe("https://example.com/research");
    expect(trigger.className).toContain("h-6");
    expect(trigger.className).toContain("cursor-pointer");
    expect(trigger.className).toContain("bg-surface-raised");
    expect(screen.getByText("网页来源 · 1")).not.toBeNull();
  });

  it("labels published_at separately from host retrieved_at", async () => {
    const { container } = render(
      <WebSources
        sources={[
          {
            citationId: "w-date",
            href: "https://example.com/date",
            title: "Dated research",
            publishedAt: "2026-07-20",
            retrievedAt: "2026-07-29T08:30:00+00:00",
          },
        ]}
      />,
    );

    const trigger = container.querySelector<HTMLAnchorElement>(
      'a[href="https://example.com/date"]',
    );
    expect(trigger).not.toBeNull();
    fireEvent.focus(trigger!);
    fireEvent.pointerEnter(trigger!, { pointerType: "mouse" });
    fireEvent.pointerMove(trigger!, { pointerType: "mouse" });

    expect((await screen.findByTestId("web-source-published-at")).textContent).toBe(
      "发布于 2026-07-20",
    );
    const sourceCard = document.querySelector<HTMLElement>(
      '[data-slot="prompt-kit-source-content"]',
    );
    expect(sourceCard?.className).toContain("rounded-xl");
    expect(sourceCard?.className).toContain("bg-surface");
    const retrievedAt = await screen.findByTestId("web-source-retrieved-at");
    expect(retrievedAt.tagName).toBe("TIME");
    expect(retrievedAt.getAttribute("dateTime")).toBe("2026-07-29T08:30:00+00:00");
    expect(retrievedAt.textContent).toMatch(/^检索于 /);
    expect(retrievedAt.textContent).not.toContain("2026-07-29T08:30:00+00:00");
    expect(screen.queryByText(/page_age/i)).toBeNull();
  });

  it("does not show an invalid retrieved_at timestamp", async () => {
    const { container } = render(
      <WebSources
        sources={[
          {
            citationId: "w-invalid-date",
            href: "https://example.com/invalid-date",
            title: "Invalid retrieval time",
            retrievedAt: "not-an-iso-timestamp",
          },
        ]}
      />,
    );

    const trigger = container.querySelector<HTMLAnchorElement>(
      'a[href="https://example.com/invalid-date"]',
    );
    expect(trigger).not.toBeNull();
    fireEvent.focus(trigger!);
    fireEvent.pointerEnter(trigger!, { pointerType: "mouse" });
    fireEvent.pointerMove(trigger!, { pointerType: "mouse" });

    expect(await screen.findByText("Invalid retrieval time")).not.toBeNull();
    expect(screen.queryByTestId("web-source-retrieved-at")).toBeNull();
  });
});
