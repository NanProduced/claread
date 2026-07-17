/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { UsageSection } from "./UsageSection";

afterEach(cleanup);

describe("UsageSection", () => {
  it("renders the placeholder explanation with no props", () => {
    render(<UsageSection />);

    expect(screen.getByText(/用量与积分能力将随新的 Agentic orchestration 统一适配/)).toBeTruthy();
    expect(screen.getByText("当前无需操作。")).toBeTruthy();
  });

  it("does not render old usage counters, progress bar, or ledger link", () => {
    render(<UsageSection />);

    expect(screen.queryByText(/今日解析点数/)).toBeNull();
    expect(screen.queryByText("查看明细账单")).toBeNull();
    expect(document.querySelector(".bg-lens-blue")).toBeNull();
  });
});
