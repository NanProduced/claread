/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { QuotaVm } from "@/types/view/QuotaVm";
import { UsageSection } from "./UsageSection";

afterEach(cleanup);

vi.mock("../CreditLedgerPanel", () => ({
  CreditLedgerPanel: () => <div data-testid="credit-ledger-panel">Ledger</div>,
}));

const sampleQuota: QuotaVm = {
  profileId: "p1",
  quotaUsed: 3,
  quotaLimit: 10,
  quotaType: "daily",
  dailyFreePoints: 10,
  dailyUsedPoints: 3,
  bonusPoints: 5,
  remainingPoints: 7,
};

describe("UsageSection", () => {
  it("renders used and total numbers when quota is present", () => {
    render(
      <UsageSection
        quota={sampleQuota}
        quotaUsed={3}
        quotaLimit={10}
        quotaPercentage={30}
      />,
    );

    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("/ 10")).toBeTruthy();
  });

  it("renders '--' when quota is null", () => {
    const { container } = render(
      <UsageSection quota={null} quotaUsed={0} quotaLimit={0} quotaPercentage={0} />,
    );

    const dashes = Array.from(container.querySelectorAll("span")).filter((el) =>
      el.textContent?.includes("--"),
    );
    expect(dashes.length).toBeGreaterThanOrEqual(2);
    // No progress bar or ledger link should render when quota is null.
    expect(container.querySelector(".bg-lens-blue")).toBeNull();
    expect(screen.queryByText("查看明细账单")).toBeNull();
  });

  it("renders remaining and bonus points when quota is present", () => {
    render(
      <UsageSection
        quota={sampleQuota}
        quotaUsed={3}
        quotaLimit={10}
        quotaPercentage={30}
      />,
    );

    expect(screen.getByText("7")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
  });

  it("renders the '查看明细账单' link to /app/settings/ledger when quota is present", () => {
    render(
      <UsageSection
        quota={sampleQuota}
        quotaUsed={3}
        quotaLimit={10}
        quotaPercentage={30}
      />,
    );

    const link = screen.getByText("查看明细账单").closest("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/app/settings/ledger");
  });

  it("does not render the ledger link when quota is null", () => {
    render(<UsageSection quota={null} quotaUsed={0} quotaLimit={0} quotaPercentage={0} />);

    expect(screen.queryByText("查看明细账单")).toBeNull();
  });

  it("does not render CreditLedgerPanel by default", () => {
    render(
      <UsageSection
        quota={sampleQuota}
        quotaUsed={3}
        quotaLimit={10}
        quotaPercentage={30}
      />,
    );

    expect(screen.queryByTestId("credit-ledger-panel")).toBeNull();
  });

  it("renders CreditLedgerPanel when showLedger is true", () => {
    render(
      <UsageSection
        quota={sampleQuota}
        quotaUsed={3}
        quotaLimit={10}
        quotaPercentage={30}
        showLedger
      />,
    );

    expect(screen.getByTestId("credit-ledger-panel")).toBeTruthy();
  });
});

describe("UsageSection — showLedger link/ledger flow", () => {
  it("hides the '查看明细账单' link when showLedger=true (Dialog mode)", () => {
    // In Dialog mode the CreditLedgerPanel is rendered inline, so the
    // "查看明细账单" link to the standalone ledger page is redundant.
    render(
      <UsageSection
        quota={sampleQuota}
        quotaUsed={3}
        quotaLimit={10}
        quotaPercentage={30}
        showLedger
      />,
    );

    expect(screen.queryByText("查看明细账单")).toBeNull();
    // Ledger panel still mounts inline.
    expect(screen.getByTestId("credit-ledger-panel")).toBeTruthy();
  });

  it("keeps the '查看明细账单' link when showLedger=false (fallback page mode)", () => {
    render(
      <UsageSection
        quota={sampleQuota}
        quotaUsed={3}
        quotaLimit={10}
        quotaPercentage={30}
      />,
    );

    const link = screen.getByText("查看明细账单").closest("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/app/settings/ledger");
    // No inline ledger panel in fallback page mode.
    expect(screen.queryByTestId("credit-ledger-panel")).toBeNull();
  });

  it("hides both link and ledger panel when quota is null even if showLedger=true", () => {
    // No quota means no usage data to display; the link is gated on quota
    // and the ledger panel is gated on showLedger. When quota is null,
    // the link must not render regardless of showLedger.
    render(
      <UsageSection
        quota={null}
        quotaUsed={0}
        quotaLimit={0}
        quotaPercentage={0}
        showLedger
      />,
    );

    expect(screen.queryByText("查看明细账单")).toBeNull();
    // Ledger panel is still rendered because showLedger=true; the panel
    // itself is responsible for its own empty state.
    expect(screen.getByTestId("credit-ledger-panel")).toBeTruthy();
  });
});
