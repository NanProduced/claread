/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ArticleRagStatusPanel } from "./ArticleRagStatusPanel";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function installLocationShim() {
  if (typeof window === "undefined") return;
  if (typeof (window as { location?: unknown }).location === "object") return;
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign: vi.fn(), reload: vi.fn() },
  });
}

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
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
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

describe("ArticleRagStatusPanel — status rendering", () => {
  beforeEach(() => {
    installLocationShim();
    ensureLocalStorage();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders the 'ready' label when status is 'indexed' and shows chunk count", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        ok: true,
        status: "indexed",
        chunk_count: 42,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ArticleRagStatusPanel recordId="rec_x" generation={1} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("ready");
    });
    expect(screen.getByTestId("article-rag-status-label").textContent).toBe(
      "可用于文章引用问答",
    );
    expect(screen.getByTestId("article-rag-status-meta").textContent).toContain(
      "已索引 42 块",
    );
  });

  it("renders the 'preparing' label when status is 'queued' or 'indexing'", async () => {
    for (const status of ["queued", "indexing"]) {
      vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ ok: true, status })));
      const { unmount } = render(<ArticleRagStatusPanel recordId={`rec_${status}`} generation={1} />);
      await waitFor(() => {
        expect(
          screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
        ).toBe("preparing");
      });
      expect(screen.getByTestId("article-rag-status-label").textContent).toContain(
        "后台准备文章引用中",
      );
      expect(screen.getByText(/不影响当前阅读/)).toBeTruthy();
      unmount();
    }
  });

  it("silently degrades to 'unavailable' for not_ready / not_indexed / failed / superseded_or_stale / unavailable / unknown", async () => {
    const lifecycleCases = [
      "not_ready",
      "not_indexed",
      "failed",
      "superseded_or_stale",
      "unavailable",
      "this-is-not-real",
    ];
    for (const status of lifecycleCases) {
      vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ ok: true, status })));
      const { unmount } = render(<ArticleRagStatusPanel recordId={`rec_${status}`} generation={1} />);
      await waitFor(() => {
        expect(
          screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
        ).toBe("unavailable");
      });
      expect(screen.getByTestId("article-rag-status-label").textContent).toBe(
        "文章引用问答暂未准备",
      );
      unmount();
    }
  });

  it("silently degrades to 'unavailable' when the status endpoint returns BFF error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          { ok: false, status: 503, code: "upstream_unavailable", message: "暂时无法连接服务" },
          503,
        ),
      ),
    );

    render(<ArticleRagStatusPanel recordId="rec_x" generation={1} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("unavailable");
    });
    expect(screen.queryByText(/upstream_unavailable|暂时无法连接/)).toBeNull();
    expect(screen.queryByText(/reason_code/)).toBeNull();
  });

  it("does NOT render debug-only fields like reason_code / failure_code / provider / query", () => {
    render(<ArticleRagStatusPanel recordId="rec_x" generation={1} />);
    expect(screen.queryByText(/reason_code/)).toBeNull();
    expect(screen.queryByText(/failure_code/)).toBeNull();
    expect(screen.queryByText(/query_sha256/)).toBeNull();
    expect(screen.queryByText(/source_pack_hash/)).toBeNull();
    expect(screen.queryByText(/provider/)).toBeNull();
  });
});

describe("ArticleRagStatusPanel — ensure action", () => {
  beforeEach(() => {
    installLocationShim();
    ensureLocalStorage();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("POSTs to /article-rag-index/ensure with expectedGeneration and flips to 'preparing' on 'enqueued' status", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/status")) {
        return jsonResponse({ ok: true, status: "unavailable" });
      }
      if (url.endsWith("/ensure")) {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body ?? "{}"))).toEqual({
          expectedGeneration: 1,
        });
        return jsonResponse({ ok: true, status: "enqueued" });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ArticleRagStatusPanel recordId="rec_x" generation={1} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("unavailable");
    });

    fireEvent.click(screen.getByTestId("article-rag-status-ensure"));

    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("preparing");
    });
    expect(screen.queryByTestId("article-rag-status-ensure")).toBeNull();
  });

  it("'idempotent_noop' ensure result re-fetches status (does not stay in synthetic 'preparing')", async () => {
    // Sequence: initial /status lands the panel in 'unavailable' so the
    // ensure button is visible. /ensure returns 'idempotent_noop' which
    // triggers a re-fetch of /status. The re-fetch returns 'indexed' and
    // the panel must land in 'ready' (NOT in a synthetic 'preparing' state).
    let statusCallCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/status")) {
        statusCallCount += 1;
        if (statusCallCount === 1) {
          return jsonResponse({ ok: true, status: "unavailable" });
        }
        return jsonResponse({ ok: true, status: "indexed", chunk_count: 12 });
      }
      if (url.endsWith("/ensure")) {
        return jsonResponse({ ok: true, status: "idempotent_noop" });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ArticleRagStatusPanel recordId="rec_x" generation={1} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("unavailable");
    });

    fireEvent.click(screen.getByTestId("article-rag-status-ensure"));

    // After idempotent_noop, the component must re-fetch /status and land
    // in 'ready' (not 'preparing').
    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("ready");
    });
    expect(screen.getByTestId("article-rag-status-meta").textContent).toContain(
      "已索引 12 块",
    );
  });

  it("ensure status 'error' or 'generation_mismatch' falls back to 'unavailable' (no debug fields rendered)", async () => {
    for (const ensureStatus of ["error", "generation_mismatch", "bootstrap_inconsistent", "record_not_found"]) {
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/status")) {
          return jsonResponse({ ok: true, status: "unavailable" });
        }
        if (url.endsWith("/ensure")) {
          return jsonResponse({
            ok: true,
            status: ensureStatus,
          });
        }
        return new Response(null, { status: 404 });
      });
      vi.stubGlobal("fetch", fetchMock);

      const { unmount } = render(
        <ArticleRagStatusPanel recordId={`rec_${ensureStatus}`} generation={1} />,
      );
      await waitFor(() => {
        expect(
          screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
        ).toBe("unavailable");
      });

      fireEvent.click(screen.getByTestId("article-rag-status-ensure"));

      await waitFor(() => {
        expect(
          screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
        ).toBe("unavailable");
      });
      // Debug fields must not surface anywhere.
      expect(screen.queryByText(/reason_code/)).toBeNull();
      expect(screen.queryByText(new RegExp(ensureStatus))).toBeNull();
      unmount();
    }
  });

  it("ensure button is truly disabled while a request is in flight (no double submit)", async () => {
    let resolveEnsure: ((v: Response) => void) | null = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/status")) {
        return jsonResponse({ ok: true, status: "unavailable" });
      }
      if (url.endsWith("/ensure")) {
        return new Promise<Response>((resolve) => {
          resolveEnsure = resolve as unknown as (v: Response) => void;
        });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ArticleRagStatusPanel recordId="rec_x" generation={1} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("unavailable");
    });

    fireEvent.click(screen.getByTestId("article-rag-status-ensure"));
    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("ensuring");
    });

    const ensuringButton = screen.getByTestId("article-rag-status-refresh");
    expect(ensuringButton).toBeTruthy();
    expect((ensuringButton as HTMLButtonElement).disabled).toBe(true);

    const ensureCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).endsWith("/ensure"),
    );
    expect(ensureCalls).toHaveLength(1);

    if (resolveEnsure) {
      const r = resolveEnsure as unknown as (v: unknown) => void;
      r(jsonResponse({ ok: true, status: "enqueued" }));
    }
  });

  it("ensure returns to 'unavailable' on failure (no debug fields rendered)", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/status")) {
        return jsonResponse({ ok: true, status: "unavailable" });
      }
      if (url.endsWith("/ensure")) {
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
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ArticleRagStatusPanel recordId="rec_x" generation={1} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("unavailable");
    });

    fireEvent.click(screen.getByTestId("article-rag-status-ensure"));

    await waitFor(() => {
      expect(
        screen.getByTestId("article-rag-status-panel").getAttribute("data-rag-status"),
      ).toBe("unavailable");
    });
    expect(screen.queryByText(/upstream_unavailable/)).toBeNull();
  });
});

describe("ArticleRagStatusPanel — route source guards", () => {
  it("status route calls getReaderArticleRagIndexStatusFromWeb and no legacy analysis path", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve: pathResolve } = await import("node:path");
    const source = readFileSync(
      pathResolve(
        process.cwd(),
        "src/app/api/web/reader-plate/records/[recordId]/article-rag-index/status/route.ts",
      ),
      "utf-8",
    );

    expect(source).toContain("getReaderArticleRagIndexStatusFromWeb");
    expect(source).toContain("recordId");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("analysis-tasks");
  });

  it("ensure route calls ensureReaderArticleRagIndexFromWeb and forwards expectedGeneration/indexVersion", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve: pathResolve } = await import("node:path");
    const source = readFileSync(
      pathResolve(
        process.cwd(),
        "src/app/api/web/reader-plate/records/[recordId]/article-rag-index/ensure/route.ts",
      ),
      "utf-8",
    );

    expect(source).toContain("ensureReaderArticleRagIndexFromWeb");
    expect(source).toContain("expectedGeneration");
    expect(source).toContain("indexVersion");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("analysis-tasks");
  });
});
