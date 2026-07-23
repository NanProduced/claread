/**
 * @vitest-environment jsdom
 *
 * Integration test for ReaderStableSourcePreview.
 *
 * Verifies the M2 wiring: the component fetches stable-document blocks from
 * the BFF route, adapts them via `adaptStableBlocksToStructuredSource`, and
 * renders `StructuredSourceRenderer`. Also verifies fail-closed behavior
 * (null on error / empty / identity mismatch).
 *
 * Reference: docs/tmp/TMP-reader-markdown-rich-input-refactor-plan-2026-07-22.md §5 M2
 */

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReaderStableSourcePreview } from "@/components/reader/plate/ReaderStableSourcePreview";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function makeStableBlock(overrides: Partial<Record<string, unknown>> & { block_id: string }) {
  return {
    block_id: overrides.block_id,
    parent_block_id: overrides.parent_block_id ?? null,
    order_index: overrides.order_index ?? 0,
    block_type: overrides.block_type ?? "paragraph",
    text_content: overrides.text_content ?? "Hello",
    payload: overrides.payload ?? {},
    source_refs: overrides.source_refs ?? { line_start: 1, line_end: 1 },
    quality: overrides.quality ?? {},
    canonical_text_start_utf16: overrides.canonical_text_start_utf16 ?? null,
    canonical_text_end_utf16: overrides.canonical_text_end_utf16 ?? null,
    interpretation_policy: overrides.interpretation_policy ?? {},
  };
}

function mockFetchOk(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mockFetchNotFound() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: false,
    status: 404,
    json: () => Promise.resolve({ ok: false, status: 404 }),
  } as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mockFetchReject() {
  const fetchMock = vi.fn().mockRejectedValue(new Error("network"));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ReaderStableSourcePreview (M2 integration)", () => {
  it("fetches stable-document blocks, adapts, and renders StructuredSourceRenderer", async () => {
    const blocks = [
      makeStableBlock({
        block_id: "b1",
        block_type: "heading",
        text_content: "Title",
        payload: { level: 1 },
        source_refs: { line_start: 1, line_end: 1 },
      }),
      makeStableBlock({
        block_id: "b2",
        block_type: "paragraph",
        text_content: "Body text",
        source_refs: { line_start: 2, line_end: 2 },
      }),
    ];

    mockFetchOk({
      ok: true,
      reading_record_id: "rec-1",
      record_generation: 1,
      active_base_id: "base-1",
      stable_document: { stable_document_id: "sd-1" },
      blocks,
      anchor_segments: [],
    });

    render(<ReaderStableSourcePreview recordId="rec-1" />);

    await waitFor(() => {
      expect(screen.getByTestId("reader-stable-source-preview")).toBeTruthy();
    });

    // StructuredSourceRenderer renders blocks with data-block-id.
    expect(screen.getByTestId("structured-source-renderer")).toBeTruthy();
    expect(screen.getByText("Title")).toBeTruthy();
    expect(screen.getByText("Body text")).toBeTruthy();
  });

  it("renders nothing when blocks array is empty", async () => {
    mockFetchOk({
      ok: true,
      reading_record_id: "rec-1",
      record_generation: 1,
      active_base_id: "base-1",
      stable_document: { stable_document_id: "sd-1" },
      blocks: [],
      anchor_segments: [],
    });

    const { container } = render(<ReaderStableSourcePreview recordId="rec-1" />);

    // Wait a tick for the fetch to resolve.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(container.querySelector("[data-testid='reader-stable-source-preview']")).toBeNull();
  });

  it("renders nothing on 404 (stable document not ready)", async () => {
    mockFetchNotFound();

    const { container } = render(<ReaderStableSourcePreview recordId="rec-1" />);

    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(container.querySelector("[data-testid='reader-stable-source-preview']")).toBeNull();
  });

  it("renders nothing on network error (fail-closed)", async () => {
    mockFetchReject();

    const { container } = render(<ReaderStableSourcePreview recordId="rec-1" />);

    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(container.querySelector("[data-testid='reader-stable-source-preview']")).toBeNull();
  });

  it("renders nothing when response ok=false (fail-closed)", async () => {
    mockFetchOk({ ok: false, status: 500 });

    const { container } = render(<ReaderStableSourcePreview recordId="rec-1" />);

    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(container.querySelector("[data-testid='reader-stable-source-preview']")).toBeNull();
  });

  it("renders nothing on identity mismatch (reading_record_id mismatch)", async () => {
    mockFetchOk({
      ok: true,
      reading_record_id: "different-record",
      record_generation: 1,
      active_base_id: "base-1",
      stable_document: { stable_document_id: "sd-1" },
      blocks: [makeStableBlock({ block_id: "b1" })],
      anchor_segments: [],
    });

    const { container } = render(<ReaderStableSourcePreview recordId="rec-1" />);

    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(container.querySelector("[data-testid='reader-stable-source-preview']")).toBeNull();
  });

  it("fetches from the correct URL with recordId", async () => {
    const fetchMock = mockFetchOk({
      ok: true,
      reading_record_id: "rec-42",
      record_generation: 1,
      active_base_id: "base-1",
      stable_document: { stable_document_id: "sd-1" },
      blocks: [makeStableBlock({ block_id: "b1" })],
      anchor_segments: [],
    });

    render(<ReaderStableSourcePreview recordId="rec-42" />);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/web/reader-plate/records/rec-42/stable-document",
        expect.objectContaining({ method: "GET" }),
      );
    });
  });

  it("renders table/list/code_block from adapted stable-document blocks", async () => {
    const blocks = [
      makeStableBlock({
        block_id: "b1",
        block_type: "list",
        text_content: null,
        payload: { ordered: false, depth: 0 },
        source_refs: { line_start: 1, line_end: 2 },
      }),
      makeStableBlock({
        block_id: "b2",
        block_type: "list_item",
        text_content: "Item",
        parent_block_id: "b1",
        payload: { ordered: false, marker: "-", ordinal: null, depth: 0 },
        source_refs: { line_start: 1, line_end: 1 },
      }),
      makeStableBlock({
        block_id: "b3",
        block_type: "code_block",
        text_content: 'print("hi")',
        payload: { language: "python" },
        source_refs: { line_start: 3, line_end: 4 },
      }),
    ];

    mockFetchOk({
      ok: true,
      reading_record_id: "rec-1",
      record_generation: 1,
      active_base_id: "base-1",
      stable_document: { stable_document_id: "sd-1" },
      blocks,
      anchor_segments: [],
    });

    render(<ReaderStableSourcePreview recordId="rec-1" />);

    await waitFor(() => {
      expect(screen.getByTestId("reader-stable-source-preview")).toBeTruthy();
    });

    // List renders as <ul> with list items.
    const list = screen.getByTestId("structured-source-blocks").querySelector("ul");
    expect(list).toBeTruthy();
    expect(list?.querySelector("li")?.textContent).toBe("Item");

    // Code block renders as <pre>.
    const pre = screen.getByTestId("structured-source-blocks").querySelector("pre");
    expect(pre).toBeTruthy();
  });
});
