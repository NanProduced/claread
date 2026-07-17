/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProfileBffStatus } from "@/services/bff/profile";
import type { QuotaVm } from "@/types/view/QuotaVm";
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
  UsageSection: (props: { quotaUsed: number; showLedger?: boolean }) => (
    <div
      data-testid="usage-section"
      data-used={props.quotaUsed}
      data-show-ledger={String(props.showLedger ?? false)}
    >
      UsageSection
    </div>
  ),
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

const usageData = {
  quota: {
    profileId: "p1",
    quotaUsed: 3,
    quotaLimit: 10,
    quotaType: "daily" as const,
    dailyFreePoints: 10,
    dailyUsedPoints: 3,
    bonusPoints: 5,
    remainingPoints: 7,
  } satisfies QuotaVm,
  quotaUsed: 3,
  quotaLimit: 10,
  quotaPercentage: 30,
};

describe("SettingsSectionContent", () => {
  describe("fallback mode", () => {
    it("renders all four SettingsSectionLayout wrappers in order", () => {
      const { container } = render(
        <SettingsSectionContent
          mode="fallback"
          accountData={accountData}
          preferencesData={preferencesData}
          usageData={usageData}
        />,
      );

      const layouts = container.querySelectorAll("section.group");
      expect(layouts.length).toBe(4);

      const titles = Array.from(layouts).map((layout) =>
        layout.querySelector("h2")?.textContent,
      );
      expect(titles).toEqual(["Account", "Preferences", "Quota", "Support"]);
    });

    it("renders all four section components inside fallback layout", () => {
      render(
        <SettingsSectionContent
          mode="fallback"
          accountData={accountData}
          preferencesData={preferencesData}
          usageData={usageData}
        />,
      );

      expect(screen.getByTestId("account-section")).toBeTruthy();
      expect(screen.getByTestId("preferences-section")).toBeTruthy();
      expect(screen.getByTestId("usage-section")).toBeTruthy();
      expect(screen.getByTestId("support-section")).toBeTruthy();
    });

    it("forwards showLedger=false to UsageSection by default in fallback mode", () => {
      render(
        <SettingsSectionContent
          mode="fallback"
          accountData={accountData}
          preferencesData={preferencesData}
          usageData={usageData}
        />,
      );

      const usage = screen.getByTestId("usage-section");
      expect(usage.getAttribute("data-show-ledger")).toBe("false");
    });

    it("still renders Account/Preferences/Usage wrappers when Support has no data props", () => {
      const { container } = render(
        <SettingsSectionContent
          mode="fallback"
          accountData={accountData}
          preferencesData={preferencesData}
          usageData={usageData}
        />,
      );

      // Support section always renders in fallback mode regardless of data props.
      const supportLayout = Array.from(container.querySelectorAll("section.group")).find(
        (section) => section.querySelector("h2")?.textContent === "Support",
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

    it("renders only UsageSection content with showLedger forwarded", () => {
      const { container } = render(
        <SettingsSectionContent section="usage" usageData={usageData} usageShowLedger />,
      );

      const usage = screen.getByTestId("usage-section");
      expect(usage).toBeTruthy();
      expect(usage.getAttribute("data-show-ledger")).toBe("true");
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
    it("wraps account section with frame title '账户' and standard width", () => {
      const { container } = render(
        <SettingsSectionContent section="account" accountData={accountData} />,
      );

      const heading = screen.getByRole("heading", { name: "账户", level: 2 });
      expect(heading).toBeTruthy();
      // Section component renders inside the frame body.
      expect(screen.getByTestId("account-section")).toBeTruthy();
      // No fallback SettingsSectionLayout wrapper leaks into dialog mode.
      expect(container.querySelector("section.group")).toBeNull();
      // Standard width constraint is applied.
      const bodyContentWrapper = screen.getByTestId("account-section")
        .parentElement!;
      expect(bodyContentWrapper.className).toContain("max-w-[34rem]");
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
      expect(bodyContentWrapper.className).toContain("max-w-[34rem]");
    });

    it("wraps usage section with frame title '用量与积分' and wide width", () => {
      render(
        <SettingsSectionContent
          section="usage"
          usageData={usageData}
          usageShowLedger
        />,
      );

      expect(
        screen.getByRole("heading", { name: "用量与积分", level: 2 }),
      ).toBeTruthy();
      // showLedger still forwarded to UsageSection in dialog mode.
      const usage = screen.getByTestId("usage-section");
      expect(usage.getAttribute("data-show-ledger")).toBe("true");
      // Wide width: no max-w-[34rem] constraint.
      const bodyContentWrapper = screen.getByTestId("usage-section")
        .parentElement!;
      expect(bodyContentWrapper.className).not.toContain("max-w-[34rem]");
    });

    it("wraps support section with frame title '支持' and standard width", () => {
      render(<SettingsSectionContent section="support" />);

      expect(
        screen.getByRole("heading", { name: "支持", level: 2 }),
      ).toBeTruthy();
      expect(screen.getByTestId("support-section")).toBeTruthy();
      const bodyContentWrapper = screen.getByTestId("support-section")
        .parentElement!;
      expect(bodyContentWrapper.className).toContain("max-w-[34rem]");
    });

    it("exposes aria-labelledby linking the body region to the frame title", () => {
      render(
        <SettingsSectionContent section="account" accountData={accountData} />,
      );

      const heading = screen.getByRole("heading", { name: "账户", level: 2 });
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

    it("forwards usageShowLedger=false by default in dialog mode", () => {
      render(<SettingsSectionContent section="usage" usageData={usageData} />);

      const usage = screen.getByTestId("usage-section");
      expect(usage.getAttribute("data-show-ledger")).toBe("false");
    });
  });
});
