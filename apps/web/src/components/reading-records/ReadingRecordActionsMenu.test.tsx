/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const routerReplace = vi.fn();
const routerRefresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, refresh: routerRefresh }),
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock("@/components/primitives/toast", () => ({
  toast: { success: (...a: unknown[]) => toastSuccess(...a), error: (...a: unknown[]) => toastError(...a) },
  ClareadToaster: () => null,
}));

const removeLocal = vi.fn();
vi.mock("@/components/layout/recent-reading-context", () => ({
  useRecentReading: () => ({ items: [], refetch: vi.fn(), removeLocal }),
}));

import { ReadingRecordActionsMenu } from "./ReadingRecordActionsMenu";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
  // 测试环境的 window.localStorage 不可用（项目惯例：内存替身）。
  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      get length() {
        return store.size;
      },
      clear: () => store.clear(),
      getItem: (key: string) => store.get(key) ?? null,
      key: (index: number) => Array.from(store.keys())[index] ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    } satisfies Storage,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openMenu(title = "测试文章") {
  render(<ReadingRecordActionsMenu recordId="rec-1" title={title} showRemoveFromRecent />);
  await userEvent.click(screen.getByRole("button", { name: `打开“${title}”的操作菜单` }));
  await screen.findByRole("menu");
}

describe("ReadingRecordActionsMenu", () => {
  it("exposes an accessible trigger name", () => {
    render(<ReadingRecordActionsMenu recordId="rec-1" title="测试文章" />);
    expect(
      screen.getByRole("button", { name: '打开“测试文章”的操作菜单' }),
    ).toBeTruthy();
  });

  it("shows both actions when showRemoveFromRecent is true", async () => {
    await openMenu();
    expect(screen.getByText("从最近阅读中移除")).toBeTruthy();
    expect(screen.getByText("删除阅读记录")).toBeTruthy();
  });

  it("hides the remove-from-recent item when showRemoveFromRecent is false", async () => {
    render(<ReadingRecordActionsMenu recordId="rec-1" title="测试文章" />);
    await userEvent.click(screen.getByRole("button", { name: '打开“测试文章”的操作菜单' }));
    await screen.findByRole("menu");
    expect(screen.queryByText("从最近阅读中移除")).toBeNull();
    expect(screen.getByText("删除阅读记录")).toBeTruthy();
  });

  it("removes from recent with exactly one DELETE and no confirmation", async () => {
    await openMenu();
    await userEvent.click(screen.getByText("从最近阅读中移除"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/web/reader/records/rec-1/recent");
    expect(init.method).toBe("DELETE");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await waitFor(() => {
      expect(removeLocal).toHaveBeenCalledWith("rec-1");
      expect(toastSuccess).toHaveBeenCalledWith("已从最近阅读中移除");
    });
  });

  it("prevents double submit while the hide request is pending", async () => {
    let resolveFetch: (value: Response) => void = () => {};
    fetchMock.mockReturnValue(new Promise<Response>((resolve) => { resolveFetch = resolve; }));

    await openMenu();
    await userEvent.click(screen.getByText("从最近阅读中移除"));
    // The item becomes disabled and relabeled while pending; a raw second
    // click on it (double submit) must not fire another request.
    fireEvent.click(screen.getByText("正在移除…"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolveFetch(jsonResponse({ ok: true }));
  });

  it("keeps the item and shows a fixed safe toast on hide failure", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: false, status: 500 }, 500));
    await openMenu();
    await userEvent.click(screen.getByText("从最近阅读中移除"));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith("操作失败，请稍后重试。");
    });
    expect(removeLocal).not.toHaveBeenCalled();
    expect(toastError.mock.calls.some((call) => String(call[0]).includes("upstream"))).toBe(false);
  });

  it("requires confirmation before delete: cancel issues zero requests", async () => {
    await openMenu();
    await userEvent.click(screen.getByText("删除阅读记录"));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toBeTruthy();
    expect(screen.getByText("删除这条阅读记录？")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("deletes after confirmation and updates local state, toast and router", async () => {
    const onDeleted = vi.fn();
    render(
      <ReadingRecordActionsMenu
        recordId="rec-1"
        title="测试文章"
        showRemoveFromRecent
        onDeleted={onDeleted}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: '打开“测试文章”的操作菜单' }));
    await screen.findByRole("menu");
    await userEvent.click(screen.getByText("删除阅读记录"));
    await screen.findByRole("alertdialog");

    await userEvent.click(screen.getByRole("button", { name: "删除记录" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/web/reader/records/rec-1");
    expect(init.method).toBe("DELETE");
    await waitFor(() => {
      expect(removeLocal).toHaveBeenCalledWith("rec-1");
      expect(onDeleted).toHaveBeenCalledWith("rec-1");
      expect(routerRefresh).toHaveBeenCalled();
      expect(toastSuccess).toHaveBeenCalledWith("已删除阅读记录");
    });
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(routerReplace).not.toHaveBeenCalled();
  });

  it("deleting a record clears its pending candidate restore entry", async () => {
    const { readPendingCandidate, PENDING_CANDIDATE_STORAGE_KEY } =
      await import("@/app/(private)/app/read/pending-candidate");
    window.localStorage.setItem(
      PENDING_CANDIDATE_STORAGE_KEY,
      JSON.stringify({
        readingRecordId: "rec-1",
        candidateDocumentId: "cand-1",
        originalInputId: null,
        inputSnapshot: "draft text",
        filename: "sample.md",
        origin: "submit",
        savedAt: new Date().toISOString(),
      }),
    );
    expect(readPendingCandidate()?.readingRecordId).toBe("rec-1");

    render(<ReadingRecordActionsMenu recordId="rec-1" title="测试文章" />);
    await userEvent.click(screen.getByRole("button", { name: '打开“测试文章”的操作菜单' }));
    await screen.findByRole("menu");
    await userEvent.click(screen.getByText("删除阅读记录"));
    await screen.findByRole("alertdialog");
    await userEvent.click(screen.getByRole("button", { name: "删除记录" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(readPendingCandidate()).toBeNull();
    });
  });

  it("navigates to the library route when deleting the current record", async () => {
    render(
      <ReadingRecordActionsMenu
        recordId="rec-1"
        title="测试文章"
        isCurrentRecord
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: '打开“测试文章”的操作菜单' }));
    await screen.findByRole("menu");
    await userEvent.click(screen.getByText("删除阅读记录"));
    await screen.findByRole("alertdialog");
    await userEvent.click(screen.getByRole("button", { name: "删除记录" }));

    await waitFor(() => {
      expect(routerReplace).toHaveBeenCalledWith("/app/library");
    });
  });

  it("keeps the dialog retryable and shows a safe toast on delete failure", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: false, status: 404 }, 404));
    await openMenu();
    await userEvent.click(screen.getByText("删除阅读记录"));
    await screen.findByRole("alertdialog");
    await userEvent.click(screen.getByRole("button", { name: "删除记录" }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith("操作失败，请稍后重试。");
    });
    expect(removeLocal).not.toHaveBeenCalled();
    expect(routerRefresh).not.toHaveBeenCalled();
    // Dialog stays open for retry.
    expect(screen.getByRole("alertdialog")).toBeTruthy();
  });

  it("never renders a button nested inside a link (no asChild link usage)", () => {
    render(<ReadingRecordActionsMenu recordId="rec-1" title="测试文章" />);
    expect(screen.getByRole("button", { name: '打开“测试文章”的操作菜单' }).tagName).toBe("BUTTON");
  });
});
