import { computeUtf16FNV1a } from "@claread/contracts";

import type { ReaderRecordAnchorDraft, ReaderRecordAnchorScope } from "./reader-record-anchor-draft";
import type {
  ReaderRecordPlateAnchorSegmentNode,
  ReaderRecordPlateDocument,
  ReaderRecordPlateTextAnchor,
} from "./reader-record-plate-document";

export type ReaderRecordActiveAnchorSource =
  | "selection"
  | "system_mark"
  | "system_cue";

export interface UserEditorialAssetAnchorDraft {
  record_id: string;
  base_id: string;
  generation: number;
  unit_id: string;
  anchor_segment_id: string;
  scope: ReaderRecordAnchorScope;
  offset_unit: "utf16";
  start_offset: number;
  end_offset: number;
  selected_text: string;
  text_hash: string;
  hash_algorithm: "fnv1a32-utf16";
}

export type ReaderRecordActiveAnchorInput =
  | {
      source: "selection";
      anchor: ReaderRecordAnchorDraft;
    }
  | {
      source: "system_mark";
      anchor: ReaderRecordPlateTextAnchor;
    }
  | {
      source: "system_cue";
      anchor: ReaderRecordPlateTextAnchor;
    };

interface NormalizedActiveAnchor {
  unit_id: string;
  anchor_segment_id: string;
  scope: ReaderRecordAnchorScope;
  offset_unit: "utf16";
  start_offset: number;
  end_offset: number;
  selected_text: string;
  text_hash: string;
  hash_algorithm: "fnv1a32-utf16";
}

function hasValidRoot(document: ReaderRecordPlateDocument): boolean {
  return (
    document.record.recordId.length > 0 &&
    document.base.baseId.length > 0 &&
    Number.isInteger(document.record.generation) &&
    document.record.generation >= 1
  );
}

function normalizeSelectionAnchor(
  anchor: ReaderRecordAnchorDraft,
): NormalizedActiveAnchor {
  return {
    unit_id: anchor.unit_id,
    anchor_segment_id: anchor.anchor_segment_id,
    scope: anchor.scope,
    offset_unit: anchor.offset_unit,
    start_offset: anchor.start_offset,
    end_offset: anchor.end_offset,
    selected_text: anchor.selected_text,
    text_hash: anchor.text_hash,
    hash_algorithm: anchor.hash_algorithm,
  };
}

function normalizeTextAnchor(anchor: ReaderRecordPlateTextAnchor): NormalizedActiveAnchor {
  return {
    unit_id: anchor.unitId,
    anchor_segment_id: anchor.anchorSegmentId,
    scope: "system_ai_layer",
    offset_unit: anchor.offsetUnit,
    start_offset: anchor.unitStartOffset,
    end_offset: anchor.unitEndOffset,
    selected_text: anchor.selectedText,
    text_hash: anchor.textHash,
    hash_algorithm: anchor.hashAlgorithm,
  };
}

function findAnchorSegment(
  document: ReaderRecordPlateDocument,
  anchor: Pick<NormalizedActiveAnchor, "unit_id" | "anchor_segment_id">,
): ReaderRecordPlateAnchorSegmentNode | null {
  for (const unit of document.children) {
    if (unit.unitId !== anchor.unit_id) {
      continue;
    }

    for (const child of unit.children) {
      if (child.type !== "reader_record_source_block") {
        continue;
      }

      for (const sourceChild of child.children) {
        if (
          "type" in sourceChild &&
          sourceChild.type === "reader_record_anchor_segment" &&
          sourceChild.unitId === anchor.unit_id &&
          sourceChild.anchorSegmentId === anchor.anchor_segment_id
        ) {
          return sourceChild;
        }
      }
    }
  }

  return null;
}

function hasMatchingSourceRoot(
  document: ReaderRecordPlateDocument,
  active: ReaderRecordActiveAnchorInput,
): boolean {
  if (active.source === "selection") {
    return (
      active.anchor.record_id === document.record.recordId &&
      active.anchor.base_id === document.base.baseId &&
      active.anchor.generation === document.record.generation
    );
  }

  return active.anchor.baseId === document.base.baseId;
}

function isValidActiveAnchor(anchor: NormalizedActiveAnchor): boolean {
  if (
    anchor.unit_id.length === 0 ||
    anchor.anchor_segment_id.length === 0 ||
    !Number.isInteger(anchor.start_offset) ||
    !Number.isInteger(anchor.end_offset) ||
    anchor.offset_unit !== "utf16" ||
    anchor.hash_algorithm !== "fnv1a32-utf16" ||
    anchor.selected_text.length === 0 ||
    anchor.end_offset <= anchor.start_offset
  ) {
    return false;
  }

  if (anchor.selected_text.length !== anchor.end_offset - anchor.start_offset) {
    return false;
  }

  return computeUtf16FNV1a(anchor.selected_text) === anchor.text_hash;
}

function isInsideDocumentAnchorSegment(
  document: ReaderRecordPlateDocument,
  anchor: NormalizedActiveAnchor,
): boolean {
  const segment = findAnchorSegment(document, anchor);
  if (!segment || segment.baseId !== document.base.baseId) {
    return false;
  }

  return (
    anchor.start_offset >= segment.unitRange.startUtf16 &&
    anchor.end_offset <= segment.unitRange.endUtf16
  );
}

function normalizeActiveAnchor(
  active: ReaderRecordActiveAnchorInput,
): NormalizedActiveAnchor {
  if (active.source === "selection") {
    return normalizeSelectionAnchor(active.anchor);
  }
  return normalizeTextAnchor(active.anchor);
}

export function userEditorialAssetAnchorDraftForActiveAnchor(
  document: ReaderRecordPlateDocument,
  active: ReaderRecordActiveAnchorInput,
): UserEditorialAssetAnchorDraft | null {
  if (!hasValidRoot(document)) {
    return null;
  }

  if (!hasMatchingSourceRoot(document, active)) {
    return null;
  }

  const anchor = normalizeActiveAnchor(active);
  if (!isValidActiveAnchor(anchor)) {
    return null;
  }

  if (!isInsideDocumentAnchorSegment(document, anchor)) {
    return null;
  }

  return {
    record_id: document.record.recordId,
    base_id: document.base.baseId,
    generation: document.record.generation,
    ...anchor,
  };
}
