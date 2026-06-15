/** @vitest-environment jsdom */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { FollowUpSuggestionChips } from "./FollowUpSuggestionChips";
import type { ReaderAskFollowUpSuggestionDto } from "@/types/api/reader-ask";

afterEach(cleanup);

const suggestions: ReaderAskFollowUpSuggestionDto[] = [
  { label: "解释语法", prompt: "这句话的语法结构是什么？" },
  { label: "相关词汇", prompt: "有哪些相关词汇？" },
  { label: "更多例句", prompt: "能给我更多例句吗？" },
];

describe("FollowUpSuggestionChips", () => {
  it("renders suggestion labels", () => {
    render(<FollowUpSuggestionChips suggestions={suggestions} onPickSuggestion={vi.fn()} />);
    expect(screen.getByText("解释语法")).toBeTruthy();
    expect(screen.getByText("相关词汇")).toBeTruthy();
    expect(screen.getByText("更多例句")).toBeTruthy();
  });

  it("calls onPickSuggestion with prompt when chip is clicked", () => {
    const onPick = vi.fn();
    render(<FollowUpSuggestionChips suggestions={suggestions} onPickSuggestion={onPick} />);
    fireEvent.click(screen.getByText("解释语法"));
    expect(onPick).toHaveBeenCalledWith("这句话的语法结构是什么？");
  });

  it("returns null for empty suggestions", () => {
    const { container } = render(<FollowUpSuggestionChips suggestions={[]} onPickSuggestion={vi.fn()} />);
    expect(container.innerHTML).toBe("");
  });
});
