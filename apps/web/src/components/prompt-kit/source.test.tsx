/** @vitest-environment jsdom */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WebSources } from "./source";

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
    expect(screen.getByText("网页来源 · 1")).not.toBeNull();
  });
});
