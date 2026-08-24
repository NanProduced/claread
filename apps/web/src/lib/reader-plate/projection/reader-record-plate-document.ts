import type {
  AnchorSegmentType,
  ReaderBoundaryQuality,
  ReaderEnhancementCapability,
  ReaderEnhancementProgressDto,
  ReaderEnhancementProgressLayerDto,
  ReaderEnhancementProgressLayerStatus,
  ReaderEnhancementProgressOverallStatus,
  ReaderGrammarNoteMarkDto,
  ReaderLayerTargetScope,
  ReaderLayerType,
  ReaderPlateSnapshotDto,
  ReaderSentenceAnalysisChunkDto,
  ReaderSentenceAnalysisNodeDto,
  ReaderSnapshotAskSupplementDto,
  ReaderSourceBlockChildNodeDto,
  ReaderSourceBlockInlineMarkDto,
  ReaderSourceBlockNodeDto,
  ReaderStableSeparatorLeafDto,
  ReaderStableSegmentTextLeafDto,
  ReaderStableDocumentBlockNodeDto,
  ReaderSnapshotUserAssetDto,
  ReaderTranslationGroupNodeDto,
  ReaderUnitNodeDto,
  ReaderVocabularyMarkDto,
  VocabularyItemType,
  VocabularyPhraseType,
} from "@/types/api/reader-plate";
import { computeUtf16FNV1a } from "@claread/contracts";
import type { Descendant } from "platejs";
import { deserializeMarkdownToBlocks } from "@/lib/reader-plate/markdown/deserialize";
import { isSafeCalloutEmoji } from "@/lib/source-callout/source-callout-display-icon";

export const READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION =
  "reader-record-plate-document/v1" as const;

export type ReaderRecordPlateDocumentSchemaVersion =
  typeof READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION;

/**
 * Reader Record Plate Document (V2 — standard Plate blocks).
 *
 * children is a flat array of standard Plate block types:
 * - paragraph: 原文段落（anchor segment 文本）
 * - blockquote: 译文引用块
 * - callout: grammar_note / ask_supplement 增强层
 * - sentence_analysis: 句子拆解增强层
 *
 * anchor_segment_id 和 UTF-16 偏移作为 text leaf 的 metadata 保留，
 * 选区读取从 leaf metadata 重建 anchor draft。
 */
export interface ReaderRecordPlateDocument {
  type: "reader_record_plate_document";
  schemaVersion: ReaderRecordPlateDocumentSchemaVersion;
  record: {
    recordId: string;
    title: string;
    generation: number;
    productState: ReaderPlateSnapshotDto["record"]["product_state"];
    readinessState: ReaderPlateSnapshotDto["record"]["readiness_state"];
  };
  snapshot: {
    snapshotId: string;
    snapshotTakenAt: string;
    lastEventSequence: number;
  };
  base: {
    baseId: string;
    contentSha256: string;
    textLengthUtf16: number;
    hashAlgorithm: ReaderPlateSnapshotDto["base"]["hash_algorithm"];
  };
  progress: ReaderRecordPlateProgress;
  children: ReaderRecordPlateBlock[];
}

export interface ReaderRecordPlateImageData {
  sourceUrl: string;
  effectiveUrl: string | null;
  altText: string;
  title: string | null;
  positionKind: "standalone" | "inline";
  stableBlockId: string;
  parentStableBlockId: string | null;
  inlineOrdinal?: number;
  beforeUtf16?: number;
  overrideUrl?: string;
}

export interface ReaderRecordPlateImageBlock {
  type: "image";
  id: string;
  children: [{ text: "" }];
  data: ReaderRecordPlateImageData;
}

export type ReaderRecordPlateInlineNode =
  | ReaderRecordPlateTextLeaf
  | ReaderRecordPlateImageBlock;

export type ReaderRecordPlateBlock =
  | ReaderRecordPlateParagraphBlock<ReaderRecordPlateInlineNode>
  | ReaderRecordPlateBlockquoteBlock
  | ReaderRecordPlateCalloutBlock
  | ReaderRecordPlateSentenceAnalysisBlock
  // Markdown stable-block-derived types. Only emitted when backend
  // `reader_source_block` carries `stableBlockType` metadata; legacy
  // snapshots without `StableBlockAnnotation` fall through to paragraph.
  | ReaderRecordPlateHeadingBlock<ReaderRecordPlateInlineNode>
  | ReaderRecordPlateListBlock
  | ReaderRecordPlateListItemBlock<ReaderRecordPlateInlineNode>
  | ReaderRecordPlateCodeBlockBlock
  | ReaderRecordPlateMarkdownBlockquoteBlock<ReaderRecordPlateInlineNode>
  | ReaderRecordPlateTableBlock
  | ReaderRecordPlateTableRowBlock
  | ReaderRecordPlateTableCellBlock<ReaderRecordPlateInlineNode>
  | ReaderRecordPlateHrBlock
  | ReaderRecordPlateSourceCalloutBlock
  | ReaderRecordPlateImageBlock;

/** 原文段落块 — 一个 source span 对应一个 paragraph */
export interface ReaderRecordPlateParagraphBlock<
  TChild = ReaderRecordPlateTextLeaf,
> {
  type: "paragraph";
  id: string;
  children: TChild[];
  data: ReaderRecordPlateParagraphData;
}

export interface ReaderRecordPlateParagraphData {
  anchorSegmentId: string;
  coveredAnchorSegmentIds: string[];
  sentenceId: string;
  unitId: string;
  isUnitStart?: boolean;
  baseId: string;
  baseRange: ReaderRecordPlateRange;
  unitRange: ReaderRecordPlateRange;
  /** Primary anchor segment hash when a paragraph spans multiple anchors. */
  textHash: string;
  hashAlgorithm: ReaderUnitNodeDto["hash_algorithm"];
  segmentType: AnchorSegmentType;
  boundaryQuality: ReaderBoundaryQuality;
  /** Stable Document identity used by the generic persisted tree projection. */
  stableBlockId?: string | null;
  parentStableBlockId?: string | null;
  /** Optional display-only emoji promoted from a source callout. */
  calloutIcon?: string | null;
}

/** 译文引用块 — backend group-native translation group */
export interface ReaderRecordPlateBlockquoteBlock {
  type: "blockquote";
  id: string;
  children: ReaderRecordPlateTranslationTextLeaf[];
  data: ReaderRecordPlateBlockquoteData;
}

export interface ReaderRecordPlateBlockquoteData {
  unitId: string;
  layerId: string;
  layerVersion: number;
  groupId: string;
  coveredAnchorSegmentIds: string[];
  sourceTextHash: string;
}

/** Callout 增强层块 — grammar_note / ask_supplement */
export interface ReaderRecordPlateCalloutBlock {
  type: "callout";
  id: string;
  variant: ReaderRecordPlateCalloutVariant;
  icon: string;
  /** Plate 节点树，由 projection 层 deserializeMarkdownToBlocks 生成 */
  children: Descendant[];
  data: ReaderRecordPlateCalloutData;
}

export type ReaderRecordPlateCalloutVariant = "grammar" | "supplement";

export interface ReaderRecordPlateCalloutData {
  anchorSegmentId: string;
  unitId: string;
  layerId: string;
  // grammar
  itemId?: string;
  analysisId?: string;
  grammarPoint?: string;
  pattern?: string | null;
  note?: string;
  // ask_supplement
  supplementId?: string;
  supplementType?: string;
  supplementTitle?: string;
  supplementContentMd?: string;
  supplementCreatedAt?: string;
  createdFromTurnRunId?: string;
  lifecycleStatus?: string;
}

/** Sentence analysis 增强层块 — 独立 Plate element，不再伪装成 callout variant */
export interface ReaderRecordPlateSentenceAnalysisBlock {
  type: "sentence_analysis";
  id: string;
  icon: string;
  /** Plate 节点树，由 projection 层 deserializeMarkdownToBlocks 生成 */
  children: Descendant[];
  data: ReaderRecordPlateSentenceAnalysisData;
}

export interface ReaderRecordPlateSentenceAnalysisData {
  anchorSegmentId: string;
  unitId: string;
  layerId: string;
  analysisId: string;
  label: string;
  analysis: string;
  chunks: ReaderRecordPlateSentenceAnalysisChunk[];
}

export interface ReaderRecordPlateSentenceAnalysisChunk
  extends ReaderSentenceAnalysisChunkDto {
  sourceMatch?: {
    anchorSegmentId: string;
    startOffset: number;
    endOffset: number;
    markId: string;
  };
}

// ---------------------------------------------------------------------------
// Markdown stable-block-derived block types.
//
// These block types are projected from `ReaderSourceBlockNodeDto` when the
// backend emits `stableBlockType` metadata. They share a common
// `ReaderRecordPlateStableBlockData` shape that carries anchor-segment /
// base-range / hash info so selection, vocabulary marks and grammar marks
// continue to work on Markdown-rendered blocks.
//
// Legacy snapshots (no `StableBlockAnnotation`) never produce these block
// types — they fall through to the paragraph path.
// ---------------------------------------------------------------------------

/**
 * Shared data for stable-block-derived blocks (heading / list / list_item /
 * code_block / markdown_blockquote / table / table_row / table_cell / hr).
 *
 * Mirrors the essential fields of `ReaderRecordPlateParagraphData` so the
 * DOM contract (data-reader-record-block-id, data-unit-id, etc.) and
 * selection anchor reconstruction work uniformly across paragraph and
 * Markdown block types.
 */
export interface ReaderRecordPlateStableBlockData {
  unitId: string | null;
  baseId?: string;
  baseRange?: ReaderRecordPlateRange;
  /** Primary anchor segment hash when this block covers anchor segments. */
  textHash?: string;
  hashAlgorithm?: ReaderUnitNodeDto["hash_algorithm"];
  /** Backend stable block type string (`heading`/`list`/`list_item`/`code_block`/...). */
  stableBlockType: string;
  /** Diagnostic stable block id from backend (not a render contract). */
  stableBlockId?: string | null;
  /** Parent stable block id for nested structures (table cells → row, etc.). */
  parentStableBlockId?: string | null;
  /** Primary anchor segment id when this block covers anchor segments. */
  anchorSegmentId?: string;
  /** All anchor segment ids covered by this block (may be empty for hr). */
  coveredAnchorSegmentIds: string[];
  /** Boundary quality inherited from the source unit. */
  boundaryQuality?: ReaderBoundaryQuality;
  /** Segment type inherited from primary anchor segment. */
  segmentType?: AnchorSegmentType;
  /** True when this block starts a new unit (used for navigable attrs). */
  isUnitStart?: boolean;
  /** Optional display-only emoji read from the Stable wrapper payload. */
  calloutIcon?: string | null;
  /** Presentation-only list item used to keep promoted images inside valid list markup. */
  presentationOnly?: boolean;
}

/** Markdown 标题块 — `stableBlockType === "heading"` */
export interface ReaderRecordPlateHeadingBlock<
  TChild = ReaderRecordPlateTextLeaf,
> {
  type: "heading";
  id: string;
  /** 1-based heading level (clamped to 1-6). */
  level: 1 | 2 | 3 | 4 | 5 | 6;
  children: TChild[];
  data: ReaderRecordPlateStableBlockData;
}

/** Markdown 列表块 — `stableBlockType === "list"` (wrapper) */
export interface ReaderRecordPlateListBlock {
  type: "list";
  id: string;
  /** True for ordered lists (`1.` / `2.`), false for bullet lists (`-` / `*` / `+`). */
  ordered: boolean;
  children: Array<
    | ReaderRecordPlateListItemBlock<ReaderRecordPlateInlineNode>
    | ReaderRecordPlateImageBlock
  >;
  data: ReaderRecordPlateStableBlockData;
}

/** Markdown 列表项块 — `stableBlockType === "list_item"` */
export interface ReaderRecordPlateListItemBlock<
  TChild = ReaderRecordPlateTextLeaf,
> {
  type: "list_item";
  id: string;
  children: TChild[];
  /** Nested lists are owned by the Stable Document tree, not inferred from adjacency. */
  nestedChildren?: ReaderRecordPlateListBlock[];
  data: ReaderRecordPlateStableBlockData;
}

/** Markdown 代码块 — `stableBlockType === "code_block"` */
export interface ReaderRecordPlateCodeBlockBlock {
  type: "code_block";
  id: string;
  children: ReaderRecordPlateTextLeaf[];
  data: ReaderRecordPlateStableBlockData & {
    /** Language hint from fence info (` ```python `), may be empty / null. */
    language?: string | null;
  };
}

/**
 * Markdown 引用块 — `stableBlockType === "blockquote"`.
 *
 * Distinct from `ReaderRecordPlateBlockquoteBlock` (translation group) to
 * avoid conflating source-text quotes with AI-translated text. The Plate
 * element key is `blockquote` (shared with Markdown plugin), but the data
 * shape differs.
 */
export interface ReaderRecordPlateMarkdownBlockquoteBlock<
  TChild = ReaderRecordPlateTextLeaf,
> {
  type: "markdown_blockquote";
  id: string;
  children: TChild[];
  data: ReaderRecordPlateStableBlockData;
}

/**
 * Notion-style source callout — `contentRole === "source_callout"`.
 *
 * Projected from a stable block whose backend `content_role` is
 * `source_callout` (Notion/HTML `<aside>`, GFM `> [!NOTE]` alerts). The
 * backend emits `block_type="blockquote"` + `contentRole="source_callout"`;
 * the projection overlays a distinct `source_callout` stable type so the
 * Reader renders a calm Notion-style surface (light tint, icon, non-italic)
 * instead of an italic markdown blockquote. Plain markdown `>` quotes keep
 * the `markdown_blockquote` type and remain italic.
 */
export interface ReaderRecordPlateSourceCalloutBlock {
  type: "source_callout";
  id: string;
  /** Stable child blocks; the wrapper never owns a second text truth. */
  children: ReaderRecordPlateTextLeaf[] | ReaderRecordPlateBlock[];
  data: ReaderRecordPlateStableBlockData;
}

/** Markdown 表格块 — `stableBlockType === "table"` (wrapper) */
export interface ReaderRecordPlateTableBlock {
  type: "table";
  id: string;
  children: ReaderRecordPlateTableRowBlock[];
  data: ReaderRecordPlateStableBlockData & {
    /**
     * 列对齐（按列序）。前端由首行单元格的 `alignment` 推导；
     * table wrapper 不产生 reading unit，因此由首行单元格元数据推导。
     */
    alignments?: string[];
    /** 表头行数（前导全 header 行计数，推导自单元格 `isHeader`）。 */
    headerRows?: number;
  };
}

/** Markdown 表格行块 — `stableBlockType === "table_row"` */
export interface ReaderRecordPlateTableRowBlock {
  type: "table_row";
  id: string;
  children: Array<ReaderRecordPlateTableCellBlock<ReaderRecordPlateInlineNode>>;
  data: ReaderRecordPlateStableBlockData & {
    isHeader?: boolean;
    rowIndex?: number;
  };
}

/** Markdown 表格单元格块 — `stableBlockType === "table_cell"` */
export interface ReaderRecordPlateTableCellBlock<
  TChild = ReaderRecordPlateTextLeaf,
> {
  type: "table_cell";
  id: string;
  children: TChild[];
  data: ReaderRecordPlateStableBlockData & {
    columnIndex?: number;
    alignment?: "default" | "left" | "center" | "right";
    isHeader?: boolean;
  };
}

/** Markdown 水平分隔线块 — `stableBlockType === "thematic_break"` */
export interface ReaderRecordPlateHrBlock {
  type: "hr";
  id: string;
  children: [];
  data: ReaderRecordPlateStableBlockData;
}

export interface ReaderRecordPlateProgress {
  overallStatus: ReaderEnhancementProgressOverallStatus | "unknown";
  layers: ReaderRecordPlateProgressLayer[];
}

export interface ReaderRecordPlateProgressLayer {
  id: string;
  capability: ReaderEnhancementCapability;
  layerType?: ReaderLayerType | null;
  status: ReaderEnhancementProgressLayerStatus;
  jobStatus?: ReaderEnhancementProgressLayerDto["job_status"];
  targetScope?: ReaderLayerTargetScope | null;
  targetKey?: string | null;
  layerId?: string | null;
  jobId?: string | null;
  failureCode?: string | null;
  failureMessage?: string | null;
}

export interface ReaderRecordPlateRange {
  startUtf16: number;
  endUtf16: number;
}

export interface ReaderRecordPlateTextLeaf {
  text: string;
  owner: "stable";
  lockSource: true;
  sourceRole: "segment_text" | "separator";
  baseRange: ReaderRecordPlateRange;
  anchorSegmentId?: string;
  segmentRange?: ReaderRecordPlateRange;
  marks: ReaderRecordPlateMark[];
  /**
   * B3: Inline marks (bold / italic / strikethrough / inline_code / link)
   * projected from the backend `ReaderSourceBlockNodeDto.inlineMarks`.
   *
   * Unlike `marks` (vocabulary / grammar / user annotations), inline marks
   * are pure typography spans emitted by the Markdown parser. Offsets are
   * segment-level UTF-16 so they compose with `segmentRange` without
   * requiring a separate anchor shape.
   */
  inlineMarks?: ReaderRecordPlateInlineMark[];
}

/**
 * Inline mark projected from backend `ReaderSourceBlockInlineMarkDto`.
 *
 * `start` / `end` are segment-level UTF-16 offsets (already converted from
 * block-level offsets via `segment.unit_start_utf16`). The renderer slices
 * leaves at mark boundaries so each sub-leaf carries the marks that fully
 * cover its range.
 */
export interface ReaderRecordPlateInlineMark {
  kind: "strong" | "em" | "strikethrough" | "inline_code" | "link";
  /** Segment-level UTF-16 start offset. */
  start: number;
  /** Segment-level UTF-16 end offset (exclusive). */
  end: number;
  /** Safe href for `kind === "link"` (whitelist-filtered by parser). */
  href?: string;
}

export type ReaderRecordPlateMark =
  | ReaderRecordPlateVocabularyMark
  | ReaderRecordPlateGrammarMark
  | ReaderRecordPlateSentenceChunkMark
  | ReaderRecordPlateUserHighlightMark
  | ReaderRecordPlateUserNoteMark;

export interface ReaderRecordPlateTextAnchor {
  anchorType: "text_range";
  baseId: string;
  unitId: string;
  anchorSegmentId: string;
  sentenceId: string;
  segmentType: AnchorSegmentType;
  offsetUnit: "utf16";
  unitStartOffset: number;
  unitEndOffset: number;
  segmentStartOffset: number;
  segmentEndOffset: number;
  selectedText: string;
  textHash: string;
  hashAlgorithm: ReaderUnitNodeDto["hash_algorithm"];
}

interface ReaderRecordPlateMarkBase {
  id: string;
  layerId: string;
  kind: VocabularyItemType | "grammar_note" | "sentence_analysis_chunk";
  owner: "system_ai";
  anchor: ReaderRecordPlateTextAnchor;
  startsHere: boolean;
  endsHere: boolean;
}

export interface ReaderRecordPlateVocabularyMark
  extends ReaderRecordPlateMarkBase {
  kind: VocabularyItemType;
  vocabulary:
    | {
        itemType: "vocab_highlight";
        headword: string;
        briefExplanation?: string | null;
        reason?: string | null;
      }
    | {
        itemType: "phrase_gloss";
        phrase: string;
        phraseType: VocabularyPhraseType;
        gloss: string;
        learningNote?: string | null;
        example?: string | null;
      }
    | {
        itemType: "context_gloss";
        display: string;
        gloss: string;
        reason: string;
      };
}

export interface ReaderRecordPlateGrammarMark
  extends ReaderRecordPlateMarkBase {
  kind: "grammar_note";
  itemId: string;
  spanIndex: number;
  spanCount: number;
  showCue: boolean;
  grammarPoint: string;
  pattern?: string | null;
  note: string;
}

export interface ReaderRecordPlateSentenceChunkMark
  extends ReaderRecordPlateMarkBase {
  kind: "sentence_analysis_chunk";
  analysisId: string;
  order: number;
  label: string;
  text: string;
}

export interface ReaderRecordPlateUserHighlightMark {
  id: string;
  kind: "user_highlight";
  owner: "user";
  assetId: string;
  assetType: string;
  color?: string | null;
  anchor: ReaderRecordPlateTextAnchor;
  createdAt?: string | null;
  updatedAt: string;
}

export interface ReaderRecordPlateUserNoteMark {
  id: string;
  kind: "user_note";
  owner: "user";
  assetId: string;
  assetType: string;
  noteText: string;
  anchor: ReaderRecordPlateTextAnchor;
  createdAt?: string | null;
  updatedAt: string;
}

export interface ReaderRecordPlateTranslationTextLeaf {
  text: string;
  owner: "system_ai";
  sourceRole: "unit_translation_text";
}

// --- Projection context ---

interface UnitProjectionContext {
  snapshot: ReaderPlateSnapshotDto;
  sentenceAnalysisBySegment: Map<
    string,
    ReaderSentenceAnalysisNodeDto[]
  >;
  sentenceChunkMarksBySegment: Map<string, ReaderRecordPlateSentenceChunkMark[]>;
  userHighlightMarksBySegment: Map<string, ReaderRecordPlateUserHighlightMark[]>;
  userNoteMarksBySegment: Map<string, ReaderRecordPlateUserNoteMark[]>;
  supplementsBySegment: Map<string, ReaderSnapshotAskSupplementDto[]>;
  progressByUnit: Map<string, ReaderRecordPlateProgressLayer[]>;
}

/** Typed shape of ReaderSnapshotAskSupplementDto.content from the backend. */
interface ReaderAskSupplementContent {
  supplement_type?: string;
  title?: string;
  content_md?: string;
  target_key?: string | null;
  sentence_id?: string | null;
  paragraph_id?: string | null;
  schema_version?: string;
  created_from_turn_run_id?: string;
  lifecycle_status?: string;
  record_id?: string;
  base_id?: string;
  generation?: number;
}

function parseSupplementContent(
  raw: unknown,
): ReaderAskSupplementContent {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    return raw as ReaderAskSupplementContent;
  }
  return {};
}

// --- Type guards ---

function isSourceBlockNode(
  node: ReaderUnitNodeDto["children"][number],
): node is ReaderSourceBlockNodeDto {
  return node.type === "reader_source_block";
}

function isTranslationGroupNode(
  node: ReaderUnitNodeDto["children"][number],
): node is ReaderTranslationGroupNodeDto {
  return node.type === "reader_translation_group";
}

function isSentenceAnalysisNode(
  node: ReaderUnitNodeDto["children"][number],
): node is ReaderSentenceAnalysisNodeDto {
  return node.type === "reader_sentence_analysis";
}

function isAnchorSegmentNode(
  node: ReaderSourceBlockChildNodeDto,
): node is Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }> {
  return "type" in node && node.type === "reader_anchor_segment";
}

// --- Helpers ---

function range(startUtf16: number, endUtf16: number): ReaderRecordPlateRange {
  return { startUtf16, endUtf16 };
}

function textFromTranslation(node: ReaderTranslationGroupNodeDto): string {
  return node.children.map((child) => child.text).join("");
}

type ReaderAnchorSegmentNode = Extract<
  ReaderSourceBlockChildNodeDto,
  { type: "reader_anchor_segment" }
>;

interface UnitSourceLayout {
  sourceChildren: ReaderSourceBlockChildNodeDto[];
  orderedAnchorSegments: ReaderAnchorSegmentNode[];
  anchorOrderIndexById: Map<string, number>;
  anchorChildIndexById: Map<string, number>;
}

interface TranslationGroupSourceSpan {
  group: ReaderTranslationGroupNodeDto;
  coveredSegments: ReaderAnchorSegmentNode[];
  startAnchorIndex: number;
  endAnchorIndex: number;
  sourceChildren: ReaderSourceBlockChildNodeDto[];
}

type TranslationGroupSourceSpanCandidate = TranslationGroupSourceSpan;

function buildUnitSourceLayout(unit: ReaderUnitNodeDto): UnitSourceLayout {
  const sourceChildren: ReaderSourceBlockChildNodeDto[] = [];
  const orderedAnchorSegments: ReaderAnchorSegmentNode[] = [];
  const anchorOrderIndexById = new Map<string, number>();
  const anchorChildIndexById = new Map<string, number>();

  for (const child of unit.children) {
    if (!isSourceBlockNode(child)) {
      continue;
    }

    for (const sourceChild of child.children) {
      const childIndex = sourceChildren.length;
      sourceChildren.push(sourceChild);

      if (!isAnchorSegmentNode(sourceChild)) {
        continue;
      }

      anchorChildIndexById.set(sourceChild.anchor_segment_id, childIndex);
      anchorOrderIndexById.set(
        sourceChild.anchor_segment_id,
        orderedAnchorSegments.length,
      );
      orderedAnchorSegments.push(sourceChild);
    }
  }

  return {
    sourceChildren,
    orderedAnchorSegments,
    anchorOrderIndexById,
    anchorChildIndexById,
  };
}

function mapProgress(
  progress?: ReaderEnhancementProgressDto,
): ReaderRecordPlateProgress {
  if (!progress) {
    return {
      overallStatus: "unknown",
      layers: [],
    };
  }

  return {
    overallStatus: progress.overall_status,
    layers: progress.layers.map(mapProgressLayer),
  };
}

function mapProgressLayer(
  layer: ReaderEnhancementProgressLayerDto,
): ReaderRecordPlateProgressLayer {
  const targetScope = layer.target_scope ?? "record";
  const targetKey = layer.target_key ?? "record";
  const stableSource =
    layer.layer_id ?? layer.job_id ?? layer.layer_type ?? layer.capability;

  return {
    id: `progress:${layer.capability}:${targetScope}:${targetKey}:${stableSource}`,
    capability: layer.capability,
    layerType: layer.layer_type,
    status: layer.status,
    jobStatus: layer.job_status,
    targetScope: layer.target_scope,
    targetKey: layer.target_key,
    layerId: layer.layer_id,
    jobId: layer.job_id,
    failureCode: layer.failure_code,
    failureMessage: layer.failure_message,
  };
}

function buildProgressByUnit(
  snapshot: ReaderPlateSnapshotDto,
  progress: ReaderRecordPlateProgress,
): Map<string, ReaderRecordPlateProgressLayer[]> {
  const segmentUnitById = new Map<string, string>();
  for (const segment of snapshot.anchor_segments) {
    segmentUnitById.set(segment.anchor_segment_id, segment.unit_id);
  }

  const result = new Map<string, ReaderRecordPlateProgressLayer[]>();
  for (const layer of progress.layers) {
    if (layer.targetScope === "unit" && layer.targetKey) {
      const list = result.get(layer.targetKey) ?? [];
      list.push(layer);
      result.set(layer.targetKey, list);
      continue;
    }
    if (layer.targetScope === "anchor_segment" && layer.targetKey) {
      const unitId = segmentUnitById.get(layer.targetKey);
      if (unitId) {
        const list = result.get(unitId) ?? [];
        list.push(layer);
        result.set(unitId, list);
      }
    }
  }
  return result;
}

function buildSentenceAnalysisBySegment(
  value: ReaderPlateSnapshotDto["value"],
): Map<string, ReaderSentenceAnalysisNodeDto[]> {
  const result = new Map<string, ReaderSentenceAnalysisNodeDto[]>();
  for (const unit of value) {
    for (const child of unit.children) {
      if (!isSentenceAnalysisNode(child)) {
        continue;
      }
      const list = result.get(child.anchor_segment_id) ?? [];
      list.push(child);
      result.set(child.anchor_segment_id, list);
    }
  }
  return result;
}

function sourceTextForSegment(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
): string {
  return segment.children.map((leaf) => leaf.text).join("");
}

function findUniqueChunkMatch(
  sourceText: string,
  chunkText: string,
): { startOffset: number; endOffset: number } | null {
  const needle = chunkText.trim();
  if (!needle) {
    return null;
  }
  const first = sourceText.indexOf(needle);
  if (first < 0) {
    return null;
  }
  const second = sourceText.indexOf(needle, first + needle.length);
  if (second >= 0) {
    return null;
  }
  return {
    startOffset: first,
    endOffset: first + needle.length,
  };
}

function chunkMarkId(analysisId: string, order: number, label: string): string {
  const safeLabel = label.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
  return `sentence_chunk:${analysisId}:${order}:${safeLabel || "chunk"}`;
}

function mapSentenceChunkMark(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  analysis: ReaderSentenceAnalysisNodeDto,
  chunk: ReaderSentenceAnalysisChunkDto,
  sourceText: string,
): ReaderRecordPlateSentenceChunkMark | null {
  const match = findUniqueChunkMatch(sourceText, chunk.text);
  if (!match) {
    return null;
  }
  const selectedText = sourceText.slice(match.startOffset, match.endOffset);
  const markId = chunkMarkId(analysis.analysis_id, chunk.order, chunk.label);
  return {
    id: markId,
    layerId: analysis.layer_id,
    kind: "sentence_analysis_chunk",
    owner: "system_ai",
    anchor: {
      anchorType: "text_range",
      baseId: segment.base_id,
      unitId: segment.unit_id,
      anchorSegmentId: segment.anchor_segment_id,
      sentenceId: segment.sentence_id,
      segmentType: segment.segment_type,
      offsetUnit: "utf16",
      unitStartOffset: segment.unit_start_utf16 + match.startOffset,
      unitEndOffset: segment.unit_start_utf16 + match.endOffset,
      segmentStartOffset: match.startOffset,
      segmentEndOffset: match.endOffset,
      selectedText,
      textHash: computeUtf16FNV1a(selectedText),
      hashAlgorithm: segment.hash_algorithm,
    },
    startsHere: true,
    endsHere: true,
    analysisId: analysis.analysis_id,
    order: chunk.order,
    label: chunk.label,
    text: selectedText,
  };
}

function buildSentenceChunkMarksBySegment(
  value: ReaderPlateSnapshotDto["value"],
): Map<string, ReaderRecordPlateSentenceChunkMark[]> {
  const result = new Map<string, ReaderRecordPlateSentenceChunkMark[]>();

  for (const unit of value) {
    const sourceSegments = unit.children.flatMap((child) =>
      isSourceBlockNode(child)
        ? child.children.filter(isAnchorSegmentNode)
        : [],
    );
    const analyses = unit.children.filter(isSentenceAnalysisNode);

    for (const analysis of analyses) {
      const segment = sourceSegments.find(
        (candidate) =>
          candidate.anchor_segment_id === analysis.anchor_segment_id,
      );
      if (!segment) {
        continue;
      }
      const sourceText = sourceTextForSegment(segment);
      const marks = analysis.chunks.flatMap((chunk) => {
        const mark = mapSentenceChunkMark(segment, analysis, chunk, sourceText);
        return mark ? [mark] : [];
      });
      if (marks.length === 0) {
        continue;
      }
      const list = result.get(segment.anchor_segment_id) ?? [];
      list.push(...marks);
      result.set(segment.anchor_segment_id, list);
    }
  }

  return result;
}

function isHighlightAssetType(assetType: string): boolean {
  return (
    assetType === "quick_highlight" ||
    assetType === "highlight" ||
    assetType === "user_highlight"
  );
}

function segmentById(
  snapshot: ReaderPlateSnapshotDto,
  anchorSegmentId: string,
) {
  return snapshot.anchor_segments.find(
    (segment) => segment.anchor_segment_id === anchorSegmentId,
  );
}

function normalizedUserAssetAnchor(
  snapshot: ReaderPlateSnapshotDto,
  asset: ReaderSnapshotUserAssetDto,
): ReaderRecordPlateTextAnchor | null {
  if (asset.deleted_at) {
    return null;
  }

  if (
    asset.reading_record_id !== snapshot.record_id ||
    asset.generation !== snapshot.record.generation ||
    asset.anchor.anchor_type !== "text_range"
  ) {
    return null;
  }

  const anchor = asset.anchor;
  if (
    anchor.base_id !== snapshot.base.base_id ||
    anchor.end_offset <= anchor.start_offset ||
    anchor.selected_text.length === 0 ||
    anchor.end_offset - anchor.start_offset !== anchor.selected_text.length ||
    anchor.text_hash !== computeUtf16FNV1a(anchor.selected_text)
  ) {
    return null;
  }

  const segment = segmentById(snapshot, anchor.anchor_segment_id);
  if (!segment || segment.unit_id !== anchor.unit_id) {
    return null;
  }

  const segmentStartOffset = anchor.start_offset - segment.unit_start_utf16;
  const segmentEndOffset = anchor.end_offset - segment.unit_start_utf16;
  if (
    segmentStartOffset < 0 ||
    segmentEndOffset > segment.unit_end_utf16 - segment.unit_start_utf16
  ) {
    return null;
  }

  return {
    anchorType: "text_range",
    baseId: anchor.base_id,
    unitId: anchor.unit_id,
    anchorSegmentId: anchor.anchor_segment_id,
    sentenceId: anchor.sentence_id ?? segment.sentence_id,
    segmentType: anchor.segment_type,
    offsetUnit: "utf16",
    unitStartOffset: anchor.start_offset,
    unitEndOffset: anchor.end_offset,
    segmentStartOffset,
    segmentEndOffset,
    selectedText: anchor.selected_text,
    textHash: anchor.text_hash,
    hashAlgorithm: anchor.hash_algorithm,
  };
}

function buildUserAssetsBySegment(snapshot: ReaderPlateSnapshotDto): {
  highlights: Map<string, ReaderRecordPlateUserHighlightMark[]>;
  noteMarks: Map<string, ReaderRecordPlateUserNoteMark[]>;
} {
  const highlights = new Map<string, ReaderRecordPlateUserHighlightMark[]>();
  const noteMarks = new Map<string, ReaderRecordPlateUserNoteMark[]>();

  for (const asset of snapshot.user_assets) {
    const anchor = normalizedUserAssetAnchor(snapshot, asset);
    if (!anchor) {
      continue;
    }

    if (isHighlightAssetType(asset.asset_type)) {
      const list = highlights.get(anchor.anchorSegmentId) ?? [];
      list.push({
        id: `user_highlight:${asset.asset_id}`,
        kind: "user_highlight",
        owner: "user",
        assetId: asset.asset_id,
        assetType: asset.asset_type,
        color: asset.color ?? null,
        anchor,
        createdAt: asset.created_at,
        updatedAt: asset.updated_at,
      });
      highlights.set(anchor.anchorSegmentId, list);
      continue;
    }

    if (
      asset.asset_type === "comment" ||
      asset.asset_type === "note" ||
      asset.asset_type === "reader_note"
    ) {
      const list = noteMarks.get(anchor.anchorSegmentId) ?? [];
      list.push({
        id: `user_note:${asset.asset_id}`,
        kind: "user_note",
        owner: "user",
        assetId: asset.asset_id,
        assetType: asset.asset_type,
        noteText: asset.note_text ?? "",
        anchor,
        createdAt: asset.created_at,
        updatedAt: asset.updated_at,
      });
      noteMarks.set(anchor.anchorSegmentId, list);
    }
  }

  return { highlights, noteMarks };
}

function marksForRange(
  marks: ReaderRecordPlateMark[],
  startUtf16: number,
  endUtf16: number,
): ReaderRecordPlateMark[] {
  return marks
    .filter(
      (mark) =>
        mark.anchor.segmentStartOffset <= startUtf16 &&
        mark.anchor.segmentEndOffset >= endUtf16,
    )
    .map((mark) => {
      if ("startsHere" in mark && "endsHere" in mark) {
        return {
          ...mark,
          startsHere: mark.anchor.segmentStartOffset === startUtf16,
          endsHere: mark.anchor.segmentEndOffset === endUtf16,
        };
      }
      return mark;
    });
}

function markBoundariesForLeaf(
  leafStartUtf16: number,
  leafEndUtf16: number,
  marks: ReaderRecordPlateMark[],
): number[] {
  const boundaries = new Set<number>([leafStartUtf16, leafEndUtf16]);
  for (const mark of marks) {
    const start = Math.max(leafStartUtf16, mark.anchor.segmentStartOffset);
    const end = Math.min(leafEndUtf16, mark.anchor.segmentEndOffset);
    if (start < end) {
      boundaries.add(start);
      boundaries.add(end);
    }
  }
  return [...boundaries].sort((a, b) => a - b);
}

function splitTextLeafByMarks(
  leaf: ReaderStableSegmentTextLeafDto,
  marks: ReaderRecordPlateMark[],
): ReaderRecordPlateTextLeaf[] {
  const leafStart = leaf.segment_start_utf16;
  const leafEnd = leaf.segment_end_utf16;
  const boundaries = markBoundariesForLeaf(leafStart, leafEnd, marks);
  const projected: ReaderRecordPlateTextLeaf[] = [];

  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const start = boundaries[index];
    const end = boundaries[index + 1];
    if (end <= start) {
      continue;
    }

    const relativeStart = start - leafStart;
    const relativeEnd = end - leafStart;
    const baseStart = leaf.base_start_utf16 + relativeStart;

    projected.push({
      text: leaf.text.slice(relativeStart, relativeEnd),
      owner: "stable",
      lockSource: true,
      sourceRole: "segment_text",
      baseRange: range(baseStart, baseStart + (end - start)),
      anchorSegmentId: leaf.anchor_segment_id,
      segmentRange: range(start, end),
      marks: marksForRange(marks, start, end),
    });
  }

  return projected;
}

function mapSeparatorLeaf(
  leaf: ReaderStableSeparatorLeafDto,
): ReaderRecordPlateTextLeaf {
  return {
    text: leaf.text,
    owner: "stable",
    lockSource: true,
    sourceRole: "separator",
    baseRange: range(leaf.base_start_utf16, leaf.base_end_utf16),
    marks: [],
  };
}

function markAnchor(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  mark: ReaderVocabularyMarkDto | ReaderGrammarNoteMarkDto,
): ReaderRecordPlateTextAnchor {
  return {
    anchorType: "text_range",
    baseId: segment.base_id,
    unitId: segment.unit_id,
    anchorSegmentId: mark.anchor_segment_id,
    sentenceId: segment.sentence_id,
    segmentType: segment.segment_type,
    offsetUnit: "utf16",
    unitStartOffset: mark.start_offset,
    unitEndOffset: mark.end_offset,
    segmentStartOffset: mark.segment_start_utf16,
    segmentEndOffset: mark.segment_end_utf16,
    selectedText: mark.selected_text,
    textHash: computeUtf16FNV1a(mark.selected_text),
    hashAlgorithm: segment.hash_algorithm,
  };
}

function mapVocabularyMark(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  mark: ReaderVocabularyMarkDto,
): ReaderRecordPlateVocabularyMark {
  const base = {
    id: mark.mark_id,
    layerId: mark.layer_id,
    kind: mark.item_type,
    owner: "system_ai" as const,
    anchor: markAnchor(segment, mark),
    startsHere: mark.starts_here,
    endsHere: mark.ends_here,
  };

  switch (mark.item_type) {
    case "vocab_highlight":
      return {
        ...base,
        vocabulary: {
          itemType: "vocab_highlight",
          headword: mark.headword,
          briefExplanation: mark.brief_explanation,
          reason: mark.reason,
        },
      };
    case "phrase_gloss":
      return {
        ...base,
        vocabulary: {
          itemType: "phrase_gloss",
          phrase: mark.phrase,
          phraseType: mark.phrase_type,
          gloss: mark.gloss,
          learningNote: mark.learning_note,
          example: mark.example,
        },
      };
    case "context_gloss":
      return {
        ...base,
        vocabulary: {
          itemType: "context_gloss",
          display: mark.display,
          gloss: mark.gloss,
          reason: mark.reason,
        },
      };
  }
}

function mapGrammarMark(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  mark: ReaderGrammarNoteMarkDto,
): ReaderRecordPlateGrammarMark {
  return {
    id: mark.mark_id,
    layerId: mark.layer_id,
    kind: "grammar_note",
    owner: "system_ai",
    anchor: markAnchor(segment, mark),
    startsHere: mark.starts_here,
    endsHere: mark.ends_here,
    itemId: mark.item_id,
    spanIndex: mark.span_index,
    spanCount: mark.span_count,
    showCue: mark.show_note_chip,
    grammarPoint: mark.grammar_point,
    pattern: mark.pattern,
    note: mark.note,
  };
}

function mapTextLeaf(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  leaf: ReaderStableSegmentTextLeafDto,
  context: UnitProjectionContext,
  /**
   * B3: Source block carrying inline marks (bold / italic / strikethrough /
   * inline_code / link). Only stable-block-derived blocks pass a source
   * block; legacy paragraph projection omits it so inline marks stay
   * disabled on the paragraph path.
   */
  sourceBlock?: ReaderSourceBlockNodeDto,
): ReaderRecordPlateTextLeaf[] {
  const vocabularyMarks =
    leaf.reader_vocabulary_marks?.map((mark) => mapVocabularyMark(segment, mark)) ??
    [];
  const grammarMarks =
    leaf.reader_grammar_note_marks?.map((mark) => mapGrammarMark(segment, mark)) ?? [];
  const userHighlightMarks =
    context.userHighlightMarksBySegment.get(segment.anchor_segment_id) ?? [];
  const userNoteMarks =
    context.userNoteMarksBySegment.get(segment.anchor_segment_id) ?? [];
  const sentenceChunkMarks =
    context.sentenceChunkMarksBySegment.get(segment.anchor_segment_id) ?? [];
  const marks = [
    ...vocabularyMarks,
    ...grammarMarks,
    ...sentenceChunkMarks,
    ...userHighlightMarks,
    ...userNoteMarks,
  ];

  const projectedLeaves = splitTextLeafByMarks(leaf, marks);

  // B3: when the backend emitted inline marks for this stable block,
  // further slice each sub-leaf at inline mark boundaries so the
  // renderer can apply bold / italic / strikethrough / code / link
  // styling. Block-level offsets are converted to segment-level via
  // `segment.unit_start_utf16` (block text === unit text for stable
  // blocks projected by `buildStableBlockForSourceSpan`).
  const blockInlineMarks = sourceBlock?.inlineMarks;
  if (!blockInlineMarks || blockInlineMarks.length === 0) {
    return projectedLeaves;
  }

  return projectedLeaves.flatMap((subLeaf) =>
    splitLeafByInlineMarks(subLeaf, blockInlineMarks, segment.unit_start_utf16),
  );
}

/**
 * B3: split a projected text leaf at inline mark boundaries.
 *
 * `blockInlineMarks` carries block-level UTF-16 offsets (relative to the
 * stable block's canonical text). `segmentOffsetInBlock` is the segment's
 * start offset within the block text (= `segment.unit_start_utf16` since
 * block text === unit text for stable blocks). We convert mark offsets to
 * segment-level, clamp to the leaf's segment range, then slice the leaf
 * at every boundary so each sub-leaf carries only the marks that fully
 * cover it.
 *
 * Sub-leaves preserve `marks` (vocabulary / grammar / user annotations)
 * and `baseRange` is recomputed from the original leaf's base start so
 * selection anchors stay valid.
 */
function splitLeafByInlineMarks(
  leaf: ReaderRecordPlateTextLeaf,
  blockInlineMarks: ReaderSourceBlockInlineMarkDto[],
  segmentOffsetInBlock: number,
): ReaderRecordPlateTextLeaf[] {
  if (!leaf.segmentRange || !leaf.anchorSegmentId) {
    // Separator leaf or leaf without anchor metadata — inline marks do
    // not apply. Return as-is.
    return [leaf];
  }

  const leafSegmentStart = leaf.segmentRange.startUtf16;
  const leafSegmentEnd = leaf.segmentRange.endUtf16;

  // Convert block-level inline marks to segment-level and keep only
  // those overlapping this leaf's range.
  const segmentInlineMarks: ReaderRecordPlateInlineMark[] = [];
  for (const mark of blockInlineMarks) {
    if (typeof mark.start !== "number" || typeof mark.end !== "number") {
      continue;
    }
    const segStart = mark.start - segmentOffsetInBlock;
    const segEnd = mark.end - segmentOffsetInBlock;
    // Skip marks that do not overlap the leaf range.
    if (segEnd <= leafSegmentStart || segStart >= leafSegmentEnd) {
      continue;
    }
    // Clamp mark range to leaf range (mark may extend beyond leaf).
    const clampedStart = Math.max(segStart, leafSegmentStart);
    const clampedEnd = Math.min(segEnd, leafSegmentEnd);
    if (clampedEnd <= clampedStart) {
      continue;
    }
    segmentInlineMarks.push({
      kind: mark.type,
      start: clampedStart,
      end: clampedEnd,
      href: mark.href,
    });
  }

  if (segmentInlineMarks.length === 0) {
    return [leaf];
  }

  // Compute split boundaries from leaf range + mark boundaries.
  const boundaries = new Set<number>([leafSegmentStart, leafSegmentEnd]);
  for (const mark of segmentInlineMarks) {
    boundaries.add(mark.start);
    boundaries.add(mark.end);
  }
  const sortedBoundaries = [...boundaries].sort((a, b) => a - b);

  const leafBaseStart = leaf.baseRange.startUtf16;
  const result: ReaderRecordPlateTextLeaf[] = [];

  for (let i = 0; i < sortedBoundaries.length - 1; i += 1) {
    const start = sortedBoundaries[i];
    const end = sortedBoundaries[i + 1];
    if (end <= start) {
      continue;
    }

    const relativeStart = start - leafSegmentStart;
    const relativeEnd = end - leafSegmentStart;

    // Find marks that fully cover this sub-range.
    const coveringMarks = segmentInlineMarks.filter(
      (m) => m.start <= start && m.end >= end,
    );

    result.push({
      ...leaf,
      text: leaf.text.slice(relativeStart, relativeEnd),
      baseRange: range(
        leafBaseStart + relativeStart,
        leafBaseStart + relativeEnd,
      ),
      segmentRange: range(start, end),
      inlineMarks: coveringMarks.length > 0 ? coveringMarks : undefined,
    });
  }

  return result;
}

// --- Block builders ---

function buildParagraphBlockForSourceSpan(
  sourceChildren: ReaderSourceBlockChildNodeDto[],
  anchorSegments: ReaderAnchorSegmentNode[],
  context: UnitProjectionContext,
  options: { isUnitStart?: boolean } = {},
): ReaderRecordPlateParagraphBlock {
  if (anchorSegments.length === 0) {
    throw new Error("Expected at least one anchor segment in source span");
  }

  const primaryAnchor = anchorSegments[0];
  const terminalAnchor = anchorSegments[anchorSegments.length - 1];
  const children = sourceChildren.flatMap((child) =>
    isAnchorSegmentNode(child)
      ? child.children.flatMap((leaf) => mapTextLeaf(child, leaf, context))
      : [mapSeparatorLeaf(child)],
  );

  return {
    type: "paragraph",
    id: `paragraph:${primaryAnchor.anchor_segment_id}`,
    children,
    data: {
      anchorSegmentId: primaryAnchor.anchor_segment_id,
      coveredAnchorSegmentIds: anchorSegments.map(
        (segment) => segment.anchor_segment_id,
      ),
      sentenceId: primaryAnchor.sentence_id,
      unitId: primaryAnchor.unit_id,
      isUnitStart: options.isUnitStart || undefined,
      baseId: primaryAnchor.base_id,
      baseRange: range(
        primaryAnchor.base_start_utf16,
        terminalAnchor.base_end_utf16,
      ),
      unitRange: range(
        primaryAnchor.unit_start_utf16,
        terminalAnchor.unit_end_utf16,
      ),
      textHash: primaryAnchor.text_hash,
      hashAlgorithm: primaryAnchor.hash_algorithm,
      segmentType: primaryAnchor.segment_type,
      boundaryQuality: primaryAnchor.boundary_quality,
    },
  };
}

function buildParagraphBlock(
  segment: ReaderAnchorSegmentNode,
  context: UnitProjectionContext,
  options: { isUnitStart?: boolean } = {},
): ReaderRecordPlateParagraphBlock {
  return buildParagraphBlockForSourceSpan([segment], [segment], context, options);
}

// ---------------------------------------------------------------------------
// B2.3: Stable-block-derived block builders.
//
// When a unit's `reader_source_block` carries `stableBlockType` metadata
// (A5), we project the unit to the corresponding Markdown block type
// instead of a plain paragraph. The text leaves, anchor-segment metadata,
// and translation/annotation flow remain identical — only the block type
// and `data` shape change.
//
// Wrapper blocks (`list` / `table` / `table_row`) are not reconstructed
// here; they are assembled in B2.6 by grouping child blocks via
// `parentStableBlockId`. This function only emits leaf/standalone blocks.
// ---------------------------------------------------------------------------

/** Recognized stable block types that map to a non-paragraph Plate block. */
const STABLE_BLOCK_TYPES_WITH_PLATE_PROJECTION: ReadonlySet<string> = new Set([
  // R1: `paragraph` must take the stable projection path too — otherwise
  // paragraph units fall through to the legacy builder, which does not
  // receive the source block and silently drops inlineMarks (emphasis /
  // strong / code / strikethrough / link degrade to plain text even when
  // the snapshot carries the marks).
  "paragraph",
  "heading",
  "code_block",
  "blockquote",
  "thematic_break",
  "list_item",
  "table_cell",
  // source_callout is a content-role overlay: the backend emits
  // block_type="blockquote" + contentRole="source_callout" for Notion/HTML
  // <aside> blocks. We project it as a distinct stable type so the Reader
  // renders a Notion-style callout instead of an italic markdown blockquote.
  "source_callout",
]);

/**
 * Extract the first `reader_source_block` child from a unit. A unit
 * typically has exactly one source block; if absent (legacy / malformed),
 * returns `null` so the caller falls through to the paragraph path.
 */
function findUnitSourceBlock(
  unit: ReaderUnitNodeDto,
): ReaderSourceBlockNodeDto | null {
  for (const child of unit.children) {
    if (isSourceBlockNode(child)) {
      return child;
    }
  }
  return null;
}

/**
 * Determine whether a unit should be projected as a stable Markdown block
 * (heading / code_block / etc.) rather than the legacy paragraph path.
 * Returns the stable block type string when a projection applies, or
 * `null` to fall through to the paragraph path.
 */
function getUnitStableBlockType(unit: ReaderUnitNodeDto): string | null {
  const sourceBlock = findUnitSourceBlock(unit);
  if (!sourceBlock) {
    return null;
  }
  const rawType = sourceBlock.stableBlockType;
  if (typeof rawType !== "string" || rawType.length === 0) {
    return null;
  }
  // source_callout content-role overlay: backend emits block_type="blockquote"
  // + contentRole="source_callout" for Notion/HTML <aside> / GFM alerts.
  // Project as a distinct stable type so the Reader renders a Notion-style
  // callout surface instead of an italic markdown blockquote.
  if (
    sourceBlock.contentRole === "source_callout" &&
    rawType === "blockquote"
  ) {
    return "source_callout";
  }
  if (!STABLE_BLOCK_TYPES_WITH_PLATE_PROJECTION.has(rawType)) {
    return null;
  }
  return rawType;
}

/**
 * Build the shared `ReaderRecordPlateStableBlockData` from anchor-segment
 * metadata + source-block metadata. Mirrors the paragraph data shape so
 * selection, vocabulary marks, and grammar marks work uniformly.
 */
function buildStableBlockData(
  anchorSegments: ReaderAnchorSegmentNode[],
  sourceBlock: ReaderSourceBlockNodeDto,
  unit: ReaderUnitNodeDto,
  options: { isUnitStart?: boolean; calloutIcon?: string | null } = {},
): ReaderRecordPlateStableBlockData {
  const primaryAnchor = anchorSegments[0];
  const terminalAnchor = anchorSegments[anchorSegments.length - 1];
  const coveredAnchorSegmentIds = anchorSegments.map(
    (segment) => segment.anchor_segment_id,
  );

  return {
    unitId: unit.unit_id,
    baseId: unit.base_id,
    baseRange: range(
      primaryAnchor.base_start_utf16,
      terminalAnchor.base_end_utf16,
    ),
    textHash: primaryAnchor.text_hash,
    hashAlgorithm: primaryAnchor.hash_algorithm,
    stableBlockType: sourceBlock.stableBlockType ?? "paragraph",
    stableBlockId: sourceBlock.stableBlockId ?? null,
    parentStableBlockId: sourceBlock.parentStableBlockId ?? null,
    anchorSegmentId: primaryAnchor.anchor_segment_id,
    coveredAnchorSegmentIds,
    boundaryQuality: unit.boundary_quality,
    segmentType: primaryAnchor.segment_type,
    isUnitStart: options.isUnitStart || undefined,
    calloutIcon: options.calloutIcon ?? null,
  };
}

/**
 * Project a source span to the appropriate stable Markdown block type
 * based on `stableBlockType`. Falls back to the paragraph builder when
 * the type is not recognized (defensive — should not happen after
 * `getUnitStableBlockType` gates entry).
 */
function buildStableBlockForSourceSpan(
  sourceChildren: ReaderSourceBlockChildNodeDto[],
  anchorSegments: ReaderAnchorSegmentNode[],
  context: UnitProjectionContext,
  unit: ReaderUnitNodeDto,
  sourceBlock: ReaderSourceBlockNodeDto,
  options: { isUnitStart?: boolean } = {},
): ReaderRecordPlateBlock {
  if (anchorSegments.length === 0) {
    throw new Error("Expected at least one anchor segment in source span");
  }

  const stableType =
    sourceBlock.contentRole === "source_callout" &&
    sourceBlock.stableBlockType === "blockquote"
      ? "source_callout"
      : sourceBlock.stableBlockType;
  const children = sourceChildren.flatMap((child) =>
    isAnchorSegmentNode(child)
      ? child.children.flatMap((leaf) =>
          mapTextLeaf(child, leaf, context, sourceBlock),
        )
      : [mapSeparatorLeaf(child)],
  );
  const data = buildStableBlockData(anchorSegments, sourceBlock, unit, {
    ...options,
    // Reader icon projection is owned by the persisted Stable wrapper
    // payload. The flat compatibility path has no wrapper payload and must
    // not infer an icon by hiding the first body child.
    calloutIcon: null,
  });

  // Clamp heading level to 1-6; default to 1 when missing/invalid.
  const headingLevel =
    typeof sourceBlock.headingLevel === "number" &&
    Number.isFinite(sourceBlock.headingLevel) &&
    sourceBlock.headingLevel >= 1 &&
    sourceBlock.headingLevel <= 6
      ? (Math.trunc(sourceBlock.headingLevel) as 1 | 2 | 3 | 4 | 5 | 6)
      : 1;

  const primaryAnchor = anchorSegments[0];

  switch (stableType) {
    case "paragraph": {
      // R1: stable paragraph — reuse the source-block-mapped `children`
      // (inline marks applied) with the full paragraph data shape so
      // selection, vocabulary marks and grammar marks keep working exactly
      // as on the legacy path.
      const terminalAnchor = anchorSegments[anchorSegments.length - 1];
      return {
        type: "paragraph",
        id: `paragraph:${primaryAnchor.anchor_segment_id}`,
        children,
        data: {
          anchorSegmentId: primaryAnchor.anchor_segment_id,
          coveredAnchorSegmentIds: anchorSegments.map(
            (segment) => segment.anchor_segment_id,
          ),
          sentenceId: primaryAnchor.sentence_id,
          unitId: primaryAnchor.unit_id,
          isUnitStart: options.isUnitStart || undefined,
          baseId: primaryAnchor.base_id,
          baseRange: range(
            primaryAnchor.base_start_utf16,
            terminalAnchor.base_end_utf16,
          ),
          unitRange: range(
            primaryAnchor.unit_start_utf16,
            terminalAnchor.unit_end_utf16,
          ),
          textHash: primaryAnchor.text_hash,
          hashAlgorithm: primaryAnchor.hash_algorithm,
          segmentType: primaryAnchor.segment_type,
          boundaryQuality: primaryAnchor.boundary_quality,
          stableBlockId: sourceBlock.stableBlockId ?? null,
          parentStableBlockId: sourceBlock.parentStableBlockId ?? null,
          calloutIcon: null,
        },
      };
    }
    case "heading":
      return {
        type: "heading",
        id: `heading:${primaryAnchor.anchor_segment_id}`,
        level: headingLevel,
        children,
        data,
      };
    case "code_block":
      return {
        type: "code_block",
        id: `code_block:${primaryAnchor.anchor_segment_id}`,
        children,
        data: {
          ...data,
          // L1: 消费后端 DTO `codeLanguage`（fence info string；
          // 无语言代码块为 null）。legacy snapshot 无该字段 → null。
          language: sourceBlock.codeLanguage ?? null,
        },
      };
    case "blockquote":
      return {
        type: "markdown_blockquote",
        id: `markdown_blockquote:${primaryAnchor.anchor_segment_id}`,
        children,
        data,
      };
    case "source_callout":
      return {
        type: "source_callout",
        id: `source_callout:${primaryAnchor.anchor_segment_id}`,
        children,
        data,
      };
    case "thematic_break":
      return {
        type: "hr",
        id: `hr:${primaryAnchor.anchor_segment_id}`,
        children: [],
        data,
      };
    case "list_item":
      return {
        type: "list_item",
        id: `list_item:${primaryAnchor.anchor_segment_id}`,
        children,
        data,
      };
    case "table_cell":
      return {
        type: "table_cell",
        id: `table_cell:${primaryAnchor.anchor_segment_id}`,
        children,
        data: {
          ...data,
          // L1: 消费后端 DTO `tableAlignment` / `tableIsHeader`
          // （逐单元格对齐与表头标记）。legacy snapshot 无该字段 →
          // 默认 default / false。columnIndex 由 B2.6 行重组按列位补齐。
          alignment: sourceBlock.tableAlignment ?? "default",
          isHeader: sourceBlock.tableIsHeader ?? false,
        },
      };
    default:
      // Defensive: unrecognized stable type → fall back to paragraph.
      return buildParagraphBlockForSourceSpan(
        sourceChildren,
        anchorSegments,
        context,
        options,
      );
  }
}

function buildBlockquoteBlock(
  node: ReaderTranslationGroupNodeDto,
): ReaderRecordPlateBlockquoteBlock | null {
  if (node.target_scope !== "unit") {
    return null;
  }

  return {
    type: "blockquote",
    id: `blockquote:${node.layer_id}:${node.group_id}`,
    children: [
      {
        text: textFromTranslation(node),
        owner: "system_ai",
        sourceRole: "unit_translation_text",
      },
    ],
    data: {
      unitId: node.unit_id,
      layerId: node.layer_id,
      layerVersion: node.layer_version,
      groupId: node.group_id,
      coveredAnchorSegmentIds: node.covered_anchor_segment_ids,
      sourceTextHash: node.source_text_hash,
    },
  };
}

function buildGrammarCalloutBlocks(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
): ReaderRecordPlateCalloutBlock[] {
  const callouts: ReaderRecordPlateCalloutBlock[] = [];
  const seenGrammarItems = new Set<string>();

  for (const leaf of segment.children) {
    const grammarMarks = leaf.reader_grammar_note_marks ?? [];
    for (const mark of grammarMarks) {
      if (!mark.show_note_chip) {
        continue;
      }
      const calloutId = `callout:grammar:${mark.item_id}`;
      if (seenGrammarItems.has(calloutId)) {
        continue;
      }
      seenGrammarItems.add(calloutId);
      const rawAnalysisId = (mark as { analysis_id?: unknown }).analysis_id;
      const analysisId =
        typeof rawAnalysisId === "string"
          ? rawAnalysisId.trim() || undefined
          : undefined;
      callouts.push({
        type: "callout",
        id: calloutId,
        variant: "grammar",
        icon: "📖",
        children: deserializeMarkdownToBlocks(mark.note),
        data: {
          anchorSegmentId: mark.anchor_segment_id,
          unitId: segment.unit_id,
          layerId: mark.layer_id,
          itemId: mark.item_id,
          ...(analysisId ? { analysisId } : {}),
          grammarPoint: mark.grammar_point,
          pattern: mark.pattern,
          note: mark.note,
        },
      });
    }
  }

  return callouts;
}

function buildSentenceAnalysisBlocks(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  context: UnitProjectionContext,
): ReaderRecordPlateSentenceAnalysisBlock[] {
  const analyses =
    context.sentenceAnalysisBySegment.get(segment.anchor_segment_id) ?? [];
  const chunkMarks = context.sentenceChunkMarksBySegment.get(
    segment.anchor_segment_id,
  ) ?? [];

  return analyses.map((node) => ({
    type: "sentence_analysis" as const,
    id: `sentence_analysis:${node.analysis_id}`,
    icon: "🔍",
    children: deserializeMarkdownToBlocks(node.analysis),
    data: {
      anchorSegmentId: node.anchor_segment_id,
      unitId: node.unit_id,
      layerId: node.layer_id,
      analysisId: node.analysis_id,
      label: node.label,
      analysis: node.analysis,
      chunks: node.chunks.map((chunk) => {
        const mark = chunkMarks.find(
          (candidate) =>
            candidate.analysisId === node.analysis_id &&
            candidate.order === chunk.order &&
            candidate.label === chunk.label,
        );
        if (!mark) {
          return chunk;
        }
        return {
          ...chunk,
          sourceMatch: {
            anchorSegmentId: mark.anchor.anchorSegmentId,
            startOffset: mark.anchor.segmentStartOffset,
            endOffset: mark.anchor.segmentEndOffset,
            markId: mark.id,
          },
        };
      }),
    },
  }));
}

function buildSupplementsBySegment(
  snapshot: ReaderPlateSnapshotDto,
): Map<string, ReaderSnapshotAskSupplementDto[]> {
  const result = new Map<string, ReaderSnapshotAskSupplementDto[]>();
  for (const supplement of snapshot.ask_supplements) {
    const anchor = supplement.anchor;
    if (!anchor || anchor.anchor_type !== "text_range") {
      continue;
    }
    if (
      anchor.base_id !== snapshot.base.base_id ||
      anchor.end_offset <= anchor.start_offset ||
      anchor.selected_text.length === 0 ||
      anchor.end_offset - anchor.start_offset !== anchor.selected_text.length ||
      anchor.text_hash !== computeUtf16FNV1a(anchor.selected_text)
    ) {
      continue;
    }
    const segment = snapshot.anchor_segments.find(
      (seg) =>
        seg.anchor_segment_id === anchor.anchor_segment_id &&
        seg.unit_id === anchor.unit_id,
    );
    if (!segment) {
      continue;
    }
    const list = result.get(anchor.anchor_segment_id) ?? [];
    list.push(supplement);
    result.set(anchor.anchor_segment_id, list);
  }
  return result;
}

function buildSupplementCalloutBlocks(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  context: UnitProjectionContext,
): ReaderRecordPlateCalloutBlock[] {
  const supplements =
    context.supplementsBySegment.get(segment.anchor_segment_id) ?? [];

  return supplements.map((supplement) => {
    const content = parseSupplementContent(supplement.content);
    const contentMd = content.content_md ?? "";
    const title = content.title ?? "AI 补充语法旁注";
    return {
      type: "callout" as const,
      id: `callout:supplement:${supplement.supplement_id}`,
      variant: "supplement" as const,
      icon: "💬",
      children: deserializeMarkdownToBlocks(contentMd),
      data: {
        anchorSegmentId: segment.anchor_segment_id,
        unitId: segment.unit_id,
        layerId: `ask_supplement:${supplement.supplement_id}`,
        supplementId: supplement.supplement_id,
        supplementType: content.supplement_type ?? "grammar_note",
        supplementTitle: title,
        supplementContentMd: contentMd,
        supplementCreatedAt: supplement.created_at,
        createdFromTurnRunId: content.created_from_turn_run_id ?? "",
        lifecycleStatus: content.lifecycle_status ?? "persisted",
      },
    };
  });
}

function buildAnnotationBlocksForSegments(
  segments: ReaderAnchorSegmentNode[],
  context: UnitProjectionContext,
  options?: { supplementEligible?: boolean },
): ReaderRecordPlateBlock[] {
  const blocks: ReaderRecordPlateBlock[] = [];
  const supplementEligible = options?.supplementEligible ?? true;

  for (const segment of segments) {
    blocks.push(...buildGrammarCalloutBlocks(segment));
    blocks.push(...buildSentenceAnalysisBlocks(segment, context));
    if (supplementEligible) {
      blocks.push(...buildSupplementCalloutBlocks(segment, context));
    }
  }

  return blocks;
}

function buildValidTranslationGroupSpans(
  unit: ReaderUnitNodeDto,
): {
  layout: UnitSourceLayout;
  spans: TranslationGroupSourceSpan[];
} {
  const layout = buildUnitSourceLayout(unit);
  const candidates: TranslationGroupSourceSpanCandidate[] = [];

  for (const child of unit.children) {
    if (!isTranslationGroupNode(child) || child.target_scope !== "unit") {
      continue;
    }

    const coveredAnchorSegmentIds = child.covered_anchor_segment_ids;
    if (coveredAnchorSegmentIds.length === 0) {
      continue;
    }

    if (
      new Set(coveredAnchorSegmentIds).size !== coveredAnchorSegmentIds.length
    ) {
      continue;
    }

    const startAnchorId = coveredAnchorSegmentIds[0];
    const endAnchorId = coveredAnchorSegmentIds[coveredAnchorSegmentIds.length - 1];
    if (!startAnchorId || !endAnchorId) {
      continue;
    }
    const startAnchorIndex = layout.anchorOrderIndexById.get(startAnchorId);
    const startChildIndex = layout.anchorChildIndexById.get(startAnchorId);
    const endChildIndex = layout.anchorChildIndexById.get(endAnchorId);

    if (
      startAnchorIndex === undefined ||
      startChildIndex === undefined ||
      endChildIndex === undefined ||
      endChildIndex < startChildIndex
    ) {
      continue;
    }

    const coveredSegments = layout.orderedAnchorSegments.slice(
      startAnchorIndex,
      startAnchorIndex + coveredAnchorSegmentIds.length,
    );
    if (coveredSegments.length !== coveredAnchorSegmentIds.length) {
      continue;
    }

    if (
      coveredSegments.some(
        (segment, index) =>
          segment.anchor_segment_id !== coveredAnchorSegmentIds[index],
      )
    ) {
      continue;
    }

    const endAnchorIndex =
      startAnchorIndex + coveredAnchorSegmentIds.length - 1;

    candidates.push({
      group: child,
      coveredSegments,
      startAnchorIndex,
      endAnchorIndex,
      sourceChildren: layout.sourceChildren.slice(
        startChildIndex,
        endChildIndex + 1,
      ),
    });
  }

  candidates.sort((left, right) => {
    if (left.startAnchorIndex !== right.startAnchorIndex) {
      return left.startAnchorIndex - right.startAnchorIndex;
    }
    if (left.endAnchorIndex !== right.endAnchorIndex) {
      return left.endAnchorIndex - right.endAnchorIndex;
    }
    return left.group.group_id.localeCompare(right.group.group_id);
  });

  const spans: TranslationGroupSourceSpan[] = [];
  let lastAcceptedEndAnchorIndex = -1;
  for (const candidate of candidates) {
    if (candidate.startAnchorIndex <= lastAcceptedEndAnchorIndex) {
      continue;
    }

    spans.push(candidate);
    lastAcceptedEndAnchorIndex = candidate.endAnchorIndex;
  }

  return {
    layout,
    spans,
  };
}

function mapUnitToBlocks(
  unit: ReaderUnitNodeDto,
  context: UnitProjectionContext,
): ReaderRecordPlateBlock[] {
  const blocks: ReaderRecordPlateBlock[] = [];
  let isFirstAnchorSegmentInUnit = true;
  const { layout, spans } = buildValidTranslationGroupSpans(unit);
  let nextAnchorIndex = 0;

  // B2.3: When the unit's source block carries a recognized
  // `stableBlockType`, project the unit to the corresponding Markdown
  // block type (heading / code_block / blockquote / hr / list_item /
  // table_cell) instead of the legacy paragraph. Translation blockquotes
  // and annotation blocks (callouts / sentence analysis / supplements)
  // remain unchanged so enhancement layers still attach correctly.
  const stableBlockType = getUnitStableBlockType(unit);
  const sourceBlock = stableBlockType ? findUnitSourceBlock(unit) : null;
  const useStableProjection = stableBlockType !== null && sourceBlock !== null;
  // Supplement eligibility is derived from the same single stableBlockType
  // signal: code blocks are fail-closed for Ask supplement cards, every
  // other block kind follows the shared wrapper policy. Legacy units without
  // a stable type keep the historical eligible default.
  const supplementEligible =
    !useStableProjection || stableBlockType !== "code_block";

  const sourceChildrenForAnchorRange = (
    startAnchorIndex: number,
    endAnchorIndex: number,
  ): ReaderSourceBlockChildNodeDto[] => {
    const firstSegment = layout.orderedAnchorSegments[startAnchorIndex];
    const lastSegment = layout.orderedAnchorSegments[endAnchorIndex];
    if (!firstSegment || !lastSegment) {
      return [];
    }

    const firstChildIndex = layout.anchorChildIndexById.get(
      firstSegment.anchor_segment_id,
    );
    const lastChildIndex = layout.anchorChildIndexById.get(
      lastSegment.anchor_segment_id,
    );
    if (
      firstChildIndex === undefined ||
      lastChildIndex === undefined ||
      lastChildIndex < firstChildIndex
    ) {
      return [];
    }
    return layout.sourceChildren.slice(firstChildIndex, lastChildIndex + 1);
  };

  const pushFallbackRange = (
    startAnchorIndex: number,
    endAnchorIndex: number,
  ) => {
    if (startAnchorIndex > endAnchorIndex) {
      return;
    }

    const segments = layout.orderedAnchorSegments.slice(
      startAnchorIndex,
      endAnchorIndex + 1,
    );
    if (segments.length === 0) {
      return;
    }

    if (useStableProjection && sourceBlock) {
      const sourceChildren = sourceChildrenForAnchorRange(
        startAnchorIndex,
        endAnchorIndex,
      );
      blocks.push(
        buildStableBlockForSourceSpan(
          sourceChildren.length > 0 ? sourceChildren : segments,
          segments,
          context,
          unit,
          sourceBlock,
          { isUnitStart: isFirstAnchorSegmentInUnit },
        ),
      );
      isFirstAnchorSegmentInUnit = false;
      blocks.push(
        ...buildAnnotationBlocksForSegments(segments, context, {
          supplementEligible,
        }),
      );
      return;
    }

    for (const segment of segments) {
      blocks.push(
        buildParagraphBlock(segment, context, {
          isUnitStart: isFirstAnchorSegmentInUnit,
        }),
      );
      isFirstAnchorSegmentInUnit = false;
      blocks.push(...buildAnnotationBlocksForSegments([segment], context));
    }
  };

  for (const span of spans) {
    pushFallbackRange(nextAnchorIndex, span.startAnchorIndex - 1);

    blocks.push(
      useStableProjection && sourceBlock
        ? buildStableBlockForSourceSpan(
            span.sourceChildren,
            span.coveredSegments,
            context,
            unit,
            sourceBlock,
            {
              isUnitStart: isFirstAnchorSegmentInUnit,
            },
          )
        : buildParagraphBlockForSourceSpan(
            span.sourceChildren,
            span.coveredSegments,
            context,
            {
              isUnitStart: isFirstAnchorSegmentInUnit,
            },
          ),
    );
    isFirstAnchorSegmentInUnit = false;

    const blockquote = buildBlockquoteBlock(span.group);
    if (blockquote) {
      blocks.push(blockquote);
    }
    blocks.push(
      ...buildAnnotationBlocksForSegments(span.coveredSegments, context, {
        supplementEligible,
      }),
    );
    nextAnchorIndex = span.endAnchorIndex + 1;
  }

  pushFallbackRange(
    nextAnchorIndex,
    layout.orderedAnchorSegments.length - 1,
  );

  return blocks;
}

// ---------------------------------------------------------------------------
// B2.6: Reconstruct wrapper blocks (`list` / `table` / `table_row`) from
// leaf blocks (`list_item` / `table_cell`).
//
// The backend `StableBlockAnnotation` is only created for leaf blocks that
// have canonical-text offsets (heading / list_item / code_block / blockquote
// / thematic_break / table_cell). Wrapper blocks (list / table / table_row)
// do not carry canonical text — they exist only as `parent_block_id`
// references on their children. This means the frontend receives a flat
// sequence of leaf blocks, each carrying `parentStableBlockId`, and must
// reconstruct the nesting.
//
// Strategy: post-process the flat `children` array from `mapUnitToBlocks`.
// Group `list_item` blocks by `parentStableBlockId` into `list` wrappers.
// Group `table_cell` blocks into rows (by `parentStableBlockId`) and then
// into a single `table` wrapper per contiguous run. Overlay blocks between
// leaves of the same wrapper are deferred past the wrapper instead of
// breaking the run (see `isWrapperOverlayBlock`); any other block closes it.
// ---------------------------------------------------------------------------

/**
 * Group consecutive `list_item` blocks into `list` wrappers by
 * `parentStableBlockId`. Items with the same parent form one list; items
 * with `null` parent each form a single-item fallback list.
 */
function groupListItemsIntoLists(
  items: ReaderRecordPlateListItemBlock[],
): ReaderRecordPlateListBlock[] {
  const lists: ReaderRecordPlateListBlock[] = [];
  let fallbackCounter = 0;

  let index = 0;
  while (index < items.length) {
    const item = items[index];
    const parentId = item.data.parentStableBlockId ?? null;

    // Collect consecutive items with the same parentStableBlockId.
    const group: ReaderRecordPlateListItemBlock[] = [item];
    let next = index + 1;
    while (
      next < items.length &&
      (items[next].data.parentStableBlockId ?? null) === parentId
    ) {
      group.push(items[next]);
      next += 1;
    }

    const listId =
      parentId !== null
        ? `list:${parentId}`
        : `list:fallback:${fallbackCounter++}`;

    // Clone data from the first item; override stableBlockType to "list".
    const firstData = group[0].data;
    const listData: ReaderRecordPlateStableBlockData = {
      ...firstData,
      stableBlockType: "list",
      // The list wrapper's parentStableBlockId is not meaningful (the
      // backend `list` block has no parent in the Markdown model). Clear
      // it so downstream consumers don't misinterpret a stale value.
      parentStableBlockId: null,
      // isUnitStart is only meaningful for the first child; the wrapper
      // itself inherits it so navigation attrs attach correctly.
      isUnitStart: firstData.isUnitStart,
    };

    lists.push({
      type: "list",
      id: listId,
      // Default to unordered; the backend does not currently project
      // list ordering. B2.6 leaves this as `false` — a future task can
      // enrich the backend DTO to carry `ordered` from the `list`
      // block's `payload_json`.
      ordered: false,
      children: group,
      data: listData,
    });

    index = next;
  }

  return lists;
}

/**
 * Group consecutive `table_cell` blocks into `table_row` blocks (by
 * `parentStableBlockId`) and then into a single `table` wrapper per
 * contiguous run.
 */
function groupTableCellsIntoTable(
  cells: ReaderRecordPlateTableCellBlock[],
  tableCounter: { value: number },
): ReaderRecordPlateTableBlock[] {
  if (cells.length === 0) {
    return [];
  }

  // Phase 1: group cells into rows by parentStableBlockId (row id).
  const rows: ReaderRecordPlateTableRowBlock[] = [];
  let fallbackRowCounter = 0;

  let index = 0;
  while (index < cells.length) {
    const cell = cells[index];
    const rowId = cell.data.parentStableBlockId ?? null;

    const rowCells: ReaderRecordPlateTableCellBlock[] = [cell];
    let next = index + 1;
    while (
      next < cells.length &&
      (cells[next].data.parentStableBlockId ?? null) === rowId
    ) {
      rowCells.push(cells[next]);
      next += 1;
    }

    const tableRowId =
      rowId !== null
        ? `table_row:${rowId}`
        : `table_row:fallback:${fallbackRowCounter++}`;

    const firstCellData = rowCells[0].data;
    const rowData: ReaderRecordPlateStableBlockData & {
      isHeader?: boolean;
      rowIndex?: number;
    } = {
      ...firstCellData,
      stableBlockType: "table_row",
      parentStableBlockId: null,
      isUnitStart: firstCellData.isUnitStart,
      // rowIndex is assigned after we know how many rows exist.
      // L1: 行级 isHeader 由单元格 DTO `tableIsHeader` 推导 ——
      // 整行所有单元格都是 header 才是表头行（后端 table_row
      // wrapper source block 不进入 Plate 投影，无法直接消费其
      // `tableIsHeader`）。
      isHeader: rowCells.every((cell) => cell.data.isHeader === true),
    };

    // Assign per-cell columnIndex based on position in the row.
    const cellsWithColumnIndex = rowCells.map((cell, columnIndex) => ({
      ...cell,
      data: {
        ...cell.data,
        columnIndex,
      },
    }));

    rows.push({
      type: "table_row",
      id: tableRowId,
      children: cellsWithColumnIndex,
      data: { ...rowData, rowIndex: rows.length },
    });

    index = next;
  }

  // Phase 2: wrap all rows into a single table block.
  const firstRowData = rows[0].data;
  const tableId = `table:fallback:${tableCounter.value++}`;

  // L1: table wrapper 不产生 reading unit，表级元数据由 cell DTO 推导：
  // - alignments：首行各列单元格的 alignment（按列序）。
  // - headerRows：前导 isHeader 行计数。
  const alignments = rows[0].children.map(
    (cell) => cell.data.alignment ?? "default",
  );
  let headerRows = 0;
  while (headerRows < rows.length && rows[headerRows].data.isHeader === true) {
    headerRows += 1;
  }

  const tableData: ReaderRecordPlateTableBlock["data"] = {
    ...firstRowData,
    stableBlockType: "table",
    parentStableBlockId: null,
    isUnitStart: firstRowData.isUnitStart,
    alignments,
    headerRows,
  };

  return [
    {
      type: "table",
      id: tableId,
      children: rows,
      data: tableData,
    },
  ];
}

/**
 * Post-process the flat block array to reconstruct `list` / `table` /
 * `table_row` wrapper blocks from `list_item` / `table_cell` leaf blocks.
 *
 * Overlay blocks (translations / callouts / sentence analyses) between leaf
 * blocks of the same wrapper no longer break the run: items keep grouping
 * by `parentStableBlockId` across overlays, and the skipped overlays are
 * re-emitted right after the wrapper in their original order. A block that
 * is neither an overlay nor a matching leaf closes the run, so unrelated
 * content never moves.
 */
function groupStableWrapperBlocks(
  blocks: ReaderRecordPlateBlock[],
): ReaderRecordPlateBlock[] {
  const result: ReaderRecordPlateBlock[] = [];
  const tableCounter = { value: 0 };

  let index = 0;
  while (index < blocks.length) {
    const block = blocks[index];

    if (block.type === "list_item") {
      const parentId =
        (block as ReaderRecordPlateListItemBlock).data.parentStableBlockId ??
        null;
      const run: ReaderRecordPlateListItemBlock[] = [
        block as ReaderRecordPlateListItemBlock,
      ];
      const deferred: ReaderRecordPlateBlock[] = [];
      index += 1;
      while (index < blocks.length) {
        const candidate = blocks[index];
        if (isWrapperOverlayBlock(candidate)) {
          deferred.push(candidate);
          index += 1;
          continue;
        }
        if (
          candidate.type === "list_item" &&
          ((candidate as ReaderRecordPlateListItemBlock).data
            .parentStableBlockId ?? null) === parentId
        ) {
          run.push(candidate as ReaderRecordPlateListItemBlock);
          index += 1;
          continue;
        }
        break;
      }
      result.push(...groupListItemsIntoLists(run));
      result.push(...deferred);
    } else if (block.type === "table_cell") {
      const run: ReaderRecordPlateTableCellBlock[] = [
        block as ReaderRecordPlateTableCellBlock,
      ];
      const deferred: ReaderRecordPlateBlock[] = [];
      index += 1;
      while (index < blocks.length) {
        const candidate = blocks[index];
        if (isWrapperOverlayBlock(candidate)) {
          deferred.push(candidate);
          index += 1;
          continue;
        }
        if (candidate.type === "table_cell") {
          run.push(candidate as ReaderRecordPlateTableCellBlock);
          index += 1;
          continue;
        }
        break;
      }
      result.push(...groupTableCellsIntoTable(run, tableCounter));
      result.push(...deferred);
    } else {
      result.push(block);
      index += 1;
    }
  }

  return result;
}

/**
 * Compose the server-owned Stable Document tree with the flat block array.
 *
 * Order authority: the `mapUnitToBlocks` flat output is the only
 * anchor-level ordering. Every source span stays at its own flat position
 * and every overlay stays interleaved unless a wrapper policy defers it.
 * The tree contributes structure only: list/table row-cell membership,
 * nesting (nested lists, quote children), and wrapper payload (ordered,
 * alignments, header rows, display icon). No global re-bucketing by
 * stableBlockId and no reordering of unrelated blocks.
 *
 * Controlled overlay postposition (wrapper policy):
 * - list: overlays between items move after the whole list in anchor order;
 *   items group across overlays; nested lists and `ordered` come from the
 *   persisted tree.
 * - table: overlays (Ask supplements) move after the whole table; anchors
 *   stay in their cells.
 * - blockquote / source_callout: source structure stays inside the wrapper;
 *   translation overlays move out as siblings right after it.
 *
 * Legacy snapshots without `stable_document_tree` use
 * `groupStableWrapperBlocks` above with the same deferral policy.
 */
function projectStableDocumentTree(
  nodes: ReaderStableDocumentBlockNodeDto[],
  flatBlocks: ReaderRecordPlateBlock[],
): ReaderRecordPlateBlock[] {
  // ------------------------------------------------------------------
  // Tree indexes — structure membership only, never order.
  // ------------------------------------------------------------------
  const listNodeByItemId = new Map<
    string,
    ReaderStableDocumentBlockNodeDto
  >();
  const parentItemIdByListId = new Map<string, string>();
  const tableByCellId = new Map<
    string,
    {
      table: ReaderStableDocumentBlockNodeDto;
      row: ReaderStableDocumentBlockNodeDto;
    }
  >();
  const quoteByLeafId = new Map<string, ReaderStableDocumentBlockNodeDto>();
  const orderById = new Map<string, number>();

  const indexOrder = (node: ReaderStableDocumentBlockNodeDto): void => {
    orderById.set(node.block_id, node.order_index);
    node.children.forEach(indexOrder);
  };
  nodes.forEach(indexOrder);

  const collectDescendantIds = (
    node: ReaderStableDocumentBlockNodeDto,
  ): string[] => [
    ...node.children.map((child) => child.block_id),
    ...node.children.flatMap(collectDescendantIds),
  ];

  const indexTreeNode = (
    node: ReaderStableDocumentBlockNodeDto,
    parentItemId: string | null,
  ): void => {
    if (node.block_type === "list") {
      if (parentItemId !== null) {
        parentItemIdByListId.set(node.block_id, parentItemId);
      }
      for (const child of node.children) {
        if (child.block_type !== "list_item") continue;
        listNodeByItemId.set(child.block_id, node);
        for (const grandchild of child.children) {
          if (grandchild.block_type === "list") {
            indexTreeNode(grandchild, child.block_id);
          }
        }
      }
      return;
    }
    if (node.block_type === "table") {
      for (const row of node.children.filter(
        (child) => child.block_type === "table_row",
      )) {
        for (const cell of row.children.filter(
          (child) => child.block_type === "table_cell",
        )) {
          tableByCellId.set(cell.block_id, { table: node, row });
        }
      }
      return;
    }
    // Recurse first so an inner quote wins over an outer one.
    for (const child of node.children) {
      indexTreeNode(child, null);
    }
    if (node.block_type === "blockquote") {
      for (const leafId of collectDescendantIds(node)) {
        if (!quoteByLeafId.has(leafId)) {
          quoteByLeafId.set(leafId, node);
        }
      }
    }
  };
  for (const node of nodes) {
    indexTreeNode(node, null);
  }

  // ------------------------------------------------------------------
  // Span pool — flat blocks keyed by stableBlockId, in flat order.
  // ------------------------------------------------------------------
  const spanPool = new Map<string, ReaderRecordPlateBlock[]>();
  for (const block of flatBlocks) {
    const stableBlockId = getStableBlockId(block);
    if (stableBlockId === null) continue;
    const pooled = spanPool.get(stableBlockId) ?? [];
    pooled.push(block);
    spanPool.set(stableBlockId, pooled);
  }
  const takeSpans = (stableBlockId: string): ReaderRecordPlateBlock[] => {
    const spans = spanPool.get(stableBlockId) ?? [];
    spanPool.delete(stableBlockId);
    return spans;
  };

  const stableNodeData = (
    node: ReaderStableDocumentBlockNodeDto,
    stableBlockType = node.block_type,
  ): ReaderRecordPlateStableBlockData => ({
    unitId: null,
    stableBlockType,
    stableBlockId: node.block_id,
    parentStableBlockId: node.parent_block_id,
    coveredAnchorSegmentIds: node.anchor_segment_ids,
  });
  const composedListIds = new Set<string>();
  const composedTableIds = new Set<string>();
  const composedImageIds = new Set<string>();
  const composedSourceCalloutIds = new Set<string>();

  const composeImage = (
    node: ReaderStableDocumentBlockNodeDto,
  ): ReaderRecordPlateImageBlock | null => {
    if (composedImageIds.has(node.block_id)) return null;
    composedImageIds.add(node.block_id);
    return makeStandaloneImageBlock(node);
  };

  const composeList = (
    listNode: ReaderStableDocumentBlockNodeDto,
  ): ReaderRecordPlateListBlock | null => {
    if (composedListIds.has(listNode.block_id)) return null;
    const listChildren: Array<
      ReaderRecordPlateListItemBlock | ReaderRecordPlateImageBlock
    > = [];
    for (const child of listNode.children) {
      if (child.block_type === "image") {
        const image = composeImage(child);
        if (image) listChildren.push(image);
        continue;
      }
      if (child.block_type !== "list_item") continue;
      const spans = takeSpans(child.block_id).filter(isListItemBlock);
      if (spans.length === 0) continue;
      const first = spans[0];
      const merged: ReaderRecordPlateListItemBlock =
        spans.length === 1
          ? first
          : {
              ...first,
              children: spans.flatMap((item) => item.children),
              data: {
                ...first.data,
                coveredAnchorSegmentIds: spans.flatMap(
                  (item) => item.data.coveredAnchorSegmentIds,
                ),
              },
            };
      const nestedChildren = child.children
        .filter((grandchild) => grandchild.block_type === "list")
        .map(composeList)
        .filter((list): list is ReaderRecordPlateListBlock => list !== null);
      listChildren.push(
        nestedChildren.length > 0 ? { ...merged, nestedChildren } : merged,
      );
    }
    if (listChildren.length === 0) return null;
    composedListIds.add(listNode.block_id);
    const firstItem = listChildren.find(isListItemBlock);
    return {
      type: "list",
      id: `list:${listNode.block_id}`,
      ordered: listNode.payload["ordered"] === true,
      children: listChildren,
      data: {
        ...(firstItem?.data ?? stableNodeData(listNode, "list")),
        stableBlockType: "list",
        stableBlockId: listNode.block_id,
        parentStableBlockId: listNode.parent_block_id,
      },
    };
  };

  const composeTable = (
    tableNode: ReaderStableDocumentBlockNodeDto,
  ): ReaderRecordPlateTableBlock | null => {
    if (composedTableIds.has(tableNode.block_id)) return null;
    const rows: ReaderRecordPlateTableRowBlock[] = [];
    for (const rowNode of tableNode.children.filter(
      (child) => child.block_type === "table_row",
    )) {
      const cells: Array<
        ReaderRecordPlateTableCellBlock<ReaderRecordPlateInlineNode>
      > = [];
      for (const cellNode of rowNode.children.filter(
        (child) => child.block_type === "table_cell",
      )) {
        const spans = takeSpans(cellNode.block_id).filter(isTableCellBlock);
        const payload = cellNode.payload;
        const alignment =
          payload["alignment"] === "left" ||
          payload["alignment"] === "center" ||
          payload["alignment"] === "right" ||
          payload["alignment"] === "default"
            ? payload["alignment"]
            : undefined;
        const isHeader =
          typeof payload["is_header"] === "boolean"
            ? payload["is_header"]
            : undefined;
        if (spans.length > 0) {
          const first = spans[0];
          const merged: ReaderRecordPlateTableCellBlock =
            spans.length === 1
              ? first
              : {
                  ...first,
                  children: spans.flatMap((cell) => cell.children),
                  data: {
                    ...first.data,
                    coveredAnchorSegmentIds: spans.flatMap(
                      (cell) => cell.data.coveredAnchorSegmentIds,
                    ),
                  },
                };
          cells.push({
            ...merged,
            data: {
              ...merged.data,
              columnIndex: cells.length,
              alignment: alignment ?? merged.data.alignment,
              isHeader: isHeader ?? merged.data.isHeader,
            },
          });
          continue;
        }

        cells.push({
          type: "table_cell",
          id: `table_cell:${cellNode.block_id}`,
          children: [],
          data: {
            ...stableNodeData(cellNode, "table_cell"),
            columnIndex: cells.length,
            alignment: alignment ?? "default",
            isHeader: isHeader ?? false,
          },
        });
      }
      if (cells.length === 0) continue;
      const firstCellData = cells[0].data;
      rows.push({
        type: "table_row",
        id: `table_row:${rowNode.block_id}`,
        children: cells,
        data: {
          ...firstCellData,
          stableBlockType: "table_row",
          stableBlockId: rowNode.block_id,
          parentStableBlockId: tableNode.block_id,
          isHeader:
            typeof rowNode.payload["is_header"] === "boolean"
              ? rowNode.payload["is_header"]
              : cells.every((cell) => cell.data.isHeader === true),
          rowIndex:
            typeof rowNode.payload["row_index"] === "number"
              ? rowNode.payload["row_index"]
              : rows.length,
        },
      });
    }
    if (rows.length === 0) return null;
    composedTableIds.add(tableNode.block_id);

    const firstRow = rows[0];
    const payload = tableNode.payload;
    const alignments = firstRow.children.map(
      (cell) => cell.data.alignment ?? "default",
    );
    let headerRows = 0;
    while (headerRows < rows.length && rows[headerRows].data.isHeader === true) {
      headerRows += 1;
    }
    return {
      type: "table",
      id: `table:${tableNode.block_id}`,
      children: rows,
      data: {
        ...firstRow.data,
        stableBlockType: "table",
        stableBlockId: tableNode.block_id,
        parentStableBlockId: tableNode.parent_block_id,
        alignments:
          Array.isArray(payload["alignments"]) &&
          payload["alignments"].every((value) => typeof value === "string")
            ? (payload["alignments"] as string[])
            : alignments,
        headerRows:
          typeof payload["header_rows"] === "number"
            ? payload["header_rows"]
            : headerRows,
      },
    };
  };

  const composeSourceCallout = (
    quoteNode: ReaderStableDocumentBlockNodeDto,
  ): ReaderRecordPlateBlock[] => {
    if (composedSourceCalloutIds.has(quoteNode.block_id)) return [];
    composedSourceCalloutIds.add(quoteNode.block_id);
    const childBlocks = quoteNode.children.flatMap(composeNodeContent);
    if (childBlocks.length === 0) {
      // The callout itself is one leaf unit (no structured children).
      return takeSpans(quoteNode.block_id).filter(
        (block): block is ReaderRecordPlateSourceCalloutBlock =>
          block.type === "source_callout",
      );
    }
    const rawDisplayIcon = quoteNode.payload["display_icon"];
    const displayIcon =
      typeof rawDisplayIcon === "string" && isSafeCalloutEmoji(rawDisplayIcon)
        ? rawDisplayIcon
        : null;
    const firstChild = childBlocks[0];
    if (!firstChild) return [];
    const firstData = firstChild.data as ReaderRecordPlateStableBlockData;
    return [
      {
        type: "source_callout",
        id: `source_callout:${quoteNode.block_id}`,
        children: childBlocks,
        data: {
          ...firstData,
          stableBlockType: "source_callout",
          stableBlockId: quoteNode.block_id,
          parentStableBlockId: quoteNode.parent_block_id,
          calloutIcon: displayIcon,
        },
      },
    ];
  };

  // Quote inner content: source structure only, in flat order.
  function composeNodeContent(
    node: ReaderStableDocumentBlockNodeDto,
  ): ReaderRecordPlateBlock[] {
    if (node.block_type === "image") {
      const image = composeImage(node);
      return image ? [image] : [];
    }
    if (node.block_type === "list") {
      const list = composeList(node);
      return list ? [list] : [];
    }
    if (node.block_type === "table") {
      const table = composeTable(node);
      return table ? [table] : [];
    }
    if (
      node.block_type === "blockquote" &&
      node.content_role === "source_callout"
    ) {
      return composeSourceCallout(node);
    }
    const ownSpans = takeSpans(node.block_id);
    if (ownSpans.length > 0) return ownSpans;
    return node.children.flatMap(composeNodeContent);
  }

  // ------------------------------------------------------------------
  // Flat-driven scan. Wrappers replace the position of their first leaf;
  // deferred overlays flush right after their wrapper. Everything else
  // keeps its exact flat position.
  // ------------------------------------------------------------------
  const result: ReaderRecordPlateBlock[] = [];
  let index = 0;
  while (index < flatBlocks.length) {
    const block = flatBlocks[index];
    const stableBlockId = getStableBlockId(block);

    if (stableBlockId === null) {
      result.push(block);
      index += 1;
      continue;
    }
    if (!spanPool.has(stableBlockId)) {
      // Already composed into a wrapper emitted earlier.
      index += 1;
      continue;
    }

    const quoteNode = quoteByLeafId.get(stableBlockId);
    if (quoteNode) {
      const deferred: ReaderRecordPlateBlock[] = [];
      let end = index + 1;
      while (end < flatBlocks.length) {
        const candidate = flatBlocks[end];
        const candidateId = getStableBlockId(candidate);
        if (
          candidateId !== null &&
          spanPool.has(candidateId) &&
          quoteByLeafId.get(candidateId) === quoteNode
        ) {
          end += 1;
          continue;
        }
        if (candidateId === null && isWrapperOverlayBlock(candidate)) {
          deferred.push(candidate);
          end += 1;
          continue;
        }
        break;
      }
      if (quoteNode.content_role === "source_callout") {
        result.push(...composeSourceCallout(quoteNode));
      } else {
        result.push(...quoteNode.children.flatMap(composeNodeContent));
      }
      result.push(...deferred);
      index = end;
      continue;
    }

    const listNode = listNodeByItemId.get(stableBlockId);
    if (listNode) {
      const deferred: ReaderRecordPlateBlock[] = [];
      const runItemIds = new Set<string>([stableBlockId]);
      let end = index + 1;
      while (end < flatBlocks.length) {
        const candidate = flatBlocks[end];
        const candidateId = getStableBlockId(candidate);
        if (candidateId !== null && spanPool.has(candidateId)) {
          const candidateList = listNodeByItemId.get(candidateId);
          const belongsToRun =
            candidateList === listNode ||
            (candidateList !== undefined &&
              runItemIds.has(
                parentItemIdByListId.get(candidateList.block_id) ?? "",
              ));
          if (belongsToRun) {
            runItemIds.add(candidateId);
            end += 1;
            continue;
          }
          break;
        }
        if (candidateId === null && isWrapperOverlayBlock(candidate)) {
          deferred.push(candidate);
          end += 1;
          continue;
        }
        break;
      }
      const list = composeList(listNode);
      if (list) {
        result.push(list);
      }
      result.push(...deferred);
      index = end;
      continue;
    }

    const tableInfo = tableByCellId.get(stableBlockId);
    if (tableInfo) {
      const deferred: ReaderRecordPlateBlock[] = [];
      let end = index + 1;
      while (end < flatBlocks.length) {
        const candidate = flatBlocks[end];
        const candidateId = getStableBlockId(candidate);
        if (
          candidateId !== null &&
          spanPool.has(candidateId) &&
          tableByCellId.get(candidateId)?.table === tableInfo.table
        ) {
          end += 1;
          continue;
        }
        if (candidateId === null && isWrapperOverlayBlock(candidate)) {
          deferred.push(candidate);
          end += 1;
          continue;
        }
        break;
      }
      const table = composeTable(tableInfo.table);
      if (table) {
        result.push(table);
      }
      result.push(...deferred);
      index = end;
      continue;
    }

    // Plain leaf: emit exactly this span; later spans of the same
    // stableBlockId keep their own flat positions.
    const pooledSpans = spanPool.get(stableBlockId) ?? [];
    const [firstSpan, ...restSpans] = pooledSpans;
    if (firstSpan) {
      result.push(firstSpan);
    }
    if (restSpans.length > 0) {
      spanPool.set(stableBlockId, restSpans);
    } else {
      spanPool.delete(stableBlockId);
    }
    index += 1;
  }

  const remainingRootBlocks = nodes.flatMap(composeNodeContent);
  return mergeRootBlocks(result, remainingRootBlocks, orderById);
}

function getStableBlockId(block: ReaderRecordPlateBlock): string | null {
  const data = block.data as Partial<ReaderRecordPlateStableBlockData>;
  const value = data.stableBlockId;
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * Overlay blocks carry no stableBlockId and may be deferred past a wrapper
 * (translation blockquotes, callouts, sentence analyses). Source blocks —
 * including legacy paragraphs without a stable id — are never overlays.
 */
function isWrapperOverlayBlock(block: ReaderRecordPlateBlock): boolean {
  return (
    block.type === "blockquote" ||
    block.type === "callout" ||
    block.type === "sentence_analysis"
  );
}

function isListItemBlock(
  block: ReaderRecordPlateBlock,
): block is ReaderRecordPlateListItemBlock {
  return block.type === "list_item";
}

function isTableCellBlock(
  block: ReaderRecordPlateBlock,
): block is ReaderRecordPlateTableCellBlock {
  return block.type === "table_cell";
}

function isTextLeaf(node: unknown): node is ReaderRecordPlateTextLeaf {
  return (
    !!node &&
    typeof (node as { text?: unknown }).text === "string" &&
    (node as { type?: unknown }).type !== "image"
  );
}

function makeStandaloneImageBlock(
  node: ReaderStableDocumentBlockNodeDto,
): ReaderRecordPlateImageBlock {
  const p = node.payload as Record<string, unknown>;
  const sourceUrl = typeof p.source_url === "string" ? (p.source_url as string) : "";
  const effectiveRaw = p.effective_url;
  const effectiveUrl =
    typeof effectiveRaw === "string" ? (effectiveRaw as string) : effectiveRaw === null ? null : null;
  const altText = typeof p.alt_text === "string" ? (p.alt_text as string) : "";
  const titleRaw = p.title;
  const title = typeof titleRaw === "string" ? (titleRaw as string) : titleRaw === null ? null : null;
  const overrideRaw = p.override_url;
  const overrideUrl = typeof overrideRaw === "string" ? (overrideRaw as string) : undefined;
  return {
    type: "image",
    id: `image:${node.block_id}`,
    children: [{ text: "" }],
    data: {
      sourceUrl,
      effectiveUrl: effectiveUrl as string | null,
      altText,
      title,
      positionKind: "standalone",
      stableBlockId: node.block_id,
      parentStableBlockId: node.parent_block_id,
      ...(overrideUrl !== undefined ? { overrideUrl } : {}),
    },
  };
}

function makeInlineImageBlock(
  entry: Record<string, unknown>,
  owningId: string,
  parentStableBlockId: string | null,
  ordinal: number,
): ReaderRecordPlateImageBlock | null {
  const sourceUrl = entry.source_url;
  const altText = entry.alt_text;
  const before = entry.before_utf16;
  if (typeof sourceUrl !== "string") return null;
  if (typeof altText !== "string") return null;
  // title may be string|null
  const titleRaw = entry.title;
  const title = typeof titleRaw === "string" ? titleRaw : titleRaw === null ? null : null;
  const effectiveRaw = entry.effective_url;
  const effectiveUrl = typeof effectiveRaw === "string" ? effectiveRaw : effectiveRaw === null ? null : null;
  // before is validated outside (integer and bounds), but also guard here
  if (typeof before !== "number" || !Number.isInteger(before)) return null;
  const overrideRaw = entry.override_url;
  const overrideUrl = typeof overrideRaw === "string" ? (overrideRaw as string) : undefined;
  return {
    type: "image",
    id: `image:${owningId}:${ordinal}`,
    children: [{ text: "" }],
    data: {
      sourceUrl,
      effectiveUrl: effectiveUrl as string | null,
      altText,
      title,
      positionKind: "inline",
      stableBlockId: owningId,
      parentStableBlockId,
      inlineOrdinal: ordinal,
      beforeUtf16: before as number,
      ...(overrideUrl !== undefined ? { overrideUrl } : {}),
    },
  };
}

function sliceLeafForImage(
  leaf: ReaderRecordPlateTextLeaf,
  newText: string,
  localStart: number,
  localEnd: number,
): ReaderRecordPlateTextLeaf {
  const oldSegStart = leaf.segmentRange?.startUtf16 ?? 0;
  const oldBaseStart = leaf.baseRange.startUtf16;
  const newSegStart = oldSegStart + localStart;
  const newSegEnd = oldSegStart + localEnd;
  const newSegRange = leaf.segmentRange
    ? { startUtf16: newSegStart, endUtf16: newSegEnd }
    : undefined;
  const newBaseRange = {
    startUtf16: oldBaseStart + localStart,
    endUtf16: oldBaseStart + localEnd,
  };
  return {
    ...leaf,
    text: newText,
    baseRange: newBaseRange,
    segmentRange: newSegRange,
    marks: leaf.marks.map((m) => ({
      ...m,
      startsHere: m.anchor.segmentStartOffset === newSegStart,
      endsHere: m.anchor.segmentEndOffset === newSegEnd,
    })),
  };
}

function splitLeavesAtOffsets(
  leaves: ReaderRecordPlateTextLeaf[],
  offsets: number[],
): ReaderRecordPlateTextLeaf[] {
  const uniq = [...new Set(offsets)].sort((a, b) => a - b);
  if (uniq.length === 0) return leaves;
  const out: ReaderRecordPlateTextLeaf[] = [];
  let cum = 0;
  for (const leaf of leaves) {
    const len = leaf.text.length;
    const start = cum;
    const end = cum + len;
    const inner = uniq.filter((o) => o > start && o < end).sort((a, b) => a - b);
    if (inner.length === 0) {
      out.push(leaf);
    } else {
      let lastPos = 0;
      for (const off of inner) {
        const pos = off - start;
        const pieceText = leaf.text.slice(lastPos, pos);
        out.push(sliceLeafForImage(leaf, pieceText, lastPos, pos));
        lastPos = pos;
      }
      const finalText = leaf.text.slice(lastPos);
      out.push(sliceLeafForImage(leaf, finalText, lastPos, len));
    }
    cum = end;
  }
  return out;
}

function enrichInlineImagesForBlock(
  block: ReaderRecordPlateBlock,
  placements: Array<{
    entry: Record<string, unknown>;
    ordinal: number;
    localBefore: number;
  }>,
  owningId: string,
  parentStableBlockId: string | null,
): void {
  if (
    block.type !== "paragraph" &&
    block.type !== "heading" &&
    block.type !== "list_item" &&
    block.type !== "markdown_blockquote" &&
    block.type !== "table_cell"
  ) {
    return;
  }
  const textLeaves = block.children.filter(isTextLeaf);
  const textLength = textLeaves.reduce((length, leaf) => length + leaf.text.length, 0);
  const valid = placements
    .filter(
      ({ entry, localBefore }) =>
        Number.isInteger(localBefore) &&
        localBefore >= 0 &&
        localBefore <= textLength &&
        typeof entry.source_url === "string" &&
        typeof entry.alt_text === "string",
    )
    .sort(
      (a, b) => a.localBefore - b.localBefore || a.ordinal - b.ordinal,
    );
  if (valid.length === 0) return;
  const offsets = valid.map(({ localBefore }) => localBefore);
  const splitLeaves = splitLeavesAtOffsets(textLeaves, offsets);
  const byOffset = new Map<number, ReaderRecordPlateImageBlock[]>();
  for (const v of valid) {
    const before = v.localBefore;
    const img = makeInlineImageBlock(v.entry, owningId, parentStableBlockId, v.ordinal);
    if (!img) continue;
    const arr = byOffset.get(before) ?? [];
    arr.push(img);
    byOffset.set(before, arr);
  }
  const sortedOffsets = [...byOffset.keys()].sort((a, b) => a - b);
  const leafOffsets: Array<{ leaf: ReaderRecordPlateTextLeaf; start: number; end: number }> = [];
  let cum2 = 0;
  for (const leaf of splitLeaves) {
    const len = leaf.text.length;
    leafOffsets.push({ leaf, start: cum2, end: cum2 + len });
    cum2 += len;
  }
  const finalChildren: Array<ReaderRecordPlateTextLeaf | ReaderRecordPlateImageBlock> = [];
  let leafIdx = 0;
  for (const off of sortedOffsets) {
    while (leafIdx < leafOffsets.length && leafOffsets[leafIdx].end <= off) {
      finalChildren.push(leafOffsets[leafIdx].leaf);
      leafIdx += 1;
    }
    const imgs = byOffset.get(off) ?? [];
    finalChildren.push(...imgs);
  }
  while (leafIdx < leafOffsets.length) {
    finalChildren.push(leafOffsets[leafIdx].leaf);
    leafIdx += 1;
  }
  if (splitLeaves.length === 0 && valid.length > 0) {
    block.children = sortedOffsets.flatMap((off) => byOffset.get(off) ?? []);
    return;
  }
  block.children = finalChildren;
}

function findBlocksByStableId(
  blocks: ReaderRecordPlateBlock[],
  stableId: string,
  out: ReaderRecordPlateBlock[],
) {
  for (const block of blocks) {
    const sid = getStableBlockId(block);
    if (sid === stableId) out.push(block);
    if (block.type === "list") {
      for (const child of block.children) {
        findBlocksByStableId([child], stableId, out);
        if (child.type === "list_item" && child.nestedChildren) {
          findBlocksByStableId(child.nestedChildren, stableId, out);
        }
      }
    } else if (block.type === "table") {
      for (const row of block.children) {
        for (const cell of row.children) findBlocksByStableId([cell], stableId, out);
      }
    } else if (block.type === "source_callout") {
      findBlocksByStableId(block.children as ReaderRecordPlateBlock[], stableId, out);
    }
  }
}

function mergeRootBlocks(
  rootChildren: ReaderRecordPlateBlock[],
  insertedBlocks: ReaderRecordPlateBlock[],
  orderById: Map<string, number>,
): ReaderRecordPlateBlock[] {
  type Group = { order: number; blocks: ReaderRecordPlateBlock[] };
  const groups: Group[] = [];
  let current: Group | null = null;
  for (const block of rootChildren) {
    const isOverlay = isWrapperOverlayBlock(block);
    if (isOverlay && current) {
      current.blocks.push(block);
    } else {
      const sid = getStableBlockId(block);
      const order = sid ? (orderById.get(sid) ?? groups.length * 1000) : groups.length * 1000;
      current = { order, blocks: [block] };
      groups.push(current);
    }
  }
  for (const block of insertedBlocks) {
    const stableBlockId = getStableBlockId(block);
    const order = stableBlockId ? (orderById.get(stableBlockId) ?? 1e9) : 1e9;
    groups.push({ order, blocks: [block] });
  }
  groups.sort((a, b) => a.order - b.order);
  return groups.flatMap((g) => g.blocks);
}

function injectImages(
  children: ReaderRecordPlateBlock[],
  tree: ReaderStableDocumentBlockNodeDto[],
): ReaderRecordPlateBlock[] {
  const inlineById = new Map<string, unknown[]>();
  const nodeById = new Map<string, ReaderStableDocumentBlockNodeDto>();
  const walkCollect = (list: ReaderStableDocumentBlockNodeDto[]) => {
    for (const n of list) {
      nodeById.set(n.block_id, n);
      const inline = n.payload["inline_images"];
      if (Array.isArray(inline)) inlineById.set(n.block_id, inline);
      if (n.children?.length) walkCollect(n.children);
    }
  };
  walkCollect(tree);

  for (const [owningId, rawEntries] of inlineById.entries()) {
    const targets: ReaderRecordPlateBlock[] = [];
    findBlocksByStableId(children, owningId, targets);
    const owningNode = nodeById.get(owningId);
    const parentStableBlockId = owningNode?.parent_block_id ?? null;
    const inlineTargets = targets.filter(
      (target) =>
        target.type === "paragraph" ||
        target.type === "heading" ||
        target.type === "list_item" ||
        target.type === "markdown_blockquote" ||
        target.type === "table_cell",
    );
    if (inlineTargets.length === 0) continue;

    const lengths = inlineTargets.map((target) =>
      target.children.filter(isTextLeaf).reduce((sum, leaf) => sum + leaf.text.length, 0),
    );
    const totalLength = lengths.reduce((sum, length) => sum + length, 0);
    const placements = inlineTargets.map(() => [] as Array<{
      entry: Record<string, unknown>;
      ordinal: number;
      localBefore: number;
    }>);

    rawEntries.forEach((raw, ordinal) => {
      if (!raw || typeof raw !== "object") return;
      const entry = raw as Record<string, unknown>;
      const before = entry["before_utf16"];
      if (
        typeof before !== "number" ||
        !Number.isInteger(before) ||
        before < 0 ||
        before > totalLength ||
        typeof entry["source_url"] !== "string" ||
        typeof entry["alt_text"] !== "string"
      ) {
        return;
      }
      let start = 0;
      for (let index = 0; index < inlineTargets.length; index += 1) {
        const end = start + lengths[index];
        if (before < end || (index === inlineTargets.length - 1 && before <= end)) {
          placements[index].push({
            entry,
            ordinal,
            localBefore: before - start,
          });
          return;
        }
        start = end;
      }
    });

    inlineTargets.forEach((target, index) => {
      enrichInlineImagesForBlock(
        target,
        placements[index],
        owningId,
        parentStableBlockId,
      );
    });
  }
  return children;
}

export function projectReaderPlateSnapshotToReaderRecordPlateDocument(
  snapshot: ReaderPlateSnapshotDto,
): ReaderRecordPlateDocument {
  const progress = mapProgress(snapshot.enhancement_progress);
  const userAssetsBySegment = buildUserAssetsBySegment(snapshot);
  const context: UnitProjectionContext = {
    snapshot,
    sentenceAnalysisBySegment: buildSentenceAnalysisBySegment(snapshot.value),
    sentenceChunkMarksBySegment: buildSentenceChunkMarksBySegment(snapshot.value),
    userHighlightMarksBySegment: userAssetsBySegment.highlights,
    userNoteMarksBySegment: userAssetsBySegment.noteMarks,
    supplementsBySegment: buildSupplementsBySegment(snapshot),
    progressByUnit: buildProgressByUnit(snapshot, progress),
  };

  const flatChildren = snapshot.value.flatMap((unit) =>
    mapUnitToBlocks(unit, context),
  );
  // Stable Document is the structure authority.  Legacy snapshots without
  // the server tree retain the compatibility grouping path; current
  // Markdown snapshots resolve wrappers and nesting from persisted parent
  // identities instead of adjacency or raw Markdown.
  const baseChildren = snapshot.stable_document_tree?.length
    ? projectStableDocumentTree(snapshot.stable_document_tree, flatChildren)
    : groupStableWrapperBlocks(flatChildren);
  const children = snapshot.stable_document_tree?.length
    ? injectImages(baseChildren, snapshot.stable_document_tree)
    : baseChildren;

  return {
    type: "reader_record_plate_document",
    schemaVersion: READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
    record: {
      recordId: snapshot.record_id,
      title: snapshot.record.title,
      generation: snapshot.record.generation,
      productState: snapshot.record.product_state,
      readinessState: snapshot.record.readiness_state,
    },
    snapshot: {
      snapshotId: snapshot.snapshot_id,
      snapshotTakenAt: snapshot.snapshot_taken_at,
      lastEventSequence: snapshot.last_event_sequence,
    },
    base: {
      baseId: snapshot.base.base_id,
      contentSha256: snapshot.base.content_sha256,
      textLengthUtf16: snapshot.base.text_length_utf16,
      hashAlgorithm: snapshot.base.hash_algorithm,
    },
    progress,
    children,
  };
}
