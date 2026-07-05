import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";
import type {
  ReaderRecordPlateDocument,
  ReaderRecordPlateParagraphBlock,
  ReaderRecordPlateTextLeaf,
} from "@/lib/reader-plate/projection/reader-record-plate-document";

export interface ReaderRecordNavigationItem {
  unitId: string;
  orderIndex: number;
  label: string;
  fallbackIndex: number;
}

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

export function buildReaderRecordNavigationItems(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): ReaderRecordNavigationItem[] {
  const units = [...snapshot.navigation.units].sort(
    (a, b) => a.order_index - b.order_index,
  );

  const paragraphsByUnitId = new Map<string, ReaderRecordPlateParagraphBlock[]>();
  for (const block of plateDocument.children) {
    if (!isParagraphBlock(block)) {
      continue;
    }
    const list = paragraphsByUnitId.get(block.data.unitId) ?? [];
    list.push(block);
    paragraphsByUnitId.set(block.data.unitId, list);
  }

  return units.map((unit, fallbackIndex) => {
    const explicitLabel = unit.label?.trim();
    if (explicitLabel) {
      return {
        unitId: unit.unit_id,
        orderIndex: unit.order_index,
        label: explicitLabel,
        fallbackIndex,
      };
    }

    const unitParagraphs = paragraphsByUnitId.get(unit.unit_id) ?? [];
    const firstParagraph = unitParagraphs[0];
    const derivedText = firstParagraph
      ? extractParagraphText(firstParagraph)
      : "";

    if (derivedText) {
      return {
        unitId: unit.unit_id,
        orderIndex: unit.order_index,
        label: truncateLabel(derivedText),
        fallbackIndex,
      };
    }

    return {
      unitId: unit.unit_id,
      orderIndex: unit.order_index,
      label: `第 ${fallbackIndex + 1} 段`,
      fallbackIndex,
    };
  });
}
