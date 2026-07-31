/** @vitest-environment node */
import { describe, expect, it } from "vitest";

import type { ReaderAskAttachment } from "@/lib/reader-plate/bridges/ask/types";

import {
  askSelectionAnchorFingerprint,
  decideAutoSelectionIngest,
  decidePinSelection,
  MAX_MANUAL_ASK_SELECTIONS,
} from "./selection-slots";

/**
 * Build a selection attachment with a canonical record anchor. Identity
 * is carried by the anchor fields (record/base/generation/unit/segment/
 * offsets/hash) — the display label/selectedText must never drive it.
 */
function selection(
  overrides: {
    id?: string;
    startOffset?: number;
    endOffset?: number;
    textHash?: string;
    selectedText?: string;
    anchorSegmentId?: string;
    unitId?: string;
  } = {},
): ReaderAskAttachment {
  const id = overrides.id ?? "a";
  const selectedText = overrides.selectedText ?? `text-${id}`;
  return {
    kind: "text_selection",
    subtype: "text_range",
    label: selectedText,
    selectedText,
    targetKey: `segment-${overrides.anchorSegmentId ?? "seg-1"}`,
    metadata: {
      pageIdentity: { recordId: "record-1", surface: "reader" },
      sourceSurface: "selection_toolbar",
      entryAction: "ask_about_this",
      readingRecordAnchor: {
        record_id: "record-1",
        base_id: "base-1",
        generation: 2,
        unit_id: overrides.unitId ?? "unit-1",
        anchor_segment_id: overrides.anchorSegmentId ?? "seg-1",
        start_offset: overrides.startOffset ?? 0,
        end_offset: overrides.endOffset ?? 6,
        offset_unit: "utf16",
        selected_text: selectedText,
        text_hash: overrides.textHash ?? "11111111",
        hash_algorithm: "fnv1a32-utf16",
        scope: "stable_source",
      },
    },
  } as unknown as ReaderAskAttachment;
}

describe("askSelectionAnchorFingerprint", () => {
  it("is stable for identical anchors and insensitive to display text", () => {
    const first = selection({ id: "x", selectedText: "选区文本一" });
    const second = selection({ id: "y", selectedText: "完全不同的显示文本" });
    // Same anchor offsets/hash ⇒ same fingerprint even though the labels
    // differ (dedupe is anchor-based, never text-based).
    expect(askSelectionAnchorFingerprint(first)).toBe(
      askSelectionAnchorFingerprint(second),
    );
  });

  it("differs when the anchor range differs", () => {
    expect(
      askSelectionAnchorFingerprint(selection({ startOffset: 0, endOffset: 6 })),
    ).not.toBe(
      askSelectionAnchorFingerprint(selection({ startOffset: 2, endOffset: 8 })),
    );
    expect(
      askSelectionAnchorFingerprint(selection({ textHash: "11111111" })),
    ).not.toBe(
      askSelectionAnchorFingerprint(selection({ textHash: "22222222" })),
    );
    expect(
      askSelectionAnchorFingerprint(selection({ anchorSegmentId: "seg-1" })),
    ).not.toBe(
      askSelectionAnchorFingerprint(selection({ anchorSegmentId: "seg-2" })),
    );
  });

  it("returns null without a record anchor (non-selection attachments)", () => {
    expect(askSelectionAnchorFingerprint(null)).toBeNull();
    const noteRef = {
      kind: "annotation_ref",
      subtype: "reader_note",
      label: "笔记",
      metadata: {},
    } as unknown as ReaderAskAttachment;
    expect(askSelectionAnchorFingerprint(noteRef)).toBeNull();
  });
});

describe("decideAutoSelectionIngest", () => {
  it("ingests the first legitimate selection", () => {
    const candidate = selection({ id: "a" });
    const decision = decideAutoSelectionIngest({
      candidate,
      currentFingerprint: null,
      dismissedFingerprint: null,
    });
    expect(decision).toEqual({
      kind: "ingest",
      attachment: candidate,
      fingerprint: askSelectionAnchorFingerprint(candidate),
    });
  });

  it("skips when there is no candidate (highlight dismissed — chip stays)", () => {
    expect(
      decideAutoSelectionIngest({
        candidate: null,
        currentFingerprint: "fp",
        dismissedFingerprint: null,
      }),
    ).toEqual({ kind: "skip" });
  });

  it("replaces the auto slot on a NEW fingerprint selection", () => {
    const current = selection({ id: "a" });
    const next = selection({ id: "b", startOffset: 10, endOffset: 20 });
    const decision = decideAutoSelectionIngest({
      candidate: next,
      currentFingerprint: askSelectionAnchorFingerprint(current),
      dismissedFingerprint: null,
    });
    expect(decision.kind).toBe("ingest");
    if (decision.kind === "ingest") {
      expect(decision.attachment).toBe(next);
    }
  });

  it("does not rewrite the slot for the same fingerprint (bridge re-emit)", () => {
    const current = selection({ id: "a" });
    expect(
      decideAutoSelectionIngest({
        candidate: selection({ id: "a-dup" }),
        currentFingerprint: askSelectionAnchorFingerprint(current),
        dismissedFingerprint: null,
      }),
    ).toEqual({ kind: "skip" });
  });

  it("never auto-restores a ×-dismissed fingerprint until a new selection happens", () => {
    const dismissed = selection({ id: "a" });
    const fingerprint = askSelectionAnchorFingerprint(dismissed);
    // Same still-active selection after × → skip.
    expect(
      decideAutoSelectionIngest({
        candidate: selection({ id: "a-again" }),
        currentFingerprint: null,
        dismissedFingerprint: fingerprint,
      }),
    ).toEqual({ kind: "skip" });
    // A genuinely new selection ingests (the caller clears the dismissal,
    // so returning to the old range afterwards counts as fresh again).
    const fresh = selection({ id: "b", startOffset: 30, endOffset: 40 });
    const freshDecision = decideAutoSelectionIngest({
      candidate: fresh,
      currentFingerprint: null,
      dismissedFingerprint: fingerprint,
    });
    expect(freshDecision.kind).toBe("ingest");
  });

  it("skips candidates without an anchor fingerprint", () => {
    const anchorless = {
      kind: "text_selection",
      subtype: "text_range",
      label: "无锚点",
      metadata: {},
    } as unknown as ReaderAskAttachment;
    expect(
      decideAutoSelectionIngest({
        candidate: anchorless,
        currentFingerprint: null,
        dismissedFingerprint: null,
      }),
    ).toEqual({ kind: "skip" });
  });
});

describe("decidePinSelection", () => {
  it("noops without a candidate", () => {
    expect(
      decidePinSelection({ candidate: null, autoSelection: null, manualSelections: [] }),
    ).toEqual({ kind: "noop" });
  });

  it("promotes the current auto selection (no duplicate chip)", () => {
    const auto = selection({ id: "a" });
    const decision = decidePinSelection({
      candidate: selection({ id: "a-same-range" }),
      autoSelection: auto,
      manualSelections: [],
    });
    expect(decision).toEqual({
      kind: "promote",
      fingerprint: askSelectionAnchorFingerprint(auto),
    });
  });

  it("appends a new selection under the cap", () => {
    const candidate = selection({ id: "c" });
    expect(
      decidePinSelection({
        candidate,
        autoSelection: null,
        manualSelections: [selection({ id: "m1", startOffset: 1, endOffset: 2 })],
      }),
    ).toEqual({
      kind: "append",
      fingerprint: askSelectionAnchorFingerprint(candidate),
    });
  });

  it("is idempotent for an already-pinned manual selection", () => {
    const manual = selection({ id: "m1", startOffset: 1, endOffset: 2 });
    expect(
      decidePinSelection({
        // Same anchor range, different display label ⇒ same selection.
        candidate: selection({ id: "m1-dup", startOffset: 1, endOffset: 2 }),
        autoSelection: null,
        manualSelections: [manual],
      }),
    ).toEqual({
      kind: "already-manual",
      fingerprint: askSelectionAnchorFingerprint(manual),
    });
  });

  it("blocks a new pin at the 3-slot cap", () => {
    expect(MAX_MANUAL_ASK_SELECTIONS).toBe(3);
    const manuals = [
      selection({ id: "m1", startOffset: 1, endOffset: 2 }),
      selection({ id: "m2", startOffset: 3, endOffset: 4 }),
      selection({ id: "m3", startOffset: 5, endOffset: 6 }),
    ];
    const fresh = selection({ id: "m4", startOffset: 7, endOffset: 8 });
    expect(
      decidePinSelection({ candidate: fresh, autoSelection: null, manualSelections: manuals }),
    ).toEqual({
      kind: "blocked-full",
      fingerprint: askSelectionAnchorFingerprint(fresh),
    });
  });

  it("blocks auto promotion when all three manual slots are already full", () => {
    const manuals = [
      selection({ id: "m1", startOffset: 1, endOffset: 2 }),
      selection({ id: "m2", startOffset: 3, endOffset: 4 }),
      selection({ id: "m3", startOffset: 5, endOffset: 6 }),
    ];
    // Promoting the auto slot would create a fourth manual chip, violating
    // the independent manual cap. Keep the auto chip and report full.
    const auto = selection({ id: "auto", startOffset: 9, endOffset: 10 });
    expect(
      decidePinSelection({
        candidate: selection({ id: "auto-dup", startOffset: 9, endOffset: 10 }),
        autoSelection: auto,
        manualSelections: manuals,
      }).kind,
    ).toBe("blocked-full");
    // Re-pinning an existing manual selection stays idempotent at the cap.
    expect(
      decidePinSelection({
        candidate: selection({ id: "m2-dup", startOffset: 3, endOffset: 4 }),
        autoSelection: null,
        manualSelections: manuals,
      }).kind,
    ).toBe("already-manual");
  });

  it("dedupes by anchor fingerprint, not by display text", () => {
    const manual = selection({
      id: "m1",
      selectedText: "同一段原文文本",
      startOffset: 1,
      endOffset: 9,
    });
    // Same display text, DIFFERENT anchor range ⇒ distinct selection.
    const sameTextDifferentRange = selection({
      id: "other",
      selectedText: "同一段原文文本",
      startOffset: 40,
      endOffset: 48,
    });
    expect(
      decidePinSelection({
        candidate: sameTextDifferentRange,
        autoSelection: null,
        manualSelections: [manual],
      }).kind,
    ).toBe("append");
    // Same anchor range, different display text ⇒ same selection.
    const sameRangeDifferentText = selection({
      id: "dup",
      selectedText: "另一段显示文本",
      startOffset: 1,
      endOffset: 9,
    });
    expect(
      decidePinSelection({
        candidate: sameRangeDifferentText,
        autoSelection: null,
        manualSelections: [manual],
      }).kind,
    ).toBe("already-manual");
  });
});
