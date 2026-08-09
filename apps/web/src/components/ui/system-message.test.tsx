/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SystemMessage } from "./system-message";

afterEach(cleanup);

describe("SystemMessage quiet presentation", () => {
  it("keeps warning semantics without a panel-like border or fill", () => {
    const onClick = vi.fn();
    render(
      <SystemMessage
        variant="quiet"
        severity="warning"
        cta={{ label: "重新生成", onClick, variant: "ghost" }}
      >
        回答生成失败，请稍后重试。
      </SystemMessage>,
    );

    const notice = screen.getByRole("status");
    expect(notice.getAttribute("aria-live")).toBe("polite");
    expect(notice.className).toContain("border-0");
    expect(notice.className).toContain("bg-transparent");
    expect(notice.className).toContain("text-[13px]");
    expect(notice.querySelector(".lucide-triangle-alert")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
