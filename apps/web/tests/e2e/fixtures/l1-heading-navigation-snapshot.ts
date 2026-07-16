/**
 * T5.1d — Browser fixture builders for L1 deterministic heading navigation.
 *
 * Pure snapshot DTO factories used by Chromium E2E only.
 * Does not touch production projection / rail / Surface code.
 */

const HASH_ALG = "fnv1a32-utf16" as const;
const SCHEMA_KIND = "reader_plate_snapshot" as const;

export type L1NavUnitType =
  | "body"
  | "heading"
  | "list"
  | "quote"
  | "unknown"
  | "fallback";

export interface L1NavUnitSpec {
  unit_id: string;
  order_index: number;
  unit_type: L1NavUnitType;
  label: string | null;
  /** Source paragraph text projected into the plate document. */
  text: string;
}

export interface L1NavSnapshotOptions {
  units: L1NavUnitSpec[];
  baseId?: string;
  generation?: number;
  snapshotId?: string;
  recordId?: string;
}

function makeUnitNode(
  unit: L1NavUnitSpec,
  baseId: string,
  baseStart: number,
): Record<string, unknown> {
  const baseEnd = baseStart + unit.text.length;
  const segId = `seg_${unit.unit_id}`;
  return {
    type: "reader_unit",
    owner: "stable",
    base_id: baseId,
    unit_id: unit.unit_id,
    order_index: unit.order_index,
    unit_type: unit.unit_type,
    boundary_quality: "normal",
    base_start_utf16: baseStart,
    base_end_utf16: baseEnd,
    text_hash: `hash_${unit.unit_id}`,
    hash_algorithm: HASH_ALG,
    children: [
      {
        type: "reader_source_block",
        owner: "stable",
        base_id: baseId,
        unit_id: unit.unit_id,
        base_start_utf16: baseStart,
        base_end_utf16: baseEnd,
        children: [
          {
            type: "reader_anchor_segment",
            owner: "stable",
            base_id: baseId,
            unit_id: unit.unit_id,
            anchor_segment_id: segId,
            sentence_id: `sent_${unit.unit_id}`,
            segment_type: "sentence",
            boundary_quality: "normal",
            base_start_utf16: baseStart,
            base_end_utf16: baseEnd,
            unit_start_utf16: 0,
            unit_end_utf16: unit.text.length,
            text_hash: `seg_hash_${unit.unit_id}`,
            hash_algorithm: HASH_ALG,
            children: [
              {
                text: unit.text,
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: baseStart,
                base_end_utf16: baseEnd,
                anchor_segment_id: segId,
                segment_start_utf16: baseStart,
                segment_end_utf16: baseEnd,
                reader_vocabulary_marks: [],
                reader_grammar_note_marks: [],
              },
            ],
          },
        ],
      },
    ],
  };
}

function makeAnchorSegment(
  unit: L1NavUnitSpec,
  baseId: string,
  baseStart: number,
  orderIndex: number,
): Record<string, unknown> {
  const baseEnd = baseStart + unit.text.length;
  return {
    anchor_segment_id: `seg_${unit.unit_id}`,
    sentence_id: `sent_${unit.unit_id}`,
    paragraph_id: unit.unit_id,
    unit_id: unit.unit_id,
    order_index: orderIndex,
    unit_order_index: unit.order_index,
    segment_type: "sentence",
    boundary_quality: "normal",
    base_start_utf16: baseStart,
    base_end_utf16: baseEnd,
    unit_start_utf16: 0,
    unit_end_utf16: unit.text.length,
    text_hash: `seg_hash_${unit.unit_id}`,
    hash_algorithm: HASH_ALG,
  };
}

/** Build a multi-unit ReaderPlateSnapshotDto-shaped object for navigation E2E. */
export function makeNavigationFixtureSnapshot(
  options: L1NavSnapshotOptions,
): Record<string, unknown> {
  const baseId = options.baseId ?? "base_1";
  const generation = options.generation ?? 1;
  const units = options.units;

  let cursor = 0;
  const value: Record<string, unknown>[] = [];
  const anchorSegments: Record<string, unknown>[] = [];
  const navigationUnits: Record<string, unknown>[] = [];

  for (let i = 0; i < units.length; i++) {
    const unit = units[i]!;
    // Separate units with a single space in the virtual base string.
    if (i > 0) cursor += 1;
    const baseStart = cursor;
    value.push(makeUnitNode(unit, baseId, baseStart));
    anchorSegments.push(makeAnchorSegment(unit, baseId, baseStart, i + 1));
    navigationUnits.push({
      unit_id: unit.unit_id,
      order_index: unit.order_index,
      unit_type: unit.unit_type,
      boundary_quality: "normal",
      label: unit.label,
      base_start_utf16: baseStart,
      base_end_utf16: baseStart + unit.text.length,
      text_hash: `hash_${unit.unit_id}`,
      hash_algorithm: HASH_ALG,
    });
    cursor += unit.text.length;
  }

  return {
    schema_kind: SCHEMA_KIND,
    snapshot_id: options.snapshotId ?? "snap_l1_nav_1",
    snapshot_taken_at: "2026-07-16T00:00:00Z",
    last_event_sequence: 1,
    record_id: options.recordId ?? "record_l1_nav",
    record: {
      title: "L1 Heading Navigation Fixture",
      display_title_zh: null,
      title_generation_status: "pending",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intensive_reading",
      created_at: "2026-07-16T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      generation,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: baseId,
      content_sha256: "b".repeat(64),
      canonicalizer_version: "test",
      builder_version: "test",
      segmenter_version: "test",
      text_length_utf16: cursor,
      hash_algorithm: HASH_ALG,
    },
    navigation: { units: navigationUnits },
    anchor_segments: anchorSegments,
    enhancement_layers: [],
    enhancement_progress: {
      overall_status: "readable_enhancing",
      layers: [],
    },
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value,
  };
}

/** Tall filler so natural layout keeps at most one heading above safeTop. */
function tallBody(seed: string): string {
  // ~40 lines → enough vertical room without test-only CSS.
  return Array.from({ length: 40 }, (_, i) => `${seed} line ${i + 1}.`).join(
    " ",
  );
}

/** ≥6 units, ≥2 headings, with lead body before first heading. */
export function headingRichUnitSpecs(): L1NavUnitSpec[] {
  return [
    {
      unit_id: "u1",
      order_index: 1,
      unit_type: "body",
      label: null,
      text: tallBody("Lead prologue body before any chapter heading begins"),
    },
    {
      unit_id: "u2",
      order_index: 2,
      unit_type: "heading",
      label: "Chapter One",
      text: "Chapter One",
    },
    {
      unit_id: "u3",
      order_index: 3,
      unit_type: "body",
      label: null,
      text: tallBody("Body under chapter one first paragraph for coverage spy"),
    },
    {
      unit_id: "u4",
      order_index: 4,
      unit_type: "body",
      label: null,
      text: tallBody("Body under chapter one second paragraph continues reading"),
    },
    {
      unit_id: "u5",
      order_index: 5,
      unit_type: "heading",
      label: "Chapter Two",
      text: "Chapter Two",
    },
    {
      unit_id: "u6",
      order_index: 6,
      unit_type: "body",
      label: null,
      text: tallBody("Body under chapter two first paragraph after second heading"),
    },
    {
      unit_id: "u7",
      order_index: 7,
      unit_type: "body",
      label: null,
      text: tallBody("Body under chapter two closing paragraph for scroll room"),
    },
  ];
}

/** unit_count ≥ 6 with exactly one heading → must stay L0. */
export function longSingleHeadingUnitSpecs(): L1NavUnitSpec[] {
  return [
    {
      unit_id: "u1",
      order_index: 1,
      unit_type: "body",
      label: null,
      text: tallBody("Opening body paragraph one of a long pure-ish article"),
    },
    {
      unit_id: "u2",
      order_index: 2,
      unit_type: "heading",
      label: "Only Heading",
      text: "Only Heading",
    },
    {
      unit_id: "u3",
      order_index: 3,
      unit_type: "body",
      label: null,
      text: tallBody("Body paragraph three after the single false heading"),
    },
    {
      unit_id: "u4",
      order_index: 4,
      unit_type: "body",
      label: null,
      text: tallBody("Body paragraph four keeps full L0 list visible"),
    },
    {
      unit_id: "u5",
      order_index: 5,
      unit_type: "body",
      label: null,
      text: tallBody("Body paragraph five for the long single-heading gate"),
    },
    {
      unit_id: "u6",
      order_index: 6,
      unit_type: "body",
      label: null,
      text: tallBody("Body paragraph six completes unit_count of six"),
    },
  ];
}

export function makeL1HeadingRichSnapshot(
  overrides: Partial<L1NavSnapshotOptions> = {},
): Record<string, unknown> {
  return makeNavigationFixtureSnapshot({
    units: headingRichUnitSpecs(),
    snapshotId: "snap_l1_heading_rich",
    ...overrides,
  });
}

export function makeL0SingleHeadingLongSnapshot(
  overrides: Partial<L1NavSnapshotOptions> = {},
): Record<string, unknown> {
  return makeNavigationFixtureSnapshot({
    units: longSingleHeadingUnitSpecs(),
    snapshotId: "snap_l0_single_heading",
    ...overrides,
  });
}
