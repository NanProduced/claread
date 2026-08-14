/**
 * Adapt stable-document blocks (wide `block_type: string`) to the
 * Structured Source contract (narrow `block_type` union).
 *
 * This is a pure, fail-safe projection from the frozen BFF DTO
 * {@link ReaderStableDocumentBlockDto} to the G0 renderer-input
 * {@link ReaderStructuredSourceBlock}. It:
 *   - Does NOT re-parse raw Markdown.
 *   - Does NOT touch the network / DOM / Ask panel / SSE / transport.
 *   - Does NOT mutate the input array or its block objects.
 *
 * Mapping rules:
 *   - `block_type: string` → narrow union. Unknown types fall back to
 *     `"paragraph"`; the original type and a diagnostic warning are preserved
 *     inside `payload_json` (`original_block_type`, `adaptation_warning`) so
 *     the renderer can surface the fallback without violating the G0 contract.
 *   - `payload` → `payload_json` (shallow copy). Links / stripped_links /
 *     level / ordered / language / alignments etc. are carried verbatim; the
 *     renderer applies its own defensive whitelist on links.
 *   - `source_refs.line_start` / `source_refs.line_end` → `source_range`.
 *     Missing / non-finite values fall back to `0` (fail-closed; the renderer
 *     treats `line_start === line_end === 0` as "no range").
 *   - `canonical_text_start_utf16` / `canonical_text_end_utf16` →
 *     `source_range.utf16_start` / `source_range.utf16_end` (optional fields).
 *   - `quality.warnings` → `payload_json.quality_warnings` (diagnostic only;
 *     the G0 per-block contract has no warnings field, so they are preserved
 *     as a non-standard payload key rather than dropped).
 *   - `block_id`, `parent_block_id`, `order_index`, `text_content` are
 *     preserved as-is. `text_content: null` is preserved (not coerced).
 *
 apps/web/docs/reader-ia.md
 */

import type {
  ReaderStableDocumentBlockDto,
  ReaderStructuredSourceBlock,
  ReaderStructuredSourceBlockType,
  ReaderStructuredSourceRange,
} from "@/types/api/reader-plate";

const KNOWN_BLOCK_TYPES: ReadonlySet<ReaderStructuredSourceBlockType> = new Set([
  "heading",
  "paragraph",
  "blockquote",
  "thematic_break",
  "list",
  "list_item",
  "code_block",
  "table",
  "table_row",
  "table_cell",
  "footnote",
]);

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asFiniteNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

/**
 * Narrow a wide `block_type: string` to the G0 union. Unknown values fall
 * back to `"paragraph"`. Returns the narrowed type plus a diagnostic warning
 * string when a fallback was applied.
 */
function narrowBlockType(
  raw: string,
): { type: ReaderStructuredSourceBlockType; warning: string | null } {
  if (typeof raw === "string" && KNOWN_BLOCK_TYPES.has(raw as ReaderStructuredSourceBlockType)) {
    return { type: raw as ReaderStructuredSourceBlockType, warning: null };
  }
  return {
    type: "paragraph",
    warning: `Unknown block_type "${String(raw)}"; fell back to "paragraph".`,
  };
}

/**
 * Extract `line_start` / `line_end` from the opaque `source_refs` record.
 * Falls back to `0` for missing / non-finite values (fail-closed).
 */
function extractSourceRange(
  sourceRefs: Record<string, unknown>,
  canonicalStartUtf16: number | null,
  canonicalEndUtf16: number | null,
): ReaderStructuredSourceRange {
  const lineStart = asFiniteNumber(sourceRefs.line_start);
  const lineEnd = asFiniteNumber(sourceRefs.line_end);

  const range: ReaderStructuredSourceRange = {
    line_start: lineStart ?? 0,
    line_end: lineEnd ?? 0,
  };

  if (canonicalStartUtf16 != null) {
    range.utf16_start = canonicalStartUtf16;
  }
  if (canonicalEndUtf16 != null) {
    range.utf16_end = canonicalEndUtf16;
  }

  return range;
}

/**
 * Shallow-copy `payload` into a fresh `payload_json` record, then overlay
 * adaptation diagnostics (original block type / fallback warning) and quality
 * warnings extracted from the opaque `quality` record.
 *
 * The shallow copy preserves links / stripped_links / level / ordered /
 * language / alignments etc. without the adapter needing to understand every
 * payload shape. The renderer applies its own defensive validation.
 */
function buildPayloadJson(
  payload: Record<string, unknown>,
  quality: Record<string, unknown>,
  originalBlockType: string,
  warning: string | null,
): Record<string, unknown> {
  const payloadJson: Record<string, unknown> = { ...payload };

  if (warning !== null) {
    payloadJson.original_block_type = originalBlockType;
    payloadJson.adaptation_warning = warning;
  }

  const qualityWarnings = quality.warnings;
  if (Array.isArray(qualityWarnings)) {
    payloadJson.quality_warnings = qualityWarnings;
  }

  return payloadJson;
}

/**
 * Adapt an array of stable-document blocks (wide `block_type: string`) to
 * the G0 Structured Source contract (narrow `block_type` union).
 *
 * Returns a new array; the input is not mutated. An empty input returns an
 * empty array (caller falls back to existing rendering).
 *
 * @param blocks - Stable-document blocks from the BFF
 *   `ReaderStableDocumentResponseDto.blocks` field. May be empty.
 * @returns Structured Source blocks ready for `StructuredSourceRenderer`.
 */
export function adaptStableBlocksToStructuredSource(
  blocks: ReaderStableDocumentBlockDto[],
): ReaderStructuredSourceBlock[] {
  if (!Array.isArray(blocks) || blocks.length === 0) {
    return [];
  }

  const out: ReaderStructuredSourceBlock[] = [];

  for (const block of blocks) {
    if (!isObject(block)) {
      continue;
    }

    const rawBlockType =
      typeof block.block_type === "string" ? block.block_type : "";
    const { type, warning } = narrowBlockType(rawBlockType);

    const payload =
      isObject(block.payload) ? block.payload : {};
    const quality =
      isObject(block.quality) ? block.quality : {};
    const sourceRefs =
      isObject(block.source_refs) ? block.source_refs : {};

    const payloadJson = buildPayloadJson(
      payload,
      quality,
      rawBlockType,
      warning,
    );

    const sourceRange = extractSourceRange(
      sourceRefs,
      block.canonical_text_start_utf16 ?? null,
      block.canonical_text_end_utf16 ?? null,
    );

    out.push({
      block_id: block.block_id,
      parent_block_id: block.parent_block_id ?? null,
      order_index: block.order_index,
      block_type: type,
      text_content: block.text_content ?? null,
      payload_json: payloadJson,
      source_range: sourceRange,
    });
  }

  return out;
}
