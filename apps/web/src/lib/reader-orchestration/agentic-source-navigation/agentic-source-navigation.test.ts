/** @vitest-environment jsdom */

/**
 * Narrow typed-location navigation seam tests.
 *
 * The panel owns the secure endpoint call; this module is tested only
 * through its public interface (typed location in, typed result out).
 * DOM search/scroll policy itself lives in the adapter tests.
 */

import { describe, expect, it, vi } from "vitest";

import {
  createArticleLocationNavigator,
  sourceNavigationResultFromCitationNavigate,
  type NavigateToArticleLocation,
  type SourceNavigationResult,
} from "./agentic-source-navigation";
import type {
  DomNavigationCandidate,
  ReaderDomNavigationAdapter,
} from "./reader-dom-navigation-adapter";

function adapterReturning(
  hit: { mode: "anchor_segment" | "unit"; targetId: string } | null,
): { adapter: ReaderDomNavigationAdapter; calls: DomNavigationCandidate[][] } {
  const calls: DomNavigationCandidate[][] = [];
  return {
    calls,
    adapter: {
      resolveAndScroll(candidates) {
        calls.push([...candidates]);
        return hit;
      },
    },
  };
}

describe("createArticleLocationNavigator — candidate order", () => {
  it("tries the anchor segment before the unit when both are present", async () => {
    const { adapter, calls } = adapterReturning(null);
    const navigate = createArticleLocationNavigator({ domAdapter: adapter });

    await navigate({ unitId: "unit-1", anchorSegmentId: "anchor-1" });

    expect(calls).toEqual([
      [
        { mode: "anchor_segment", targetId: "anchor-1" },
        { mode: "unit", targetId: "unit-1" },
      ],
    ]);
  });

  it("navigates with only a unit id", async () => {
    const { adapter, calls } = adapterReturning({
      mode: "unit",
      targetId: "unit-1",
    });
    const navigate = createArticleLocationNavigator({ domAdapter: adapter });

    const result = await navigate({ unitId: "unit-1", anchorSegmentId: null });

    expect(calls).toEqual([[{ mode: "unit", targetId: "unit-1" }]]);
    expect(result).toEqual({
      status: "navigated",
      mode: "unit",
      targetId: "unit-1",
    });
  });

  it("navigates with only an anchor segment id", async () => {
    const { adapter } = adapterReturning({
      mode: "anchor_segment",
      targetId: "anchor-1",
    });
    const navigate = createArticleLocationNavigator({ domAdapter: adapter });

    const result = await navigate({ unitId: null, anchorSegmentId: "anchor-1" });

    expect(result).toEqual({
      status: "navigated",
      mode: "anchor_segment",
      targetId: "anchor-1",
    });
  });

  it("reports the adapter hit as navigated with its mode and target", async () => {
    const { adapter } = adapterReturning({
      mode: "anchor_segment",
      targetId: "anchor-9",
    });
    const navigate = createArticleLocationNavigator({ domAdapter: adapter });

    const result = await navigate({
      unitId: "unit-9",
      anchorSegmentId: "anchor-9",
    });

    expect(result).toEqual({
      status: "navigated",
      mode: "anchor_segment",
      targetId: "anchor-9",
    });
  });
});

describe("createArticleLocationNavigator — failure results", () => {
  it("returns target_not_found with attempted modes when the DOM has no target", async () => {
    const { adapter } = adapterReturning(null);
    const navigate = createArticleLocationNavigator({ domAdapter: adapter });

    const result = await navigate({
      unitId: "unit-missing",
      anchorSegmentId: "anchor-missing",
    });

    expect(result).toEqual({
      status: "target_not_found",
      attemptedModes: ["anchor_segment", "unit"],
    });
  });

  it("returns unavailable without touching the DOM when no locator exists", async () => {
    const { adapter, calls } = adapterReturning(null);
    const navigate = createArticleLocationNavigator({ domAdapter: adapter });

    const result = await navigate({ unitId: null, anchorSegmentId: null });

    expect(result).toEqual({ status: "unavailable", reason: "no_locator" });
    expect(calls).toEqual([]);
  });

  it("treats empty-string locators as absent", async () => {
    const { adapter, calls } = adapterReturning(null);
    const navigate = createArticleLocationNavigator({ domAdapter: adapter });

    const result = await navigate({ unitId: "", anchorSegmentId: "" });

    expect(result).toEqual({ status: "unavailable", reason: "no_locator" });
    expect(calls).toEqual([]);
  });
});

describe("createArticleLocationNavigator — default adapter", () => {
  it("resolves against the live plate document with the shared DOM contract", async () => {
    document.body.innerHTML = `
      <div class="reader-record-plate-document">
        <p data-reader-record-node="paragraph" data-unit-id="unit-live" data-reader-record-unit-start="true">unit</p>
        <span data-anchor-segment-id="anchor-live">anchor</span>
      </div>
    `;
    const scrollSpy = vi.fn();
    const focusSpy = vi.fn();
    const anchor = document.querySelector<HTMLElement>(
      "[data-anchor-segment-id='anchor-live']",
    );
    anchor!.scrollIntoView = scrollSpy;
    anchor!.focus = focusSpy;

    const navigate = createArticleLocationNavigator();
    const result = await navigate({
      unitId: "unit-live",
      anchorSegmentId: "anchor-live",
    });

    expect(result).toEqual({
      status: "navigated",
      mode: "anchor_segment",
      targetId: "anchor-live",
    });
    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(focusSpy).toHaveBeenCalledTimes(1);
    document.body.innerHTML = "";
  });

  it("fail-closes to target_not_found when the plate document has no match", async () => {
    document.body.innerHTML = `<div class="reader-record-plate-document"></div>`;
    const navigate = createArticleLocationNavigator();

    const result = await navigate({ unitId: "unit-absent", anchorSegmentId: null });

    expect(result).toEqual({
      status: "target_not_found",
      attemptedModes: ["unit"],
    });
    document.body.innerHTML = "";
  });
});

describe("sourceNavigationResultFromCitationNavigate", () => {
  it("maps identity_mismatch with the server reason field", () => {
    expect(
      sourceNavigationResultFromCitationNavigate({
        status: "identity_mismatch",
        reason: "base",
      }),
    ).toEqual({ status: "identity_mismatch", field: "base" });
    expect(
      sourceNavigationResultFromCitationNavigate({
        status: "identity_mismatch",
        reason: "stable_document",
      }),
    ).toEqual({ status: "identity_mismatch", field: "stable_document" });
  });

  it("defaults identity_mismatch field to reading_record for other reasons", () => {
    expect(
      sourceNavigationResultFromCitationNavigate({
        status: "identity_mismatch",
        reason: "reading_record",
      }),
    ).toEqual({ status: "identity_mismatch", field: "reading_record" });
    expect(
      sourceNavigationResultFromCitationNavigate({
        status: "identity_mismatch",
        reason: null,
      }),
    ).toEqual({ status: "identity_mismatch", field: "reading_record" });
  });

  it("maps stale_generation", () => {
    expect(
      sourceNavigationResultFromCitationNavigate({ status: "stale_generation" }),
    ).toEqual({ status: "stale_generation" });
  });

  it("maps not_found to a safe generic unavailable result", () => {
    expect(
      sourceNavigationResultFromCitationNavigate({
        status: "not_found",
        reason: "citation_not_found",
      }),
    ).toEqual({ status: "unavailable", reason: "no_locator" });
  });

  it("keeps legacy_scope_missing distinguishable for feedback", () => {
    expect(
      sourceNavigationResultFromCitationNavigate({
        status: "unavailable",
        reason: "legacy_scope_missing",
      }),
    ).toEqual({ status: "unavailable", reason: "legacy_scope_missing" });
  });

  it("maps transient fence gaps to page_identity_incomplete", () => {
    for (const reason of [
      "record_fence_unavailable",
      "live_stable_document_missing",
    ] as const) {
      expect(
        sourceNavigationResultFromCitationNavigate({
          status: "unavailable",
          reason,
        }),
      ).toEqual({ status: "unavailable", reason: "page_identity_incomplete" });
    }
  });

  it("maps unknown statuses and reasons to the safe generic result", () => {
    expect(
      sourceNavigationResultFromCitationNavigate({
        status: "something_else",
        reason: "internal_detail",
      }),
    ).toEqual({ status: "unavailable", reason: "no_locator" });
    expect(
      sourceNavigationResultFromCitationNavigate({ status: "unavailable" }),
    ).toEqual({ status: "unavailable", reason: "no_locator" });
  });
});

describe("createArticleLocationNavigator — privacy / interface", () => {
  it("Ask-facing callback accepts only the typed location (no Element/Document/identity)", async () => {
    const navigate: NavigateToArticleLocation = createArticleLocationNavigator({
      domAdapter: adapterReturning(null).adapter,
    });
    // Type-level: NavigateToArticleLocation is (location) => Promise<result>.
    // Runtime: extra caller fields are ignored, not forwarded to the adapter.
    const location = {
      unitId: "unit-1",
      anchorSegmentId: null,
      document,
      evidenceScope: {},
    } as unknown as Parameters<NavigateToArticleLocation>[0];
    const result: SourceNavigationResult = await navigate(location);
    expect(result.status).toBe("target_not_found");
  });
});
