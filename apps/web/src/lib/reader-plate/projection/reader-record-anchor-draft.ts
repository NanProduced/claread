import { computeUtf16FNV1a } from "@claread/contracts";

import type {
  ReaderPlateSnapshotDto,
  ReaderSnapshotAnchorSegmentDto,
} from "@/types/api/reader-plate";

/**
 * D6-A1 read-only anchor draft projection.
 *
 * The new `/app/reader-record/{recordId}` surface must be able to convert a
 * a read-only source selection into the new Reading Record anchor shape
 *   (record_id, base_id, generation, unit_id, anchor_segment_id,
 *    unit-local UTF-16 start/end, selected_text, text_hash, scope)
 * without touching any write API.
 *
 * This module is a pure helper. It returns either a well-formed draft or
 * `null`. It does not throw and it does not call any persistence endpoint.
 * The UI write surfaces (Ask / note / highlight / feedback) remain disabled
 * at the SelectionToolbar layer; this helper exists so that once D6-A5/A6
 * enables those writes, the anchor payload is already in the new shape.
 */

export type ReaderRecordAnchorScope =
  | "stable_source"
  | "translation"
  | "system_ai_layer"
  | "ask_supplement";

export interface ReaderRecordAnchorDraft {
  record_id: string;
  base_id: string;
  generation: number;
  unit_id: string;
  anchor_segment_id: string;
  /**
   * Unit-local UTF-16 offsets. CRITICAL: the source `ReaderSelectionSegment`
   * carries offsets relative to the anchor segment text, not the unit text.
   * The projection must add the anchor segment's `unit_start_utf16` baseline
   * so that two different anchor segments in the same unit produce two
   * distinct unit-local offsets.
   */
  start_offset: number;
  end_offset: number;
  offset_unit: "utf16";
  selected_text: string;
  text_hash: string;
  hash_algorithm: "fnv1a32-utf16";
  scope: ReaderRecordAnchorScope;
}

export interface ReaderRecordAnchorDraftSelectionSegment {
  paragraphId: string;
  sentenceId: string;
  selectedText: string;
  startOffset: number;
  endOffset: number;
  textHash: string;
}

interface AnchorSegmentIndex {
  bySentenceId: Map<string, ReaderSnapshotAnchorSegmentDto>;
  byAnchorSegmentId: Map<string, ReaderSnapshotAnchorSegmentDto>;
  byUnitAndSentence: Map<string, ReaderSnapshotAnchorSegmentDto>;
}

function buildAnchorSegmentIndex(
  anchorSegments: ReaderSnapshotAnchorSegmentDto[],
): AnchorSegmentIndex {
  const bySentenceId = new Map<string, ReaderSnapshotAnchorSegmentDto>();
  const byAnchorSegmentId = new Map<string, ReaderSnapshotAnchorSegmentDto>();
  const byUnitAndSentence = new Map<string, ReaderSnapshotAnchorSegmentDto>();

  for (const segment of anchorSegments) {
    byAnchorSegmentId.set(segment.anchor_segment_id, segment);
    if (segment.sentence_id) {
      bySentenceId.set(segment.sentence_id, segment);
    }
    byUnitAndSentence.set(`${segment.unit_id}::${segment.sentence_id}`, segment);
  }

  return { bySentenceId, byAnchorSegmentId, byUnitAndSentence };
}

function utf16Length(value: string): number {
  // A JavaScript string stores UTF-16 code units. A surrogate pair (U+D800..U+DBFF
  // followed by U+DC00..U+DFFF) represents one Unicode code point but counts as
  // 2 UTF-16 code units, so we must skip the low surrogate when we see a high
  // surrogate. Counting every code unit separately would yield 3 for a single
  // emoji (high + low + a phantom extra unit), which would diverge from the
  // Python `len(text.encode('utf-16-le')) // 2` and from `computeUtf16FNV1a`.
  let units = 0;
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      units += 2;
      // Skip the matching low surrogate if present.
      if (i + 1 < value.length) {
        const next = value.charCodeAt(i + 1);
        if (next >= 0xdc00 && next <= 0xdfff) {
          i += 1;
        }
      }
      continue;
    }
    units += 1;
  }
  return units;
}

function resolveAnchorSegment(
  index: AnchorSegmentIndex,
  segment: ReaderRecordAnchorDraftSelectionSegment,
): ReaderSnapshotAnchorSegmentDto | null {
  // Prefer (unit_id, sentence_id) — the projection sets
  // `paragraphId = unit_id`, so segment.paragraphId carries the unit id.
  const unitKey = `${segment.paragraphId}::${segment.sentenceId}`;
  const unitHit = index.byUnitAndSentence.get(unitKey);
  if (unitHit) {
    return unitHit;
  }

  const sentenceHit = index.bySentenceId.get(segment.sentenceId);
  if (sentenceHit) {
    return sentenceHit;
  }

  const anchorHit = index.byAnchorSegmentId.get(segment.sentenceId);
  return anchorHit ?? null;
}

function buildDraft(
  snapshot: ReaderPlateSnapshotDto,
  segment: ReaderRecordAnchorDraftSelectionSegment,
  anchor: ReaderSnapshotAnchorSegmentDto,
): ReaderRecordAnchorDraft | null {
  if (
    !Number.isInteger(segment.startOffset) ||
    !Number.isInteger(segment.endOffset)
  ) {
    return null;
  }

  // Anchor segment's unit_start_utf16 is the absolute offset of the segment
  // inside the parent Reading Unit. The selection's start/end offsets are
  // relative to the segment text. Convert to unit-local UTF-16.
  const start_offset = anchor.unit_start_utf16 + segment.startOffset;
  const end_offset = anchor.unit_start_utf16 + segment.endOffset;

  if (end_offset <= start_offset) {
    return null;
  }

  if (
    start_offset < anchor.unit_start_utf16 ||
    end_offset > anchor.unit_end_utf16
  ) {
    return null;
  }

  const selected_text = segment.selectedText ?? "";

  // Re-derive hash from the actual selected_text instead of trusting the
  // segment's stored hash: the draft is a UI-level projection, and we want
  // a fresh fnv1a32-utf16 hash that matches the precise slice the user
  // currently sees. The hash on the segment is a sentence-text hash which
  // may differ for sub-sentence selections.
  const computed_hash = computeUtf16FNV1a(selected_text);
  if (computed_hash !== segment.textHash) {
    // Mismatch means the selection carries stale or inconsistent hash info.
    // Returning null is safer than emitting an inconsistent draft.
    return null;
  }

  if (utf16Length(selected_text) !== end_offset - start_offset) {
    return null;
  }

  return {
    record_id: snapshot.record_id,
    base_id: snapshot.base.base_id,
    generation: snapshot.record.generation,
    unit_id: anchor.unit_id,
    anchor_segment_id: anchor.anchor_segment_id,
    start_offset,
    end_offset,
    offset_unit: "utf16",
    selected_text,
    text_hash: segment.textHash,
    hash_algorithm: "fnv1a32-utf16",
    scope: "stable_source",
  };
}

export function anchorDraftForSelectionSegment(
  snapshot: ReaderPlateSnapshotDto,
  segment: ReaderRecordAnchorDraftSelectionSegment,
): ReaderRecordAnchorDraft | null {
  const index = buildAnchorSegmentIndex(snapshot.anchor_segments);
  const anchor = resolveAnchorSegment(index, segment);
  if (!anchor) {
    return null;
  }
  return buildDraft(snapshot, segment, anchor);
}

export function anchorDraftsForSelection(
  snapshot: ReaderPlateSnapshotDto,
  selection: { segments: ReaderRecordAnchorDraftSelectionSegment[] },
): ReaderRecordAnchorDraft[] {
  const drafts: ReaderRecordAnchorDraft[] = [];
  for (const segment of selection.segments) {
    const draft = anchorDraftForSelectionSegment(snapshot, segment);
    if (draft) {
      drafts.push(draft);
    }
  }
  return drafts;
}
