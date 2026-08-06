/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ReaderAskAttachment } from "@/lib/reader-plate";
import { askSelectionAnchorFingerprint } from "@/lib/reader-ask/selection-slots";

import {
  useAskComposerContext,
  type ReaderAskQuickActionRequest,
} from "./composer-context";

/**
 * Build a selection attachment with a canonical record anchor. Identity
 * is carried by the anchor fields — display label/selectedText never
 * drive slot decisions.
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

function noteAttachment(label = "笔记 A"): ReaderAskAttachment {
  return {
    kind: "annotation_ref",
    subtype: "reader_note",
    label,
    targetKey: `note-${label}`,
    metadata: {
      pageIdentity: { recordId: "record-1", surface: "reader" },
      sourceSurface: "note_menu",
      entryAction: "ask_about_this",
    },
  } as unknown as ReaderAskAttachment;
}

describe("useAskComposerContext", () => {
  it("auto-ingests a legitimate selection and does not reappear after ×-dismiss of the same fingerprint", () => {
    const candidate = selection({ id: "a" });
    const { result, rerender } = renderHook(
      ({ open, identityKey, selectionCandidate }) =>
        useAskComposerContext({ open, identityKey, selectionCandidate }),
      {
        initialProps: {
          open: true,
          identityKey: "record-1:base-1:2",
          selectionCandidate: candidate as ReaderAskAttachment | null,
        },
      },
    );

    expect(result.current.autoSelectionAttachment).toBe(candidate);

    act(() => {
      result.current.removeAutoSelection();
    });
    expect(result.current.autoSelectionAttachment).toBeNull();

    // Same still-active native selection must not re-ingest after dismiss.
    rerender({
      open: true,
      identityKey: "record-1:base-1:2",
      selectionCandidate: candidate,
    });
    expect(result.current.autoSelectionAttachment).toBeNull();

    // A genuinely new fingerprint re-opens the auto slot.
    const next = selection({
      id: "b",
      startOffset: 8,
      endOffset: 14,
      textHash: "22222222",
    });
    rerender({
      open: true,
      identityKey: "record-1:base-1:2",
      selectionCandidate: next,
    });
    expect(result.current.autoSelectionAttachment).toBe(next);
  });

  it("promotes the auto slot on pin without duplicating and respects the manual cap", () => {
    const first = selection({ id: "a" });
    const { result, rerender } = renderHook(
      ({ open, identityKey, selectionCandidate }) =>
        useAskComposerContext({ open, identityKey, selectionCandidate }),
      {
        initialProps: {
          open: true,
          identityKey: "record-1:base-1:2",
          selectionCandidate: first as ReaderAskAttachment | null,
        },
      },
    );

    expect(result.current.autoSelectionAttachment).toBe(first);

    act(() => {
      result.current.pinSelection();
    });
    expect(result.current.autoSelectionAttachment).toBeNull();
    expect(result.current.manualSelectionAttachments).toHaveLength(1);
    expect(
      askSelectionAnchorFingerprint(result.current.manualSelectionAttachments[0]),
    ).toBe(askSelectionAnchorFingerprint(first));

    // Fill remaining manual capacity with distinct fingerprints.
    const second = selection({
      id: "b",
      startOffset: 10,
      endOffset: 16,
      textHash: "22222222",
    });
    rerender({
      open: true,
      identityKey: "record-1:base-1:2",
      selectionCandidate: second,
    });
    act(() => {
      result.current.pinSelection();
    });

    const third = selection({
      id: "c",
      startOffset: 20,
      endOffset: 26,
      textHash: "33333333",
    });
    rerender({
      open: true,
      identityKey: "record-1:base-1:2",
      selectionCandidate: third,
    });
    act(() => {
      result.current.pinSelection();
    });
    expect(result.current.manualSelectionAttachments).toHaveLength(3);
    expect(result.current.pinSelectionState.disabled).toBe(false);

    const fourth = selection({
      id: "d",
      startOffset: 30,
      endOffset: 36,
      textHash: "44444444",
    });
    rerender({
      open: true,
      identityKey: "record-1:base-1:2",
      selectionCandidate: fourth,
    });
    // Auto-ingest still works while manuals are full; pin is blocked.
    expect(result.current.autoSelectionAttachment).toBe(fourth);
    expect(result.current.pinSelectionState.disabled).toBe(true);
    act(() => {
      result.current.pinSelection();
    });
    expect(result.current.manualSelectionAttachments).toHaveLength(3);
    expect(result.current.autoSelectionAttachment).toBe(fourth);
  });

  it("identity replacement clears auto/manual selection and fingerprints but keeps attachments and pending quick action", () => {
    const candidate = selection({ id: "a" });
    const note = noteAttachment();
    const pending: ReaderAskQuickActionRequest = {
      content: "解释选区",
      entryAction: "explain_this",
      attachments: [candidate],
      submissionMode: "quick_action",
    };

    const { result, rerender } = renderHook(
      ({ open, identityKey, selectionCandidate }) =>
        useAskComposerContext({ open, identityKey, selectionCandidate }),
      {
        initialProps: {
          open: true,
          identityKey: "record-1:base-1:2",
          selectionCandidate: candidate as ReaderAskAttachment | null,
        },
      },
    );

    act(() => {
      result.current.enter(note, pending);
      result.current.pinSelection();
    });
    expect(result.current.manualSelectionAttachments).toHaveLength(1);
    expect(result.current.attachments).toEqual([note]);
    expect(result.current.pendingQuickActionRequest).toEqual(pending);

    // Drop the live candidate so identity clear is observable without the
    // auto-ingest effect immediately re-filling the auto slot.
    rerender({
      open: true,
      identityKey: "record-1:base-1:2",
      selectionCandidate: null,
    });
    expect(result.current.manualSelectionAttachments).toHaveLength(1);

    // Identity fence: selection slots + fingerprints clear; draft/pending stay.
    rerender({
      open: true,
      identityKey: "record-1:base-1:3",
      selectionCandidate: null,
    });
    expect(result.current.autoSelectionAttachment).toBeNull();
    expect(result.current.manualSelectionAttachments).toEqual([]);
    expect(result.current.attachments).toEqual([note]);
    expect(result.current.pendingQuickActionRequest).toEqual(pending);

    // After identity change, the same fingerprint is free to re-ingest.
    rerender({
      open: true,
      identityKey: "record-1:base-1:3",
      selectionCandidate: candidate,
    });
    expect(result.current.autoSelectionAttachment).toBe(candidate);
  });

  it("enter / consumePendingQuickAction / buildSendAttachments combine attachments with selection slots", () => {
    const candidate = selection({ id: "a" });
    const note = noteAttachment("笔记 B");
    const pending: ReaderAskQuickActionRequest = {
      content: "总结",
      entryAction: "ask_about_this",
      attachments: [note],
      submissionMode: "quick_action",
    };

    const { result } = renderHook(() =>
      useAskComposerContext({
        open: true,
        identityKey: "record-1:base-1:2",
        selectionCandidate: candidate,
      }),
    );

    expect(result.current.autoSelectionAttachment).toBe(candidate);

    act(() => {
      result.current.enter(note, pending);
    });
    expect(result.current.attachments).toEqual([note]);
    expect(result.current.pendingQuickActionRequest).toEqual(pending);

    const merged = result.current.buildSendAttachments(result.current.attachments);
    expect(merged).toHaveLength(2);
    expect(merged).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: note.label }),
        expect.objectContaining({
          selectedText: candidate.selectedText,
        }),
      ]),
    );

    act(() => {
      result.current.consumePendingQuickAction();
    });
    expect(result.current.pendingQuickActionRequest).toBeNull();
    // Consuming the quick action must not wipe the draft attachments or slots.
    expect(result.current.attachments).toEqual([note]);
    expect(result.current.autoSelectionAttachment).toBe(candidate);
  });
});
