/** @vitest-environment jsdom */

import { useState } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ReadingDefaultState } from "@/lib/reading-defaults";
import { ReadingPlanFields } from "./index";

afterEach(cleanup);

function Harness({
  initialValue = {
    readingGoal: "daily_reading",
    readingVariant: "intermediate_reading",
  },
  layout = "compact",
}: {
  initialValue?: ReadingDefaultState;
  layout?: "compact" | "settings";
}) {
  const [value, setValue] = useState(initialValue);
  return (
    <>
      <ReadingPlanFields
        value={value}
        onValueChange={setValue}
        layout={layout}
      />
      <output>{`${value.readingGoal}:${value.readingVariant}`}</output>
    </>
  );
}

describe("ReadingPlanFields", () => {
  it("uses one radio model for the two current reading goals", () => {
    render(<Harness />);

    const group = screen.getByRole("radiogroup", { name: "阅读目标" });
    expect(group).toBeTruthy();
    expect(within(group).getAllByRole("radio")).toHaveLength(2);
    expect(screen.queryByRole("radio", { name: "学术摘要" })).toBeNull();
    expect(
      screen
        .getByRole("radio", { name: "日常阅读" })
        .getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("changes goals with arrow keys and restores each goal's last variant", () => {
    render(<Harness />);

    const daily = screen.getByRole("radio", { name: "日常阅读" });
    daily.focus();
    fireEvent.keyDown(daily, { key: "ArrowRight" });
    expect(screen.getByText("exam:cet")).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: "考研" }));
    expect(screen.getByText("exam:kaoyan")).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: "日常阅读" }));
    fireEvent.click(screen.getByRole("radio", { name: "精读" }));
    expect(screen.getByText("daily_reading:intensive_reading")).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: "备考精读" }));
    expect(screen.getByText("exam:kaoyan")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "日常阅读" }));
    expect(screen.getByText("daily_reading:intensive_reading")).toBeTruthy();
  });

  it("keeps every option visible and only explains the current variant", () => {
    render(<Harness layout="settings" />);

    expect(
      screen.getByText("平衡理解、词汇与语法，适合日常泛读。"),
    ).toBeTruthy();
    expect(screen.queryByText("新建阅读时默认采用此阅读方案。")).toBeNull();
    expect(
      within(screen.getByRole("radiogroup", { name: "阅读方案" })).getAllByRole(
        "radio",
      ),
    ).toHaveLength(3);

    fireEvent.click(screen.getByRole("radio", { name: "备考精读" }));
    expect(
      within(screen.getByRole("radiogroup", { name: "阅读方案" })).getAllByRole(
        "radio",
      ),
    ).toHaveLength(5);
    expect(screen.getByText("抓取主干信息与同义替换，训练常见考点。")).toBeTruthy();
  });

  it("moves variant selection and focus with arrow keys", () => {
    render(<Harness />);

    const intermediate = screen.getByRole("radio", { name: "进阶" });
    intermediate.focus();
    fireEvent.keyDown(intermediate, { key: "ArrowRight" });

    const intensive = screen.getByRole("radio", { name: "精读" });
    expect(intensive.getAttribute("aria-checked")).toBe("true");
    expect(document.activeElement).toBe(intensive);
    expect(screen.getByText("daily_reading:intensive_reading")).toBeTruthy();
  });
});
