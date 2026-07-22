/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";

import {
  isReaderRecordNavigableNode,
  readerRecordNavigableNodeAttrs,
  READER_RECORD_ANCHOR_SEGMENT_ATTR,
  READER_RECORD_ANCHOR_SEGMENT_SELECTOR,
  READER_RECORD_NAVIGABLE_NODE_SELECTOR,
  READER_RECORD_NAV_NODE_ATTR,
  READER_RECORD_PLATE_DOCUMENT_SELECTOR,
  READER_RECORD_UNIT_ID_ATTR,
  READER_RECORD_UNIT_START_ATTR,
} from "@/lib/reader-plate/reader-record-dom-contract";

describe("reader-record-dom-contract (single source of truth)", () => {
  it("exposes the four contract attribute names and the derived selectors", () => {
    expect(READER_RECORD_NAV_NODE_ATTR).toBe("data-reader-record-node");
    expect(READER_RECORD_UNIT_ID_ATTR).toBe("data-unit-id");
    expect(READER_RECORD_UNIT_START_ATTR).toBe(
      "data-reader-record-unit-start",
    );
    expect(READER_RECORD_ANCHOR_SEGMENT_ATTR).toBe("data-anchor-segment-id");
    expect(READER_RECORD_NAVIGABLE_NODE_SELECTOR).toBe(
      "[data-reader-record-node][data-unit-id]",
    );
    expect(READER_RECORD_ANCHOR_SEGMENT_SELECTOR).toBe(
      "[data-anchor-segment-id]",
    );
    expect(READER_RECORD_PLATE_DOCUMENT_SELECTOR).toBe(
      ".reader-record-plate-document",
    );
  });

  it("props helper emits nodeKind always and only present, truthy hints", () => {
    expect(
      readerRecordNavigableNodeAttrs({
        nodeKind: "paragraph",
        unitId: "u1",
        isUnitStart: true,
        anchorSegmentId: "s1",
      }),
    ).toEqual({
      "data-reader-record-node": "paragraph",
      "data-unit-id": "u1",
      "data-reader-record-unit-start": "true",
      "data-anchor-segment-id": "s1",
    });

    // A future Markdown heading reuses the same helper, unchanged.
    expect(
      readerRecordNavigableNodeAttrs({ nodeKind: "heading", unitId: "u7" }),
    ).toEqual({
      "data-reader-record-node": "heading",
      "data-unit-id": "u7",
    });

    // Falsey hints are omitted (no unit id → not a navigable node).
    expect(
      readerRecordNavigableNodeAttrs({ nodeKind: "callout-group" }),
    ).toEqual({
      "data-reader-record-node": "callout-group",
    });
    expect(
      readerRecordNavigableNodeAttrs({
        nodeKind: "paragraph",
        unitId: "u1",
        isUnitStart: false,
        anchorSegmentId: null,
      }),
    ).toEqual({
      "data-reader-record-node": "paragraph",
      "data-unit-id": "u1",
    });
  });

  it("isReaderRecordNavigableNode requires the node attr AND a unit id", () => {
    const nav = document.createElement("p");
    nav.setAttribute(READER_RECORD_NAV_NODE_ATTR, "paragraph");
    nav.setAttribute(READER_RECORD_UNIT_ID_ATTR, "u1");
    expect(isReaderRecordNavigableNode(nav)).toBe(true);

    const group = document.createElement("div");
    group.setAttribute(READER_RECORD_NAV_NODE_ATTR, "callout-group");
    expect(isReaderRecordNavigableNode(group)).toBe(false);

    const chrome = document.createElement("button");
    chrome.setAttribute(READER_RECORD_UNIT_ID_ATTR, "u1"); // unit id alone ≠ navigable
    expect(isReaderRecordNavigableNode(chrome)).toBe(false);
  });

  it("the navigable selector matches exactly the nodes the predicate accepts", () => {
    const root = document.createElement("div");
    const para = document.createElement("p");
    para.setAttribute(READER_RECORD_NAV_NODE_ATTR, "paragraph");
    para.setAttribute(READER_RECORD_UNIT_ID_ATTR, "u1");
    const group = document.createElement("div");
    group.setAttribute(READER_RECORD_NAV_NODE_ATTR, "callout-group");
    root.append(para, group);

    const matched = root.querySelectorAll(
      READER_RECORD_NAVIGABLE_NODE_SELECTOR,
    );
    expect(matched).toHaveLength(1);
    expect(matched[0]).toBe(para);
  });
});
