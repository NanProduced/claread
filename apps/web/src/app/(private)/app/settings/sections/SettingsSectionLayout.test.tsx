/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SettingsSectionLayout } from "./SettingsSectionLayout";

afterEach(cleanup);

describe("SettingsSectionLayout", () => {
  it.each([
    ["Account", "账户"],
    ["Preferences", "偏好"],
    ["Quota", "用量与积分"],
    ["Support", "支持"],
  ])("renders Chinese label for %s", (title, expected) => {
    render(
      <SettingsSectionLayout title={title}>
        <p>content</p>
      </SettingsSectionLayout>,
    );

    expect(screen.getByText(expected)).toBeTruthy();
  });

  it("renders children inside the layout", () => {
    render(
      <SettingsSectionLayout title="Account">
        <p>account content</p>
      </SettingsSectionLayout>,
    );

    expect(screen.getByText("account content")).toBeTruthy();
  });

  it("uses the responsive grid layout className on the section element", () => {
    const { container } = render(
      <SettingsSectionLayout title="Support">
        <p>support content</p>
      </SettingsSectionLayout>,
    );

    const section = container.querySelector("section");
    expect(section).not.toBeNull();
    expect(section?.className).toContain("md:grid");
    expect(section?.className).toContain("md:grid-cols-[7rem_1fr]");
  });

  it("forwards the id prop to the section element", () => {
    const { container } = render(
      <SettingsSectionLayout id="usage-section" title="Quota">
        <p>usage</p>
      </SettingsSectionLayout>,
    );

    const section = container.querySelector("section#usage-section");
    expect(section).not.toBeNull();
  });

  it("does not use the old uppercase eyebrow title styles", () => {
    render(
      <SettingsSectionLayout title="Account">
        <p>content</p>
      </SettingsSectionLayout>,
    );

    const heading = screen.getByText("账户");
    expect(heading.className).not.toContain("uppercase");
    expect(heading.className).not.toContain("tracking-[0.25em]");
    expect(heading.className).not.toContain("text-[0.7rem]");
    expect(heading.className).not.toContain("font-bold");
  });

  it("uses the quiet Chinese label style", () => {
    render(
      <SettingsSectionLayout title="Preferences">
        <p>content</p>
      </SettingsSectionLayout>,
    );

    const heading = screen.getByText("偏好");
    expect(heading.className).toContain("text-sm");
    expect(heading.className).toContain("font-medium");
    expect(heading.className).toContain("text-muted-foreground");
  });
});
