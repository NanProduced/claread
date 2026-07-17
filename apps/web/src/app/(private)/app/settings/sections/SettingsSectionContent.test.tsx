/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProfileBffStatus } from "@/services/bff/profile";
import { SettingsSectionContent, type PreferencesData } from "./SettingsSectionContent";

afterEach(cleanup);

vi.mock("./AccountSection", () => ({
  AccountSection: (props: { avatarText: string; nickname: string }) => (
    <div data-testid="account-section" data-avatar={props.avatarText} data-nickname={props.nickname}>
      AccountSection
    </div>
  ),
}));

vi.mock("./PreferencesSection", () => ({
  PreferencesSection: (props: { readingGoal: string; canEdit: boolean }) => (
    <div
      data-testid="preferences-section"
      data-goal={props.readingGoal}
      data-can-edit={String(props.canEdit)}
    >
      PreferencesSection
    </div>
  ),
}));

vi.mock("./UsageSection", () => ({
  UsageSection: () => <div data-testid="usage-section">UsageSection</div>,
}));

vi.mock("./SupportSection", () => ({
  SupportSection: () => <div data-testid="support-section">SupportSection</div>,
}));

const accountData = {
  nickname: "Alice",
  displayFallback: "Alice Display",
  phone: "13800000000" as string | undefined,
  status: "ready" as ProfileBffStatus,
  avatarText: "A",
};

const preferencesData: PreferencesData = {
  readingGoal: "exam",
  readingVariant: "gaokao",
  canEdit: true,
};

describe("SettingsSectionContent", () => {
  describe("fallback mode", () => {
    it("renders all four SettingsSectionLayout wrappers in order", () => {
      const { container } = render(
        <SettingsSectionContent
          mode="fallback"
          accountData={accountData}
          preferencesData={preferencesData}
        />,
      );

      const layouts = container.querySelectorAll("section.group");
      expect(layouts.length).toBe(4);

      const titles = Array.from(layouts).map((layout) =>
        layout.querySelector("h2")?.textContent,
      );
      expect(titles).toEqual(["账户", "偏好", "用量与积分", "支持"]);
    });

    it("renders all four section components inside fallback layout", () => {
      render(
        <SettingsSectionContent
          mode="fallback"
          accountData={accountData}
          preferencesData={preferencesData}
        />,
      );

      expect(screen.getByTestId("account-section")).toBeTruthy();
      expect(screen.getByTestId("preferences-section")).toBeTruthy();
      expect(screen.getByTestId("usage-section")).toBeTruthy();
      expect(screen.getByTestId("support-section")).toBeTruthy();
    });

    it("renders usage placeholder even without any usage data prop", () => {
      render(
        <SettingsSectionContent
          mode="fallback"
          accountData={accountData}
          preferencesData={preferencesData}
        />,
      );

      expect(screen.getByTestId("usage-section")).toBeTruthy();
    });

    it("still renders Account/Preferences/Usage wrappers when Support has no data props", () => {
      const { container } = render(
        <SettingsSectionContent
          mode="fallback"
          accountData={accountData}
          preferencesData={preferencesData}
        />,
      );

      // Support section always renders in fallback mode regardless of data props.
      const supportLayout = Array.from(container.querySelectorAll("section.group")).find(
        (section) => section.querySelector("h2")?.textContent === "支持",
      );
      expect(supportLayout).not.toBeUndefined();
      expect(supportLayout?.querySelector('[data-testid="support-section"]')).not.toBeNull();
    });
  });

  describe("single-section mode", () => {
    it("renders only AccountSection content without SettingsSectionLayout wrapper", () => {
      const { container } = render(
        <SettingsSectionContent section="account" accountData={accountData} />,
      );

      expect(screen.getByTestId("account-section")).toBeTruthy();
      // No section.group layout wrapper in single-section mode.
      expect(container.querySelector("section.group")).toBeNull();
      // Other section components should not be rendered.
      expect(screen.queryByTestId("preferences-section")).toBeNull();
      expect(screen.queryByTestId("usage-section")).toBeNull();
      expect(screen.queryByTestId("support-section")).toBeNull();
    });

    it("renders only PreferencesSection content", () => {
      const { container } = render(
        <SettingsSectionContent section="preferences" preferencesData={preferencesData} />,
      );

      expect(screen.getByTestId("preferences-section")).toBeTruthy();
      expect(container.querySelector("section.group")).toBeNull();
      expect(screen.queryByTestId("account-section")).toBeNull();
      expect(screen.queryByTestId("usage-section")).toBeNull();
      expect(screen.queryByTestId("support-section")).toBeNull();
    });

    it("renders only UsageSection placeholder content without usage data", () => {
      const { container } = render(<SettingsSectionContent section="usage" />);

      expect(screen.getByTestId("usage-section")).toBeTruthy();
      expect(container.querySelector("section.group")).toBeNull();
      expect(screen.queryByTestId("account-section")).toBeNull();
      expect(screen.queryByTestId("preferences-section")).toBeNull();
      expect(screen.queryByTestId("support-section")).toBeNull();
    });

    it("renders only SupportSection content (no data props required)", () => {
      const { container } = render(<SettingsSectionContent section="support" />);

      expect(screen.getByTestId("support-section")).toBeTruthy();
      expect(container.querySelector("section.group")).toBeNull();
      expect(screen.queryByTestId("account-section")).toBeNull();
      expect(screen.queryByTestId("preferences-section")).toBeNull();
      expect(screen.queryByTestId("usage-section")).toBeNull();
    });

    it("returns null when section is specified but required data props are missing", () => {
      const { container } = render(<SettingsSectionContent section="account" />);

      expect(container.firstChild).toBeNull();
    });
  });

  describe("dialog mode — SettingsDialogSectionFrame wrapping", () => {
    it("wraps account section with frame title '个人资料' and standard width", () => {
      const { container } = render(
        <SettingsSectionContent section="account" accountData={accountData} />,
      );

      const heading = screen.getByRole("heading", { name: "个人资料", level: 2 });
      expect(heading).toBeTruthy();
      // Section component renders inside the frame body.
      expect(screen.getByTestId("account-section")).toBeTruthy();
      // No fallback SettingsSectionLayout wrapper leaks into dialog mode.
      expect(container.querySelector("section.group")).toBeNull();
      // Standard width constraint is applied.
      const bodyContentWrapper = screen.getByTestId("account-section")
        .parentElement!;
      expect(bodyContentWrapper.className).toContain("max-w-[40rem]");
    });

    it("wraps preferences section with frame title '偏好' and standard width", () => {
      render(
        <SettingsSectionContent
          section="preferences"
          preferencesData={preferencesData}
        />,
      );

      expect(
        screen.getByRole("heading", { name: "偏好", level: 2 }),
      ).toBeTruthy();
      expect(screen.getByTestId("preferences-section")).toBeTruthy();
      const bodyContentWrapper = screen.getByTestId("preferences-section")
        .parentElement!;
      expect(bodyContentWrapper.className).toContain("max-w-[40rem]");
    });

    it("wraps usage placeholder section with frame title '用量与积分' and standard width", () => {
      render(<SettingsSectionContent section="usage" />);

      expect(
        screen.getByRole("heading", { name: "用量与积分", level: 2 }),
      ).toBeTruthy();
      expect(screen.getByTestId("usage-section")).toBeTruthy();
      // Standard width constraint is applied (usage is now a placeholder).
      const bodyContentWrapper = screen.getByTestId("usage-section")
        .parentElement!;
      expect(bodyContentWrapper.className).toContain("max-w-[40rem]");
    });

    it("wraps support section with frame title '支持' and standard width", () => {
      render(<SettingsSectionContent section="support" />);

      expect(
        screen.getByRole("heading", { name: "支持", level: 2 }),
      ).toBeTruthy();
      expect(screen.getByTestId("support-section")).toBeTruthy();
      const bodyContentWrapper = screen.getByTestId("support-section")
        .parentElement!;
      expect(bodyContentWrapper.className).toContain("max-w-[40rem]");
    });

    it("exposes aria-labelledby linking the body region to the frame title", () => {
      render(
        <SettingsSectionContent section="account" accountData={accountData} />,
      );

      const heading = screen.getByRole("heading", { name: "个人资料", level: 2 });
      const titleId = heading.getAttribute("id");
      expect(titleId).toBeTruthy();
      // The body region is the scrollable container that wraps the
      // content wrapper holding the section component.
      const sectionEl = screen.getByTestId("account-section");
      const bodyRegion = sectionEl.parentElement!.parentElement!;
      expect(bodyRegion.getAttribute("aria-labelledby")).toBe(titleId);
    });

    it("frame body has independent scroll (min-h-0 + overflow-y-auto)", () => {
      render(
        <SettingsSectionContent section="account" accountData={accountData} />,
      );

      const sectionEl = screen.getByTestId("account-section");
      const bodyRegion = sectionEl.parentElement!.parentElement!;
      expect(bodyRegion.className).toContain("min-h-0");
      expect(bodyRegion.className).toContain("overflow-y-auto");
    });
  });
});
