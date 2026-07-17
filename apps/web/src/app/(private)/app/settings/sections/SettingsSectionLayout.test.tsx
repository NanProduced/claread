/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SettingsSectionLayout } from "./SettingsSectionLayout";

afterEach(cleanup);

describe("SettingsSectionLayout", () => {
  it("renders the title text", () => {
    render(
      <SettingsSectionLayout title="Account">
        <p>content</p>
      </SettingsSectionLayout>,
    );

    expect(screen.getByText("Account")).toBeTruthy();
  });

  it("renders children inside the layout", () => {
    render(
      <SettingsSectionLayout title="Quota">
        <p>quota content</p>
      </SettingsSectionLayout>,
    );

    expect(screen.getByText("quota content")).toBeTruthy();
  });

  it("uses md:grid md:grid-cols layout className on the section element", () => {
    const { container } = render(
      <SettingsSectionLayout title="Support">
        <p>support content</p>
      </SettingsSectionLayout>,
    );

    const section = container.querySelector("section");
    expect(section).not.toBeNull();
    expect(section?.className).toContain("md:grid");
    expect(section?.className).toContain("md:grid-cols-[140px_1fr]");
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
});
