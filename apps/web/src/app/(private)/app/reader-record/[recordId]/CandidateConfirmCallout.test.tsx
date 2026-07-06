/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateConfirmCallout } from "./CandidateConfirmCallout";
import { savePendingCandidate } from "../../read/pending-candidate";

const originalLocation = window.location;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// jsdom env does not always expose `window.localStorage` for files under
// directories whose name contains `[recordId]`. Use a polyfill: if absent,
// fall back to an in-memory store so `localStorage.clear|getItem|setItem`
// calls in the component and the helper below still work.
function ensureLocalStorage(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.clear();
    return;
  } catch {
    /* window.localStorage is undefined or sealed */
  }
  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
      clear: () => {
        store.clear();
      },
      key: (i: number) => Array.from(store.keys())[i] ?? null,
      get length() {
        return store.size;
      },
    },
  });
}

function seedPendingCandidate(opts: {
  readingRecordId?: string;
  candidateDocumentId?: string;
  filename?: string | null;
  canonicalTextPreview?: string | null;
}) {
  return savePendingCandidate({
    readingRecordId: opts.readingRecordId ?? "rec_x",
    candidateDocumentId: opts.candidateDocumentId ?? "cand_x",
    originalInputId: null,
    inputSnapshot: null,
    filename: opts.filename ?? null,
    canonicalTextPreview: opts.canonicalTextPreview ?? null,
  });
}

describe("CandidateConfirmCallout (real DOM behavior)", () => {
  beforeEach(() => {
    ensureLocalStorage();
    window.localStorage.clear();
    // Stub navigation so the callout doesn't actually leave the page.
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign: vi.fn(), reload: vi.fn() },
    });
  });

  afterEach(() => {
    cleanup();
    ensureLocalStorage();
    window.localStorage.clear();
    vi.unstubAllGlobals();
    // Restore real window.location so other tests aren't affected.
    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    });
  });

  it("does NOT render when there is no matching pending candidate", async () => {
    render(<CandidateConfirmCallout recordId="rec_other" />);
    await waitFor(() => {
      expect(screen.queryByTestId("candidate-confirm-dialog")).toBeNull();
    });
  });

  it("does NOT render when pending candidate belongs to a different record", async () => {
    seedPendingCandidate({
      readingRecordId: "rec_a",
      candidateDocumentId: "cand_a",
    });
    render(<CandidateConfirmCallout recordId="rec_b" />);
    await waitFor(() => {
      expect(screen.queryByTestId("candidate-confirm-dialog")).toBeNull();
    });
  });

  it("renders confirm UI when localStorage has matching readingRecordId + candidateDocumentId, showing filename + preview", async () => {
    seedPendingCandidate({
      readingRecordId: "rec_match",
      candidateDocumentId: "cand_match",
      filename: "thesis.pdf",
      canonicalTextPreview: "This is the canonical preview for the candidate document.",
    });
    render(<CandidateConfirmCallout recordId="rec_match" />);

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-dialog")).toBeTruthy();
    });
    expect(screen.getByText(/thesis\.pdf/)).toBeTruthy();
    expect(
      screen.getByText(/This is the canonical preview for the candidate document\./),
    ).toBeTruthy();
    expect(screen.getByTestId("candidate-confirm-button")).toBeTruthy();
  });

  it("点击 确认并开始透读 POSTs the confirm route, clears localStorage, and triggers page reload on success", async () => {
    seedPendingCandidate({
      readingRecordId: "rec_ok",
      candidateDocumentId: "cand_ok",
      filename: "ok.pdf",
      canonicalTextPreview: "ok preview",
    });
    const reloadSpy = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload: reloadSpy, assign: vi.fn() },
    });

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/confirm")) {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body ?? "{}"))).toEqual({ language: "en" });
        return jsonResponse({
          ok: true,
          reading_record_id: "rec_ok",
          candidate_document_id: "cand_ok",
          stable_document_id: "sd_ok",
        });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CandidateConfirmCallout recordId="rec_ok" />);
    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-button")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("candidate-confirm-button"));

    await waitFor(() => {
      expect(reloadSpy).toHaveBeenCalled();
    });
    expect(window.localStorage.getItem("claread:web:pending-candidate")).toBeNull();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/confirm"))).toBe(true);
  });

  it("稍后处理 dismisses the callout but KEEPS the pending candidate in localStorage", async () => {
    seedPendingCandidate({
      readingRecordId: "rec_defer",
      candidateDocumentId: "cand_defer",
      filename: "defer.pdf",
    });
    render(<CandidateConfirmCallout recordId="rec_defer" />);
    await waitFor(() => {
      expect(screen.getByTestId("candidate-defer-button")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("candidate-defer-button"));

    await waitFor(() => {
      expect(screen.queryByTestId("candidate-confirm-dialog")).toBeNull();
    });
    expect(window.localStorage.getItem("claread:web:pending-candidate")).not.toBeNull();
  });

  it("409 candidate_conflict: shows changed-state copy with 刷新页面 / 重试确认 / 重新提交 buttons", async () => {
    seedPendingCandidate({
      readingRecordId: "rec_conflict",
      candidateDocumentId: "cand_conflict",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/confirm")) {
          return jsonResponse(
            {
              ok: false,
              status: 409,
              code: "candidate_conflict",
              message: "候选文档状态已变化，请刷新后重试。",
            },
            409,
          );
        }
        return new Response(null, { status: 404 });
      }),
    );

    render(<CandidateConfirmCallout recordId="rec_conflict" />);
    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-button")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("candidate-confirm-button"));

    await waitFor(() => {
      expect(screen.getByText(/提取结果需要重新确认/)).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "刷新页面" })).toBeTruthy();
    expect(screen.getByTestId("candidate-retry-confirm-button")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新提交" })).toBeTruthy();
  });

  it("Plan A: 重试确认 on the conflict branch retries the POST confirm directly", async () => {
    // Spy on reload BEFORE seeding so the very first attempt's reload also
    // is observed if it accidentally succeeds.
    const reloadSpy = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload: reloadSpy, assign: vi.fn() },
    });
    seedPendingCandidate({
      readingRecordId: "rec_retry",
      candidateDocumentId: "cand_retry",
    });
    let confirmCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/confirm")) {
        confirmCalls += 1;
        if (confirmCalls === 1) {
          return jsonResponse(
            {
              ok: false,
              status: 409,
              code: "candidate_conflict",
              message: "候选文档状态已变化，请刷新后重试。",
            },
            409,
          );
        }
        return jsonResponse({
          ok: true,
          reading_record_id: "rec_retry",
          candidate_document_id: "cand_retry",
          stable_document_id: "sd_retry",
        });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CandidateConfirmCallout recordId="rec_retry" />);
    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-button")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("candidate-confirm-button"));
    await waitFor(() => {
      expect(screen.getByTestId("candidate-retry-confirm-button")).toBeTruthy();
    });
    expect(confirmCalls).toBe(1);
    fireEvent.click(screen.getByTestId("candidate-retry-confirm-button"));

    await waitFor(() => {
      expect(confirmCalls).toBe(2);
    });
    // After the 200 OK, refreshPage() fires. Give it a tick.
    await waitFor(() => {
      expect(reloadSpy).toHaveBeenCalled();
    });
  });

  it("non-409 BFF error: shows BFF Chinese message and a 返回确认 button that puts the user back to ready", async () => {
    seedPendingCandidate({
      readingRecordId: "rec_err",
      candidateDocumentId: "cand_err",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/confirm")) {
          return jsonResponse(
            {
              ok: false,
              status: 503,
              code: "upstream_unavailable",
              message: "暂时无法连接服务，请稍后重试。",
            },
            503,
          );
        }
        return new Response(null, { status: 404 });
      }),
    );

    render(<CandidateConfirmCallout recordId="rec_err" />);
    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-button")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("candidate-confirm-button"));

    await waitFor(() => {
      expect(screen.getByText(/暂时无法连接服务/)).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "返回确认" })).toBeTruthy();
    expect(screen.queryByText(/upstream_unavailable/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "返回确认" }));

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-button")).toBeTruthy();
    });
    expect(screen.queryByTestId("candidate-confirm-error")).toBeNull();
  });
});
