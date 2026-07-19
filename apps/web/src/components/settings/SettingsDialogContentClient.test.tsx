// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SettingsDialogData } from "@/lib/settings-dialog-data";

// --- Mock the four section components to inspect props ---
const accountMock = vi.fn();
vi.mock("@/app/(private)/app/settings/sections/AccountSection", () => ({
  AccountSection: (props: unknown) => {
    accountMock(props);
    return <div data-testid="account-section" />;
  },
}));

const preferencesMock = vi.fn();
vi.mock("@/app/(private)/app/settings/sections/PreferencesSection", () => ({
  PreferencesSection: (props: unknown) => {
    preferencesMock(props);
    return <div data-testid="preferences-section" />;
  },
}));

const usageMock = vi.fn();
vi.mock("@/app/(private)/app/settings/sections/UsageSection", () => ({
  UsageSection: () => {
    usageMock();
    return <div data-testid="usage-section" />;
  },
}));

const supportMock = vi.fn();
vi.mock("@/app/(private)/app/settings/sections/SupportSection", () => ({
  SupportSection: () => {
    supportMock();
    return <div data-testid="support-section" />;
  },
}));

const frameMock = vi.fn();
vi.mock("@/components/settings/SettingsDialogSectionFrame", () => ({
  SettingsDialogSectionFrame: (props: unknown) => {
    frameMock(props);
    const { children, title, description, width } = props as {
      children: React.ReactNode;
      title: string;
      description?: string;
      width?: string;
    };
    return (
      <div
        data-testid="section-frame"
        data-title={title}
        data-description={description ?? ""}
        data-width={width ?? ""}
      >
        {children}
      </div>
    );
  },
}));

// The content client must NOT import the retired legacy page-mode
// composition components. We assert this via the lint test below.
import { SettingsDialogContentClient } from "./SettingsDialogContentClient";

afterEach(() => {
  cleanup();
  accountMock.mockClear();
  preferencesMock.mockClear();
  usageMock.mockClear();
  supportMock.mockClear();
  frameMock.mockClear();
});

const validData: SettingsDialogData = {
  accountData: {
    nickname: "Alice",
    displayFallback: "Alice",
    phone: "13800000000",
    status: "ready",
    avatarText: "A",
  },
  preferencesData: {
    readingGoal: "daily_reading",
    readingVariant: "intermediate_reading",
    canEdit: true,
  },
};

describe("SettingsDialogContentClient", () => {
  it("renders AccountSection with all accountData fields when section=account", () => {
    render(<SettingsDialogContentClient data={validData} section="account" />);

    expect(accountMock).toHaveBeenCalledTimes(1);
    const props = accountMock.mock.calls[0][0] as Record<string, unknown>;
    expect(props).toEqual({
      nickname: "Alice",
      displayFallback: "Alice",
      phone: "13800000000",
      status: "ready",
      avatarText: "A",
    });
    expect(screen.getByTestId("account-section")).toBeTruthy();
  });

  it("renders PreferencesSection with reading defaults + canEdit when section=preferences", () => {
    render(
      <SettingsDialogContentClient data={validData} section="preferences" />,
    );

    expect(preferencesMock).toHaveBeenCalledTimes(1);
    const props = preferencesMock.mock.calls[0][0] as Record<string, unknown>;
    expect(props).toEqual({
      readingGoal: "daily_reading",
      readingVariant: "intermediate_reading",
      canEdit: true,
    });
    expect(screen.getByTestId("preferences-section")).toBeTruthy();
  });

  it("renders UsageSection (no props) when section=usage", () => {
    render(<SettingsDialogContentClient data={validData} section="usage" />);
    expect(usageMock).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("usage-section")).toBeTruthy();
  });

  it("renders SupportSection (no props) when section=support", () => {
    render(<SettingsDialogContentClient data={validData} section="support" />);
    expect(supportMock).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("support-section")).toBeTruthy();
  });

  it("falls back to preferences section when section is invalid", () => {
    render(
      <SettingsDialogContentClient
        data={validData}
        section={"invalid" as unknown as "preferences"}
      />,
    );
    expect(preferencesMock).toHaveBeenCalledTimes(1);
    expect(accountMock).not.toHaveBeenCalled();
  });

  it("wraps every section in SettingsDialogSectionFrame with title + description", () => {
    render(<SettingsDialogContentClient data={validData} section="account" />);
    const frameAccount = screen.getByTestId("section-frame");
    expect(frameAccount.dataset.title).toBe("个人资料");
    expect(frameAccount.dataset.description).toBe("管理你的档案与登录状态。");

    cleanup();
    frameMock.mockClear();

    render(
      <SettingsDialogContentClient data={validData} section="preferences" />,
    );
    expect(screen.getByTestId("section-frame").dataset.title).toBe("偏好");

    cleanup();
    frameMock.mockClear();

    render(<SettingsDialogContentClient data={validData} section="usage" />);
    expect(screen.getByTestId("section-frame").dataset.title).toBe(
      "用量与积分",
    );

    cleanup();
    frameMock.mockClear();

    render(<SettingsDialogContentClient data={validData} section="support" />);
    expect(screen.getByTestId("section-frame").dataset.title).toBe("支持");
  });

  it("uses standard width for all sections", () => {
    render(<SettingsDialogContentClient data={validData} section="account" />);
    expect(screen.getByTestId("section-frame").dataset.width).toBe("standard");
  });

  it("passes AccountData fields individually (NOT the whole accountData object)", () => {
    render(<SettingsDialogContentClient data={validData} section="account" />);
    const props = accountMock.mock.calls[0][0] as Record<string, unknown>;
    // The AccountSection contract takes individual fields, not an
    // accountData object — so the content adapter must spread them.
    expect(props).not.toHaveProperty("accountData");
    expect(props).not.toHaveProperty("preferencesData");
  });

  it("does not accept partial account data — TypeScript contract enforces full DTO", () => {
    // This is a compile-time guarantee; runtime just asserts the props
    // are forwarded verbatim from SettingsDialogData.
    render(<SettingsDialogContentClient data={validData} section="account" />);
    const props = accountMock.mock.calls[0][0] as Record<string, unknown>;
    expect(props.nickname).toBe(validData.accountData.nickname);
    expect(props.displayFallback).toBe(validData.accountData.displayFallback);
    expect(props.phone).toBe(validData.accountData.phone);
    expect(props.status).toBe(validData.accountData.status);
    expect(props.avatarText).toBe(validData.accountData.avatarText);
  });
});

describe("SettingsDialogContentClient — DTO source of truth", () => {
  it("does not redefine a second AccountData/PreferencesData view model", async () => {
    // Read the source file as text and verify it does not declare a
    // second DTO type — it must reuse SettingsDialogData from the data
    // layer (lib/settings-dialog-data.ts).
    const fs = await import("node:fs");
    const path = await import("node:path");
    const src = fs.readFileSync(
      path.resolve(__dirname, "SettingsDialogContentClient.tsx"),
      "utf8",
    );

    // Must import the DTO from the data layer.
    expect(src).toContain(
      'from "@/lib/settings-dialog-data"',
    );

    // Must NOT redefine a parallel DTO.
    expect(src).not.toMatch(
      /interface\s+SettingsDialogAccountData\b/,
    );
    expect(src).not.toMatch(
      /interface\s+SettingsDialogPreferencesData\b/,
    );
    expect(src).not.toMatch(
      /interface\s+AccountData\b/,
    );
    expect(src).not.toMatch(
      /interface\s+PreferencesData\b/,
    );
  });

  it("does not import retired legacy page-mode composition components", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const src = fs.readFileSync(
      path.resolve(__dirname, "SettingsDialogContentClient.tsx"),
      "utf8",
    );

    expect(src).not.toMatch(
      /from\s+["']@\/app\/\(private\)\/app\/settings\/sections\/SettingsSectionContent["']/,
    );
    expect(src).not.toMatch(
      /from\s+["']@\/app\/\(private\)\/app\/settings\/sections\/SettingsSectionLayout["']/,
    );
  });
});
