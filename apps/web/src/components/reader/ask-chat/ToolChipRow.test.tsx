/** @vitest-environment jsdom */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ToolChipRow } from "./ToolChipRow";
import type { ReaderAskToolTraceEntryDto } from "@/types/api/reader-ask";

afterEach(cleanup);

const entries: ReaderAskToolTraceEntryDto[] = [
  { tool_name: "get_record_context", status: "completed", next_actions: [], artifacts: [], metadata_json: {} },
  { tool_name: "get_record_insights", status: "completed", next_actions: [], artifacts: [], metadata_json: {} },
  { tool_name: "generate_sentence_annotation", status: "failed", next_actions: [], artifacts: [], metadata_json: {} },
];

describe("ToolChipRow", () => {
  it("renders tool labels for each entry", () => {
    render(<ToolChipRow entries={entries} />);
    // Default toolLabelFn maps get_record_context → "上下文"
    expect(screen.getByText("上下文")).toBeTruthy();
    expect(screen.getByText("解析")).toBeTruthy();
  });

  it("shows overflow count when more than 5 entries", () => {
    const manyEntries: ReaderAskToolTraceEntryDto[] = Array.from({ length: 8 }, (_, i) => ({
      tool_name: "get_record_context",
      status: "completed" as const,
      next_actions: [],
      artifacts: [],
      metadata_json: {},
    }));
    render(<ToolChipRow entries={manyEntries} />);
    expect(screen.getByText("+3")).toBeTruthy();
  });

  it("returns null for empty entries", () => {
    const { container } = render(<ToolChipRow entries={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("uses custom toolLabelFn when provided", () => {
    render(<ToolChipRow entries={entries.slice(0, 1)} toolLabelFn={() => "custom"} />);
    expect(screen.getByText("custom")).toBeTruthy();
  });
});
