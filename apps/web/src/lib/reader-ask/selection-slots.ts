/**
 * ASK-UX-COT-COMPOSER-R3 P1 — Reading Record Ask composer selection slots.
 *
 * Pure decision logic for the composer's selection context model:
 * - a permanent implicit current-article context (never a slot here);
 * - ONE auto slot (0/1): the latest legitimate stable single-range source
 *   selection. Replaced by the next NEW selection; immune to browser
 *   highlight dismissal (Esc / blank click / collapse). A ×-dismissed
 *   fingerprint never auto-reappears until a genuinely new fingerprint
 *   selection happens.
 * - up to THREE manual slots: toolbar-pinned ("加入 Ask Claread"),
 *   deduped by stable anchor fingerprint (never by display text),
 *   surviving panel toggles, highlight dismissal, and message sends.
 *
 * Identity = the server-verifiable anchor (record/base/generation/unit/
 * segment/offsets/hash), never the selected text.
 */

import type { ReaderAskAttachment } from "@/lib/reader-plate/bridges/ask/types";

export const MAX_MANUAL_ASK_SELECTIONS = 3;

/**
 * Stable fingerprint of a selection attachment's record anchor. Returns
 * null when the attachment carries no record anchor (non-selection
 * attachments are not selection-slot material).
 */
export function askSelectionAnchorFingerprint(
  attachment: ReaderAskAttachment | null | undefined,
): string | null {
  const anchor = attachment?.metadata.readingRecordAnchor as
    | Record<string, unknown>
    | null
    | undefined;
  if (!anchor || typeof anchor !== "object") {
    return null;
  }
  return JSON.stringify([
    anchor.record_id,
    anchor.base_id,
    anchor.generation,
    anchor.unit_id,
    anchor.anchor_segment_id,
    anchor.start_offset,
    anchor.end_offset,
    anchor.text_hash,
  ]);
}

export type AutoSelectionIngestInput = {
  /** The current legitimate single-range candidate (null = no selection). */
  candidate: ReaderAskAttachment | null;
  /** Fingerprint currently occupying the auto slot. */
  currentFingerprint: string | null;
  /** Fingerprint the user ×-dismissed (awaiting a new selection). */
  dismissedFingerprint: string | null;
};

export type AutoSelectionIngestDecision =
  | { kind: "skip" }
  | {
      kind: "ingest";
      attachment: ReaderAskAttachment;
      fingerprint: string;
    };

/**
 * Decide whether the current selection should be written into the auto
 * slot. Skip when: no candidate, no anchor fingerprint, same fingerprint
 * as the current slot, or the fingerprint was ×-dismissed and no new
 * selection has happened since. A fresh fingerprint clears the dismissal.
 */
export function decideAutoSelectionIngest(
  input: AutoSelectionIngestInput,
): AutoSelectionIngestDecision {
  const { candidate, currentFingerprint, dismissedFingerprint } = input;
  if (!candidate) {
    return { kind: "skip" };
  }
  const fingerprint = askSelectionAnchorFingerprint(candidate);
  if (!fingerprint) {
    return { kind: "skip" };
  }
  if (fingerprint === currentFingerprint) {
    return { kind: "skip" };
  }
  if (fingerprint === dismissedFingerprint) {
    return { kind: "skip" };
  }
  return { kind: "ingest", attachment: candidate, fingerprint };
}

export type PinSelectionInput = {
  candidate: ReaderAskAttachment | null;
  autoSelection: ReaderAskAttachment | null;
  manualSelections: readonly ReaderAskAttachment[];
};

export type PinSelectionDecision =
  /** No candidate / no anchor — nothing to pin. */
  | { kind: "noop" }
  /** The candidate IS the auto slot: promote it to manual. */
  | { kind: "promote"; fingerprint: string }
  /** Append the candidate to the manual slots. */
  | { kind: "append"; fingerprint: string }
  /** Already pinned manually — idempotent open only. */
  | { kind: "already-manual"; fingerprint: string }
  /** Manual cap reached for a not-yet-pinned selection. */
  | { kind: "blocked-full"; fingerprint: string };

/**
 * Decide the "加入 Ask Claread" pin action:
 * - candidate matches the auto slot ⇒ promote (auto cleared, manual gains
 *   it — no duplicate chip);
 * - candidate already manual ⇒ idempotent (open panel only);
 * - otherwise append when under the cap, else blocked-full.
 */
export function decidePinSelection(
  input: PinSelectionInput,
): PinSelectionDecision {
  const { candidate, autoSelection, manualSelections } = input;
  const fingerprint = askSelectionAnchorFingerprint(candidate);
  if (!candidate || !fingerprint) {
    return { kind: "noop" };
  }
  const isCurrentAuto =
    askSelectionAnchorFingerprint(autoSelection) === fingerprint;
  if (isCurrentAuto) {
    if (manualSelections.length >= MAX_MANUAL_ASK_SELECTIONS) {
      return { kind: "blocked-full", fingerprint };
    }
    return { kind: "promote", fingerprint };
  }
  const alreadyManual = manualSelections.some(
    (attachment) => askSelectionAnchorFingerprint(attachment) === fingerprint,
  );
  if (alreadyManual) {
    return { kind: "already-manual", fingerprint };
  }
  if (manualSelections.length >= MAX_MANUAL_ASK_SELECTIONS) {
    return { kind: "blocked-full", fingerprint };
  }
  return { kind: "append", fingerprint };
}
