/**
 * Ask composer send-context module.
 *
 * Owns the Ask-internal send-context state machine: the composer attachment
 * draft, selection slots (auto 0/1 + manual ≤3, anchor-fingerprint dedupe),
 * the quick-action request queue, and the send-time context merge. The plate
 * stays a page/surface owner: it adapts its selection / dictionary / note
 * domains into ReaderAskAttachment values and reports them here.
 *
 * Decision policy lives in lib/reader-ask/selection-slots (pure); this hook
 * is its single stateful host.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  askAttachmentKey,
  type ReaderAskAttachment,
} from "@/lib/reader-plate";
import {
  askSelectionAnchorFingerprint,
  decideAutoSelectionIngest,
  decidePinSelection,
  MAX_MANUAL_ASK_SELECTIONS,
} from "@/lib/reader-ask/selection-slots";
import { mergeAttachments } from "@/lib/reader-ask/send-request";
import type { ReaderAskEntryActionDto } from "@/types/api/reader-ask";

export type ReaderAskQuickActionRequest = {
  content: string;
  entryAction: ReaderAskEntryActionDto;
  attachments: ReaderAskAttachment[];
  submissionMode?: "chat" | "quick_action";
};

export type AskComposerPinState = {
  disabled: boolean;
  reason?: string;
};

export interface AskComposerContext {
  /** Explicit composer attachments (notes / vocabulary / note-menu refs). */
  attachments: ReaderAskAttachment[];
  /** Auto slot: latest legitimate single-range source selection (0/1). */
  autoSelectionAttachment: ReaderAskAttachment | null;
  /** Manual slots: toolbar-pinned selections (≤3). */
  manualSelectionAttachments: ReaderAskAttachment[];
  /** Queued quick-action send, consumed by the panel once dispatchable. */
  pendingQuickActionRequest: ReaderAskQuickActionRequest | null;
  /** Toolbar pin action state derived from the slot machine. */
  pinSelectionState: AskComposerPinState;
  /** Open the composer with an optional attachment draft + quick action. */
  enter: (
    attachment?: ReaderAskAttachment | null,
    pendingRequest?: ReaderAskQuickActionRequest | null,
  ) => void;
  /** "加入 Ask Claread": pin the live (or auto) selection per slot policy. */
  pinSelection: () => void;
  removeAutoSelection: () => void;
  removeManualSelection: (attachmentKey: string) => void;
  removeAttachment: (attachmentKey: string) => void;
  clearAttachments: () => void;
  consumePendingQuickAction: () => void;
  /** Send-time context: explicit attachments merged with the slot drafts. */
  buildSendAttachments: (base: ReaderAskAttachment[]) => ReaderAskAttachment[];
}

export type UseAskComposerContextArgs = {
  /** Panel open state — the auto slot only ingests while Ask is open. */
  open: boolean;
  /**
   * Record/base/generation fence. Auto/manual selection slots and their
   * fingerprints are bound to one identity; a replacement clears those
   * selection slots and fingerprints only (explicit attachments and a
   * pending quick action are left alone).
   */
  identityKey: string;
  /** Plate-adapted live single-range selection candidate (or null). */
  selectionCandidate: ReaderAskAttachment | null;
};

export function useAskComposerContext({
  open,
  identityKey,
  selectionCandidate,
}: UseAskComposerContextArgs): AskComposerContext {
  const [attachments, setAttachments] = useState<ReaderAskAttachment[]>([]);
  const [autoSelectionAttachment, setAutoSelectionAttachment] =
    useState<ReaderAskAttachment | null>(null);
  const [manualSelectionAttachments, setManualSelectionAttachments] = useState<
    ReaderAskAttachment[]
  >([]);
  const [pendingQuickActionRequest, setPendingQuickActionRequest] =
    useState<ReaderAskQuickActionRequest | null>(null);
  // Fingerprint the user ×-dismissed from the auto slot: the SAME still-
  // active native selection must not auto reappear until a genuinely new
  // fingerprint selection happens.
  const dismissedAutoSelectionFingerprintRef = useRef<string | null>(null);
  // Last fingerprint written to the auto slot (avoids rewrite churn when
  // the bridge re-emits the same selection).
  const lastAutoSelectionFingerprintRef = useRef<string | null>(null);

  // Identity replacement clears auto/manual selection slots and their
  // fingerprints only. Explicit attachments and pending quick actions
  // survive. Browser highlight dismissal does NOT clear slots.
  useEffect(() => {
    setAutoSelectionAttachment(null);
    setManualSelectionAttachments([]);
    dismissedAutoSelectionFingerprintRef.current = null;
    lastAutoSelectionFingerprintRef.current = null;
  }, [identityKey]);

  // Auto-ingest a legitimate stable single-range source selection into
  // the composer auto slot while Ask is open (also covers opening the
  // panel with an active selection). Clearing the browser highlight /
  // Esc / blank clicks merely null the bridge result — the chip is NOT
  // cleared. A new fingerprint replaces the auto slot without touching
  // manual selections, notes, or external attachments. A ×-dismissed
  // fingerprint never reappears until a genuinely new fingerprint
  // selection happens.
  useEffect(() => {
    if (!open) {
      return;
    }
    const decision = decideAutoSelectionIngest({
      candidate: selectionCandidate,
      currentFingerprint: lastAutoSelectionFingerprintRef.current,
      dismissedFingerprint: dismissedAutoSelectionFingerprintRef.current,
    });
    if (decision.kind !== "ingest") {
      return;
    }
    // A new valid selection clears the dismissal — returning to the old
    // range afterwards counts as a fresh selection again.
    dismissedAutoSelectionFingerprintRef.current = null;
    lastAutoSelectionFingerprintRef.current = decision.fingerprint;
    setAutoSelectionAttachment(decision.attachment);
  }, [open, selectionCandidate]);

  const enter = useCallback(
    (
      attachment?: ReaderAskAttachment | null,
      pendingRequest?: ReaderAskQuickActionRequest | null,
    ) => {
      if (attachment === null) {
        setAttachments([]);
      } else if (attachment) {
        setAttachments([attachment]);
      }
      setPendingQuickActionRequest(pendingRequest ?? null);
    },
    [],
  );

  const removeAutoSelection = useCallback(() => {
    setAutoSelectionAttachment((current) => {
      if (current) {
        dismissedAutoSelectionFingerprintRef.current =
          askSelectionAnchorFingerprint(current);
      }
      return null;
    });
  }, []);

  const removeManualSelection = useCallback((attachmentKey: string) => {
    setManualSelectionAttachments((current) =>
      current.filter((attachment) => askAttachmentKey(attachment) !== attachmentKey),
    );
  }, []);

  const removeAttachment = useCallback((attachmentKey: string) => {
    setAttachments((current) =>
      current.filter((attachment) => askAttachmentKey(attachment) !== attachmentKey),
    );
  }, []);

  const clearAttachments = useCallback(() => {
    setAttachments([]);
  }, []);

  const consumePendingQuickAction = useCallback(() => {
    setPendingQuickActionRequest(null);
  }, []);

  /**
   * "加入 Ask Claread": pin the current selection into the manual slots.
   * If it IS the current auto slot it is promoted (no duplicate chip);
   * otherwise appended. Anchor-fingerprint dedupe; capped at
   * MAX_MANUAL_ASK_SELECTIONS. The caller opens the panel; this hook only
   * mutates the slots.
   */
  const pinSelection = useCallback(() => {
    // Opening the toolbar menu moves focus away from the document and may
    // collapse the native Selection before the menu item is chosen. The
    // auto slot is the Host-owned frozen copy of that same selection, so
    // it is the safe fallback for the explicit pin action.
    const candidate = selectionCandidate ?? autoSelectionAttachment;
    if (!candidate) {
      return;
    }
    const decision = decidePinSelection({
      candidate,
      autoSelection: autoSelectionAttachment,
      manualSelections: manualSelectionAttachments,
    });
    switch (decision.kind) {
      case "noop":
        return;
      case "blocked-full":
        return;
      case "promote":
        setAutoSelectionAttachment(null);
        setManualSelectionAttachments((current) => [...current, candidate]);
        break;
      case "append":
        setManualSelectionAttachments((current) => [...current, candidate]);
        break;
      case "already-manual":
        break;
    }
    // The pinned selection must not reappear in the auto slot while the
    // same native selection is still active.
    dismissedAutoSelectionFingerprintRef.current = decision.fingerprint;
    lastAutoSelectionFingerprintRef.current = decision.fingerprint;
  }, [autoSelectionAttachment, manualSelectionAttachments, selectionCandidate]);

  const pinSelectionState = useMemo<AskComposerPinState>(() => {
    const decision = decidePinSelection({
      candidate: selectionCandidate ?? autoSelectionAttachment,
      autoSelection: autoSelectionAttachment,
      manualSelections: manualSelectionAttachments,
    });
    const full = manualSelectionAttachments.length >= MAX_MANUAL_ASK_SELECTIONS;
    return {
      disabled: decision.kind === "blocked-full",
      reason: decision.kind === "blocked-full" || full ? "最多固定 3 个选区" : undefined,
    };
  }, [autoSelectionAttachment, manualSelectionAttachments, selectionCandidate]);

  const buildSendAttachments = useCallback(
    (base: ReaderAskAttachment[]) => {
      const selectionSlotAttachments = [
        ...(autoSelectionAttachment ? [autoSelectionAttachment] : []),
        ...manualSelectionAttachments,
      ];
      if (selectionSlotAttachments.length === 0) {
        return base;
      }
      return mergeAttachments(base, selectionSlotAttachments);
    },
    [autoSelectionAttachment, manualSelectionAttachments],
  );

  return {
    attachments,
    autoSelectionAttachment,
    manualSelectionAttachments,
    pendingQuickActionRequest,
    pinSelectionState,
    enter,
    pinSelection,
    removeAutoSelection,
    removeManualSelection,
    removeAttachment,
    clearAttachments,
    consumePendingQuickAction,
    buildSendAttachments,
  };
}
