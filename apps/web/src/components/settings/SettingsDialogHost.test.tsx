/** @vitest-environment jsdom */

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// --- Mocks --------------------------------------------------------------

// Stub fetch so tests can drive response shape and timing.
const fetchMock = vi.fn<
  (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
>();
vi.stubGlobal("fetch", fetchMock);

// Mock useSettingsDialog so the Host test focuses on the fetch lifecycle.
// Tests mutate `controller` to drive isOpen / activeSection.
const controller = {
  openSettings: vi.fn(),
  closeSettings: vi.fn(),
  setActiveSection: vi.fn(),
  activeSection: "preferences" as
    | "account"
    | "preferences"
    | "usage"
    | "support",
  isOpen: false,
};

vi.mock("@/components/settings/SettingsDialogProvider", () => ({
  useSettingsDialog: () => controller,
}));

// Mock SettingsDialogShell to inspect props and render children inline
// so we can assert what the Host passes to its child.
const shellMock = vi.fn();
vi.mock("@/components/settings/SettingsDialogShell", () => ({
  SettingsDialogShell: (props: unknown) => {
    shellMock(props);
    const {
      children,
      open,
      activeSection,
      onOpenChange,
      onSectionChange,
    } = props as {
      children: React.ReactNode;
      open: boolean;
      activeSection: string;
      onOpenChange: (open: boolean) => void;
      onSectionChange: (section: string) => void;
    };
    if (!open) return null;
    return (
      <div
        data-testid="shell"
        data-open={open}
        data-section={activeSection}
      >
        <button
          data-testid="shell-close"
          onClick={() => onOpenChange(false)}
        >
          close
        </button>
        <button
          data-testid="shell-section-usage"
          onClick={() => onSectionChange("usage")}
        >
          usage
        </button>
        {children}
      </div>
    );
  },
}));

// Mock SettingsDialogContentClient to inspect the data + section props.
const contentMock = vi.fn();
vi.mock("@/components/settings/SettingsDialogContentClient", () => ({
  SettingsDialogContentClient: (props: unknown) => {
    contentMock(props);
    const { section } = props as { section: string };
    return (
      <div data-testid="content" data-section={section} />
    );
  },
}));

import { SettingsDialogHost } from "./SettingsDialogHost";

// --- Test data -----------------------------------------------------------

const SUCCESS_DATA = {
  accountData: {
    nickname: "Alice",
    displayFallback: "Alice",
    phone: "13800000000",
    status: "ready",
    avatarText: "A",
  },
  preferencesData: {
    readingGoal: "balanced",
    readingVariant: "translation",
    canEdit: true,
  },
};

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

function makePendingFetch(): {
  resolve: (response: Response) => void;
  reject: (error: Error) => void;
  promise: Promise<Response>;
} {
  let resolve!: (response: Response) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Response>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { resolve, reject, promise };
}

// --- Setup / teardown ----------------------------------------------------

beforeEach(() => {
  controller.isOpen = false;
  controller.activeSection = "preferences";
  controller.openSettings.mockClear();
  controller.closeSettings.mockClear();
  controller.setActiveSection.mockClear();
  fetchMock.mockReset();
  shellMock.mockClear();
  contentMock.mockClear();
});

afterEach(() => {
  cleanup();
});

// --- Tests ---------------------------------------------------------------

describe("SettingsDialogHost — initial mount (closed)", () => {
  it("does NOT fetch on mount", () => {
    render(<SettingsDialogHost />);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders Shell with open=false", () => {
    render(<SettingsDialogHost />);
    expect(shellMock).toHaveBeenCalledTimes(1);
    const props = shellMock.mock.calls[0][0] as { open: boolean };
    expect(props.open).toBe(false);
  });

  it("does not render content when closed", () => {
    render(<SettingsDialogHost />);
    expect(contentMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("content")).toBeNull();
  });
});

describe("SettingsDialogHost — fetch on open", () => {
  it("fetches GET /api/web/settings-dialog when isOpen transitions to true", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ok: true, data: SUCCESS_DATA }),
    );

    const { rerender } = render(<SettingsDialogHost />);
    expect(fetchMock).not.toHaveBeenCalled();

    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/web/settings-dialog");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("shows loading state immediately after open", async () => {
    const pending = makePendingFetch();
    fetchMock.mockReturnValueOnce(pending.promise);

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByRole("status")).toBeTruthy();
    });
    expect(screen.getByText("加载中…")).toBeTruthy();
    expect(contentMock).not.toHaveBeenCalled();

    pending.resolve(jsonResponse({ ok: true, data: SUCCESS_DATA }));
    await waitFor(() => {
      expect(screen.getByTestId("content")).toBeTruthy();
    });
  });

  it("renders SettingsDialogContentClient with data + active section on success", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ok: true, data: SUCCESS_DATA }),
    );

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    controller.activeSection = "account";
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(contentMock).toHaveBeenCalledTimes(1);
    });

    const props = contentMock.mock.calls[0][0] as {
      data: unknown;
      section: string;
    };
    expect(props.data).toEqual(SUCCESS_DATA);
    expect(props.section).toBe("account");
  });

  it("updates the section prop when activeSection changes after load", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ok: true, data: SUCCESS_DATA }),
    );

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    controller.activeSection = "account";
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(contentMock).toHaveBeenCalledTimes(1);
    });

    controller.activeSection = "usage";
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      const last =
        contentMock.mock.calls[contentMock.mock.calls.length - 1][0] as {
          section: string;
        };
      expect(last.section).toBe("usage");
    });
  });
});

describe("SettingsDialogHost — error handling", () => {
  it("shows safe error message on non-2xx response", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { ok: false, status: 500, code: "upstream_error", message: "internal" },
        { status: 500 },
      ),
    );

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });
    expect(screen.getByText("设置信息加载失败，请稍后重试。")).toBeTruthy();
    // Upstream internal message must NOT be surfaced.
    expect(screen.queryByText("internal")).toBeNull();
    expect(contentMock).not.toHaveBeenCalled();
  });

  it("shows safe error message on ok:false envelope (even with 200)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ok: false, status: 401, code: "auth_required", message: "no session" }),
    );

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });
    expect(screen.getByText("设置信息加载失败，请稍后重试。")).toBeTruthy();
    expect(screen.queryByText("no session")).toBeNull();
  });

  it("shows safe error message on malformed JSON body", async () => {
    fetchMock.mockResolvedValue(
      new Response("not-json", {
        status: 200,
        headers: { "content-type": "text/plain" },
      }),
    );

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });
    expect(screen.getByText("设置信息加载失败，请稍后重试。")).toBeTruthy();
  });

  it("shows safe error message on success envelope missing data", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });
  });

  it("shows safe error message on network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });
    expect(screen.getByText("设置信息加载失败，请稍后重试。")).toBeTruthy();
    expect(screen.queryByText("Failed to fetch")).toBeNull();
  });

  it("does not fake account/preferences data on error", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ok: false, status: 500 }, { status: 500 }),
    );

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });
    expect(contentMock).not.toHaveBeenCalled();
  });
});

describe("SettingsDialogHost — retry", () => {
  it("Retry button triggers a fresh fetch", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ ok: false, status: 500 }, { status: 500 }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, data: SUCCESS_DATA }),
      );

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });

    await act(async () => {
      screen.getByText("重试").click();
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    await waitFor(() => {
      expect(screen.getByTestId("content")).toBeTruthy();
    });
  });
});

describe("SettingsDialogHost — close & stale request", () => {
  it("aborts in-flight request when isOpen transitions back to false", async () => {
    const pending = makePendingFetch();
    fetchMock.mockReturnValueOnce(pending.promise);

    const abortSpy = vi.spyOn(AbortController.prototype, "abort");

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByText("加载中…")).toBeTruthy();
    });

    // Close while the request is still pending.
    controller.isOpen = false;
    rerender(<SettingsDialogHost />);

    expect(abortSpy).toHaveBeenCalled();

    // Resolving the (now aborted) request must not change UI state.
    pending.resolve(jsonResponse({ ok: true, data: SUCCESS_DATA }));
    // Give the microtask a chance to flush.
    await Promise.resolve();
    await Promise.resolve();

    // Shell is closed → no content rendered.
    expect(screen.queryByTestId("content")).toBeNull();
    expect(contentMock).not.toHaveBeenCalled();
  });

  it("re-fetches on a second open after close", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ok: true, data: SUCCESS_DATA }),
    );

    const { rerender } = render(<SettingsDialogHost />);

    // First open.
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    // Close.
    controller.isOpen = false;
    rerender(<SettingsDialogHost />);

    // Second open: should fetch again (no global cache).
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });

  it("resets to loading state on re-open (no stale success retained)", async () => {
    const firstPending = makePendingFetch();
    fetchMock
      .mockReturnValueOnce(firstPending.promise)
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, data: SUCCESS_DATA }),
      );

    const { rerender } = render(<SettingsDialogHost />);

    // First open — request pending.
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);
    await waitFor(() => {
      expect(screen.getByText("加载中…")).toBeTruthy();
    });

    // Resolve first request → success.
    firstPending.resolve(jsonResponse({ ok: true, data: SUCCESS_DATA }));
    await waitFor(() => {
      expect(screen.getByTestId("content")).toBeTruthy();
    });

    // Close → state resets to idle.
    controller.isOpen = false;
    rerender(<SettingsDialogHost />);
    expect(screen.queryByTestId("content")).toBeNull();

    // Second open → loading first, then success.
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);
    await waitFor(() => {
      expect(screen.getByText("加载中…")).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByTestId("content")).toBeTruthy();
    });
  });
});

describe("SettingsDialogHost — Shell wiring", () => {
  it("Shell onOpenChange(false) calls controller.closeSettings", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ok: true, data: SUCCESS_DATA }),
    );

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByTestId("shell")).toBeTruthy();
    });

    await act(async () => {
      screen.getByTestId("shell-close").click();
    });

    expect(controller.closeSettings).toHaveBeenCalledTimes(1);
  });

  it("Shell onSectionChange calls controller.setActiveSection", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ok: true, data: SUCCESS_DATA }),
    );

    const { rerender } = render(<SettingsDialogHost />);
    controller.isOpen = true;
    rerender(<SettingsDialogHost />);

    await waitFor(() => {
      expect(screen.getByTestId("shell")).toBeTruthy();
    });

    await act(async () => {
      screen.getByTestId("shell-section-usage").click();
    });

    expect(controller.setActiveSection).toHaveBeenCalledWith("usage");
  });

  it("passes activeSection to Shell", () => {
    controller.isOpen = false;
    controller.activeSection = "support";
    render(<SettingsDialogHost />);

    const props = shellMock.mock.calls[0][0] as { activeSection: string };
    expect(props.activeSection).toBe("support");
  });
});
