/** @vitest-environment jsdom */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AnalysisLoadingStatusBar } from "./AnalyzeSubmitForm";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

describe("AnalysisLoadingStatusBar", () => {
  it("uses reassurance copy without fake progress or timers", () => {
    render(
      <AnalysisLoadingStatusBar
        messagePrefix="正在透读"
      />,
    );

    expect(screen.getByText("正在透读")).toBeTruthy();
    expect(screen.getByText("离开本页不会影响透读，完成后会保存到阅读记录")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "去记录页" })).toBeNull();
    expect(screen.getByText("正在梳理文章结构")).toBeTruthy();

    const renderedText = document.body.textContent ?? "";
    expect(renderedText).not.toMatch(/\d{1,2}:\d{2}/);
    expect(renderedText).not.toContain("%");
    expect(renderedText).not.toContain("第");
    expect(renderedText).not.toContain("共");
  });
});
