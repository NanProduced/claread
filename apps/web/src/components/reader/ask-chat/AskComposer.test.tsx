// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AskComposer } from "./AskComposer";

describe("AskComposer web search toggle", () => {
  it("shows an explicit off state and requests allowed when clicked", () => {
    const onWebSearchModeChange = vi.fn();

    render(
      <AskComposer
        onSubmit={vi.fn()}
        onWebSearchModeChange={onWebSearchModeChange}
        placeholder="继续问这篇文章…"
        sending={false}
        webSearchMode="disabled"
      />,
    );

    const toggle = screen.getByRole("button", { name: "联网搜索已关闭" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(toggle.textContent).toContain("联网搜索 · 关");

    fireEvent.click(toggle);
    expect(onWebSearchModeChange).toHaveBeenCalledWith("allowed");
  });

  it("shows an explicit on state", () => {
    render(
      <AskComposer
        onSubmit={vi.fn()}
        onWebSearchModeChange={vi.fn()}
        placeholder="继续问这篇文章…"
        sending={false}
        webSearchMode="allowed"
      />,
    );

    const toggle = screen.getByRole("button", { name: "联网搜索已开启" });
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(toggle.textContent).toContain("联网搜索 · 开");
  });
});
