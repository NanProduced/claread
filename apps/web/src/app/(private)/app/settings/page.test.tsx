/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SettingsPage from "./page";

const loadSettingsDataMock = vi.hoisted(() => vi.fn());
const settingsSectionContentMock = vi.hoisted(() => vi.fn<(props: { mode?: string }) => null>(() => null));

vi.mock("./lib/loadSettingsData", () => ({
  loadSettingsData: loadSettingsDataMock,
}));

vi.mock("@/components/primitives/scroll-area", () => ({
  ScrollArea: ({ children }: { children?: unknown }) => children,
}));

vi.mock("./sections/SettingsSectionContent", () => ({
  SettingsSectionContent: settingsSectionContentMock,
}));

const defaultSettingsData = {
  accountData: {
    nickname: "",
    displayFallback: "",
    phone: undefined,
    status: "ready",
    avatarText: "U",
  },
  preferencesData: {
    readingGoal: "daily_reading",
    readingVariant: "intermediate_reading",
    canEdit: true,
  },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  loadSettingsDataMock.mockResolvedValue(defaultSettingsData);
});

describe("SettingsPage", () => {
  it('renders page title "设置" and not "Preferences."', async () => {
    const element = await SettingsPage();
    render(element);

    expect(screen.getByText("设置")).toBeTruthy();
    expect(screen.queryByText("Preferences.")).toBeNull();
  });

  it('renders SettingsSectionContent in fallback mode without usageData', async () => {
    const element = await SettingsPage();
    render(element);

    const callArgs = settingsSectionContentMock.mock.calls[0];
    expect(callArgs?.[0]).toMatchObject({ mode: "fallback" });
    expect(callArgs?.[0]).not.toHaveProperty("usageData");
  });

  it("loads settings data on render", async () => {
    await SettingsPage();

    expect(loadSettingsDataMock).toHaveBeenCalledTimes(1);
  });

  it("renders usage placeholder even when loadSettingsData returns no usage data", async () => {
    loadSettingsDataMock.mockResolvedValue({
      accountData: defaultSettingsData.accountData,
      preferencesData: defaultSettingsData.preferencesData,
    });

    const element = await SettingsPage();
    render(element);

    const callArgs = settingsSectionContentMock.mock.calls[0];
    expect(callArgs?.[0]).toMatchObject({ mode: "fallback" });
    expect(callArgs?.[0]).not.toHaveProperty("usageData");
  });
});
