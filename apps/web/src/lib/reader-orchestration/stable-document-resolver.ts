import type {
  ReaderStableDocumentAnchorSegmentDto,
  ReaderStableDocumentBlockDto,
  ReaderStableDocumentResponseDto,
} from "@/types/api/reader-plate";

/**
 * Stable document resolver.
 *
 * The only allowed truth sources for anchor/citation text are:
 *   - `base.text` (the canonicalized body, UTF-16 indexed)
 *   - `blocks[].text_content` (UTF-16 indexed via canonical_text_start/end)
 *   - `anchor_segments[]` (UTF-16 ranges against `base.text`)
 *
 * This module NEVER reaches for:
 *   - Plate JSON
 *   - Slate path
 *   - DOM selection
 *   - Markdown syntax
 *   - `original_inputs.source_text`
 *
 * All functions are pure and return `null` on any invalid input rather than
 * guessing. UTF-16 offsets are interpreted as JS string code-unit indices
 * (`String.prototype.length`, `String.prototype.slice`). Offsets must be
 * finite, non-negative, integer — fractional values are rejected.
 */

export interface StableDocumentIndex {
  blocksById: ReadonlyMap<string, ReaderStableDocumentBlockDto>;
  anchorsById: ReadonlyMap<string, ReaderStableDocumentAnchorSegmentDto>;
}

export interface ResolveStableAnchorTextQuery {
  blockId?: string;
  anchorSegmentId?: string;
  startUtf16?: number;
  endUtf16?: number;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isValidUtf16Offset(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    Number.isInteger(value) &&
    value >= 0
  );
}

export function buildStableDocumentIndex(
  document: ReaderStableDocumentResponseDto,
): StableDocumentIndex {
  const blocksById = new Map<string, ReaderStableDocumentBlockDto>();
  if (Array.isArray(document.blocks)) {
    for (const block of document.blocks) {
      if (block && typeof block.block_id === "string" && block.block_id.length > 0) {
        blocksById.set(block.block_id, block);
      }
    }
  }

  const anchorsById = new Map<string, ReaderStableDocumentAnchorSegmentDto>();
  if (Array.isArray(document.anchor_segments)) {
    for (const segment of document.anchor_segments) {
      if (
        segment &&
        typeof segment.anchor_segment_id === "string" &&
        segment.anchor_segment_id.length > 0
      ) {
        anchorsById.set(segment.anchor_segment_id, segment);
      }
    }
  }

  return { blocksById, anchorsById };
}

export function getStableDocumentBaseText(
  document: ReaderStableDocumentResponseDto,
): string {
  return document.base.text;
}

export function findStableBlockById(
  index: StableDocumentIndex,
  blockId: string,
): ReaderStableDocumentBlockDto | null {
  if (!isNonEmptyString(blockId)) return null;
  return index.blocksById.get(blockId) ?? null;
}

export function findStableAnchorSegmentById(
  index: StableDocumentIndex,
  anchorSegmentId: string,
): ReaderStableDocumentAnchorSegmentDto | null {
  if (!isNonEmptyString(anchorSegmentId)) return null;
  return index.anchorsById.get(anchorSegmentId) ?? null;
}

/**
 * Slice a UTF-16 range. Returns `null` when:
 *   - either offset is non-finite, negative, fractional, or non-numeric
 *   - `startUtf16 > endUtf16`
 *   - `endUtf16` exceeds the source text's UTF-16 length
 */
export function sliceStableTextByUtf16(
  sourceText: string,
  startUtf16: number,
  endUtf16: number,
): string | null {
  if (!isValidUtf16Offset(startUtf16) || !isValidUtf16Offset(endUtf16)) {
    return null;
  }
  if (startUtf16 > endUtf16) return null;
  if (endUtf16 > sourceText.length) return null;
  return sourceText.slice(startUtf16, endUtf16);
}

/**
 * Compute the effective [start, end] range for an anchor segment lookup.
 *
 * Rules (per F4 review):
 *   - The segment must have a valid UTF-16 range against the base text;
 *     otherwise return `null`.
 *   - When the caller provides `startUtf16` / `endUtf16` overrides they must
 *     each be valid integers AND lie inside the segment range. If either
 *     condition fails, return `null` — we DO NOT silently clamp.
 *   - When overrides are omitted, the segment's own start/end are used.
 *   - After applying, if start > end, return `null`.
 */
function resolveAnchorRange(
  segment: ReaderStableDocumentAnchorSegmentDto,
  document: ReaderStableDocumentResponseDto,
  overrideStart?: number,
  overrideEnd?: number,
): { start: number; end: number } | null {
  if (!isValidUtf16Offset(segment.base_start_utf16)) return null;
  if (!isValidUtf16Offset(segment.base_end_utf16)) return null;
  if (segment.base_end_utf16 > document.base.text.length) return null;
  if (segment.base_start_utf16 > segment.base_end_utf16) return null;

  const start = overrideStart === undefined ? segment.base_start_utf16 : overrideStart;
  const end = overrideEnd === undefined ? segment.base_end_utf16 : overrideEnd;

  if (!isValidUtf16Offset(start) || !isValidUtf16Offset(end)) return null;
  if (start < segment.base_start_utf16) return null;
  if (end > segment.base_end_utf16) return null;
  if (start > end) return null;

  return { start, end };
}

function resolveBlockRange(
  block: ReaderStableDocumentBlockDto,
  document: ReaderStableDocumentResponseDto,
  overrideStart?: number,
  overrideEnd?: number,
): { start: number; end: number } | null {
  if (typeof block.text_content !== "string") return null;
  if (!isValidUtf16Offset(block.canonical_text_start_utf16)) return null;
  if (!isValidUtf16Offset(block.canonical_text_end_utf16)) return null;
  if (block.canonical_text_end_utf16 > document.base.text.length) return null;
  if (block.canonical_text_start_utf16 > block.canonical_text_end_utf16) return null;

  const start = overrideStart === undefined ? block.canonical_text_start_utf16 : overrideStart;
  const end = overrideEnd === undefined ? block.canonical_text_end_utf16 : overrideEnd;

  if (!isValidUtf16Offset(start) || !isValidUtf16Offset(end)) return null;
  if (start < block.canonical_text_start_utf16) return null;
  if (end > block.canonical_text_end_utf16) return null;
  if (start > end) return null;

  return { start, end };
}

/**
 * Resolve anchor text. Resolution order is:
 *   1. If `anchorSegmentId` is present:
 *      - segment exists: resolve via segment range (override optional).
 *      - segment does NOT exist: return `null` (no fallback to block).
 *   2. Else if `blockId` is given: resolve via block range (override optional).
 *
 * No `original_inputs` lookup, no Plate JSON, no DOM selection.
 */
export function resolveStableAnchorText(
  document: ReaderStableDocumentResponseDto,
  query: ResolveStableAnchorTextQuery,
): string | null {
  const index = buildStableDocumentIndex(document);

  if (query.anchorSegmentId !== undefined) {
    const segment = findStableAnchorSegmentById(index, query.anchorSegmentId);
    if (!segment) return null;
    const range = resolveAnchorRange(segment, document, query.startUtf16, query.endUtf16);
    if (!range) return null;
    return sliceStableTextByUtf16(document.base.text, range.start, range.end);
  }

  if (query.blockId !== undefined) {
    const block = findStableBlockById(index, query.blockId);
    if (!block) return null;
    const range = resolveBlockRange(block, document, query.startUtf16, query.endUtf16);
    if (!range) return null;
    return sliceStableTextByUtf16(document.base.text, range.start, range.end);
  }

  // No blockId / anchorSegmentId — we cannot invent text. Refuse.
  return null;
}
