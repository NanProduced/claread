import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";
import type {
  ReaderRecordPlateDocument,
  ReaderRecordPlateParagraphBlock,
  ReaderRecordPlateTextLeaf,
} from "@/lib/reader-plate/projection/reader-record-plate-document";

/** L0 = full unit 段落导航；L1 = heading-only 扁平章节导航。 */
export type ReaderRecordNavigationMode = "L0" | "L1";

export interface ReaderRecordNavigationItem {
  unitId: string;
  orderIndex: number;
  label: string;
  fallbackIndex: number;
}

/**
 * L1 section row. Identity is the heading unit; coverage is a closed reading-order
 * interval [startUnitId … endUnitId] including body/list/quote between headings.
 * No depth/tree fields — L1 is intentionally flat.
 */
export interface ReaderRecordL1NavigationItem extends ReaderRecordNavigationItem {
  startUnitId: string;
  endUnitId: string;
  coveredUnitIds: string[];
}

export interface ReaderRecordNavigationProjection {
  mode: ReaderRecordNavigationMode;
  items: ReaderRecordNavigationItem[];
  /** Present only when mode === "L1"; same row order as `items`. */
  l1Items: ReaderRecordL1NavigationItem[] | null;
  sourceIdentityKey: string;
}

/** Product gate: both conditions required; not OR. */
export const L1_MIN_UNIT_COUNT = 6;
export const L1_MIN_HEADING_COUNT = 2;

const LABEL_MAX_LENGTH = 48;

function isParagraphBlock(
  block: ReaderRecordPlateDocument["children"][number],
): block is ReaderRecordPlateParagraphBlock {
  return block.type === "paragraph";
}

function extractParagraphText(block: ReaderRecordPlateParagraphBlock): string {
  return block.children
    .map((leaf: ReaderRecordPlateTextLeaf) => leaf.text)
    .join("")
    .replace(/\s+/g, " ")
    .trim();
}

function truncateLabel(text: string, maxLength = LABEL_MAX_LENGTH): string {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength).trim()}…`;
}

/** Display-only: strip leading markdown heading markers; does not change unit identity. */
export function stripHeadingDisplayMarkers(text: string): string {
  return text.replace(/^#{1,6}\s+/, "").trim();
}

function buildParagraphsByUnitId(
  plateDocument: ReaderRecordPlateDocument,
): Map<string, ReaderRecordPlateParagraphBlock[]> {
  const paragraphsByUnitId = new Map<string, ReaderRecordPlateParagraphBlock[]>();
  for (const block of plateDocument.children) {
    if (!isParagraphBlock(block)) {
      continue;
    }
    const list = paragraphsByUnitId.get(block.data.unitId) ?? [];
    list.push(block);
    paragraphsByUnitId.set(block.data.unitId, list);
  }
  return paragraphsByUnitId;
}

function resolveUnitLabel(
  unit: { unit_id: string; label?: string | null },
  paragraphsByUnitId: Map<string, ReaderRecordPlateParagraphBlock[]>,
  fallbackIndex: number,
  emptyFallbackKind: "段" | "项",
  options?: { stripHeadingMarkers?: boolean },
): string {
  const explicitLabel = unit.label?.trim();
  if (explicitLabel) {
    const display = options?.stripHeadingMarkers
      ? stripHeadingDisplayMarkers(explicitLabel)
      : explicitLabel;
    return display || explicitLabel;
  }

  const unitParagraphs = paragraphsByUnitId.get(unit.unit_id) ?? [];
  const firstParagraph = unitParagraphs[0];
  const derivedText = firstParagraph ? extractParagraphText(firstParagraph) : "";

  if (derivedText) {
    const display = options?.stripHeadingMarkers
      ? stripHeadingDisplayMarkers(derivedText)
      : derivedText;
    return truncateLabel(display || derivedText);
  }

  return `第 ${fallbackIndex + 1} ${emptyFallbackKind}`;
}

/**
 * Stable source identity for navigation rail state.
 * Changes when base or generation changes — even if unit ids still look like u1/u2.
 */
export function buildReaderRecordSourceIdentityKey(
  snapshot: ReaderPlateSnapshotDto,
): string {
  return `${snapshot.base.base_id}:${snapshot.record.generation}`;
}

/**
 * Strict L1 enable gate. Document-fallback path (empty navigation.units) is never L1.
 */
export function isL1NavigationEnabled(
  snapshot: ReaderPlateSnapshotDto,
): boolean {
  const units = snapshot.navigation.units;
  if (units.length === 0) {
    return false;
  }
  const headingCount = units.filter((unit) => unit.unit_type === "heading").length;
  return (
    units.length >= L1_MIN_UNIT_COUNT && headingCount >= L1_MIN_HEADING_COUNT
  );
}

export function buildReaderRecordNavigationItems(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): ReaderRecordNavigationItem[] {
  const paragraphsByUnitId = buildParagraphsByUnitId(plateDocument);

  const units = [...snapshot.navigation.units].sort(
    (a, b) => a.order_index - b.order_index,
  );
  // Older and partially generated snapshots can have a complete Plate document
  // before navigation.units is populated. Keep the outline usable by deriving
  // stable unit entries from the document in that one degraded state.
  const navigationUnits =
    units.length > 0
      ? units
      : [...paragraphsByUnitId.keys()].map((unitId, orderIndex) => ({
          unit_id: unitId,
          order_index: orderIndex,
          label: null as string | null,
        }));
  return navigationUnits.map((unit, fallbackIndex) => ({
    unitId: unit.unit_id,
    orderIndex: unit.order_index,
    label: resolveUnitLabel(unit, paragraphsByUnitId, fallbackIndex, "段"),
    fallbackIndex,
  }));
}

/**
 * Build L1 heading-only flat navigation items with closed coverage intervals.
 * Caller must ensure enable gate; this function only filters heading units.
 * Does not invent hierarchy/depth.
 */
export function buildReaderRecordL1NavigationItems(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): ReaderRecordL1NavigationItem[] {
  const paragraphsByUnitId = buildParagraphsByUnitId(plateDocument);
  const units = [...snapshot.navigation.units].sort(
    (a, b) => a.order_index - b.order_index,
  );

  if (units.length === 0) {
    return [];
  }

  const headingIndices: number[] = [];
  for (let i = 0; i < units.length; i++) {
    if (units[i].unit_type === "heading") {
      headingIndices.push(i);
    }
  }

  return headingIndices.map((headingIndex, fallbackIndex) => {
    const heading = units[headingIndex];
    const nextHeadingIndex = headingIndices[fallbackIndex + 1];
    const endIndex =
      nextHeadingIndex === undefined ? units.length - 1 : nextHeadingIndex - 1;
    const covered = units.slice(headingIndex, endIndex + 1);
    const coveredUnitIds = covered.map((unit) => unit.unit_id);
    const endUnit = units[endIndex];

    return {
      unitId: heading.unit_id,
      orderIndex: heading.order_index,
      label: resolveUnitLabel(
        heading,
        paragraphsByUnitId,
        fallbackIndex,
        "项",
        { stripHeadingMarkers: true },
      ),
      fallbackIndex,
      startUnitId: heading.unit_id,
      endUnitId: endUnit.unit_id,
      coveredUnitIds,
    };
  });
}

/**
 * Project L0 or L1 navigation for the rail.
 * L1 only when snapshot navigation.units satisfy the strict gate; otherwise L0.
 */
export function projectReaderRecordNavigation(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): ReaderRecordNavigationProjection {
  const sourceIdentityKey = buildReaderRecordSourceIdentityKey(snapshot);

  if (isL1NavigationEnabled(snapshot)) {
    const l1Items = buildReaderRecordL1NavigationItems(snapshot, plateDocument);
    // Gate already requires heading_count >= 2; l1Items should be non-empty.
    // If somehow empty, fall through to L0 rather than rendering empty chapter list.
    if (l1Items.length > 0) {
      return {
        mode: "L1",
        items: l1Items,
        l1Items,
        sourceIdentityKey,
      };
    }
  }

  return {
    mode: "L0",
    items: buildReaderRecordNavigationItems(snapshot, plateDocument),
    l1Items: null,
    sourceIdentityKey,
  };
}
