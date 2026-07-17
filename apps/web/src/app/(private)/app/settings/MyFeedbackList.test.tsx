/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MyFeedbackList } from "./MyFeedbackList";

vi.mock("@/components/reader/FeedbackSheet", () => ({
  FEEDBACK_CONFIG_BY_SCOPE: {
    app: {
      positiveOptions: [],
      negativeOptions: [],
      neutralOptions: [{ value: "feature_request", label: "功能建议" }],
    },
  },
}));

const createItem = (id: string, status = "pending") => ({
  id,
  feedbackScope: "app" as const,
  feedbackType: "feature_request" as const,
  sentiment: "neutral",
  content: `content ${id}`,
  contextSummary: null,
  clientPlatform: "web" as const,
  clientSurface: "settings",
  entryPoint: "settings_form",
  resolutionNote: null,
  status,
  rewardPoints: 0,
  createdAt: "2026-07-17T10:00:00.000Z",
});

describe("MyFeedbackList", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, items: [createItem("1")], cursor: "c1", hasMore: true }),
        }),
      ),
    );
    vi.stubGlobal("alert", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("fetches initial list and renders item metadata", async () => {
    render(<MyFeedbackList />);

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith("/api/web/feedback?limit=6");
    });

    expect(screen.getByText("应用反馈")).toBeTruthy();
    expect(screen.getByText("功能建议")).toBeTruthy();
    expect(screen.getByText(/content 1/)).toBeTruthy();
  });

  it("refreshes when refreshKey changes", async () => {
    const { rerender } = render(<MyFeedbackList refreshKey={0} />);

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith("/api/web/feedback?limit=6");
    });

    (globalThis.fetch as ReturnType<typeof vi.fn>).mockClear();
    rerender(<MyFeedbackList refreshKey={1} />);

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith("/api/web/feedback?limit=6");
    });
  });

  it("shows error state on fetch failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          json: () => Promise.resolve({ ok: false, message: "load failed" }),
        }),
      ),
    );

    render(<MyFeedbackList />);

    await waitFor(() => {
      expect(screen.getByText("load failed")).toBeTruthy();
    });
  });

  it("revokes a pending item with DELETE and removes it from list", async () => {
    render(<MyFeedbackList />);

    await waitFor(() => {
      expect(screen.getByText(/content 1/)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "撤回" }));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith("/api/web/feedback/1", { method: "DELETE" });
    });

    expect(screen.queryByText(/content 1/)).toBeNull();
  });

  it("loads more records when button is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.includes("cursor=c1")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ ok: true, items: [createItem("2")], cursor: null, hasMore: false }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, items: [createItem("1")], cursor: "c1", hasMore: true }),
        });
      }),
    );

    render(<MyFeedbackList />);

    await waitFor(() => {
      expect(screen.getByText(/content 1/)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "加载更多记录" }));

    await waitFor(() => {
      expect(screen.getByText(/content 2/)).toBeTruthy();
    });
  });

  it("does not show revoke button for non-pending items", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, items: [createItem("1", "resolved")], cursor: null, hasMore: false }),
        }),
      ),
    );

    render(<MyFeedbackList />);

    await waitFor(() => {
      expect(screen.getByText(/content 1/)).toBeTruthy();
    });

    expect(screen.queryByRole("button", { name: "撤回" })).toBeNull();
  });

  it("renders the revoke button with a 44px touch target and focus-ring", async () => {
    render(<MyFeedbackList />);

    await waitFor(() => {
      expect(screen.getByText(/content 1/)).toBeTruthy();
    });

    const revokeButton = screen.getByRole("button", { name: "撤回" });
    expect(revokeButton.className).toContain("min-h-11");
    expect(revokeButton.className).toContain("focus-ring");
  });

  it("does not contain decorative images or list-level animations", () => {
    const { container } = render(<MyFeedbackList />);

    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.querySelectorAll("[style*='animationDelay']")).toHaveLength(0);
  });
});
