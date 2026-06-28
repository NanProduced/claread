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
  ReaderSourceBlockNodeDto,
  ReaderStableSegmentTextLeafDto,
  ReaderSnapshotUserAssetDto,
  ReaderTranslationNodeDto,
  ReaderUnitNodeDto,
  ReaderVocabularyMarkDto,
  TranslationConfidence,
  VocabularyItemType,
  VocabularyPhraseType,
} from "@/types/api/reader-plate";
import { computeUtf16FNV1a } from "@claread/contracts";
import type { Descendant } from "platejs";
import { deserializeMarkdownToBlocks } from "@/lib/reader-plate/markdown/deserialize";

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

export type ReaderRecordPlateBlock =
  | ReaderRecordPlateParagraphBlock
  | ReaderRecordPlateBlockquoteBlock
  | ReaderRecordPlateCalloutBlock
  | ReaderRecordPlateSentenceAnalysisBlock;

/** 原文段落块 — 一个 anchor segment 对应一个 paragraph */
export interface ReaderRecordPlateParagraphBlock {
  type: "paragraph";
  id: string;
  children: ReaderRecordPlateTextLeaf[];
  data: ReaderRecordPlateParagraphData;
}

export interface ReaderRecordPlateParagraphData {
  anchorSegmentId: string;
  sentenceId: string;
  unitId: string;
  baseId: string;
  baseRange: ReaderRecordPlateRange;
  unitRange: ReaderRecordPlateRange;
  textHash: string;
  hashAlgorithm: ReaderUnitNodeDto["hash_algorithm"];
  segmentType: AnchorSegmentType;
  boundaryQuality: ReaderBoundaryQuality;
}

/** 译文引用块 — unit 级译文 */
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
  targetLanguage: string;
  confidence: TranslationConfidence;
  notes: string[];
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
  chunks: ReaderSentenceAnalysisChunkDto[];
}

/**
 * @deprecated 使用 `Descendant[]` 替代。保留为兼容别名。
 * Callout children 现在是标准 Plate 节点树，由 deserializeMarkdownToBlocks 生成。
 */
export interface ReaderRecordPlateCalloutTextLeaf {
  text: string;
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
  sourceRole: "segment_text";
  baseRange: ReaderRecordPlateRange;
  anchorSegmentId: string;
  segmentRange: ReaderRecordPlateRange;
  marks: ReaderRecordPlateMark[];
}

export type ReaderRecordPlateMark =
  | ReaderRecordPlateVocabularyMark
  | ReaderRecordPlateGrammarMark
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
  kind: VocabularyItemType | "grammar_note";
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

function isTranslationNode(
  node: ReaderUnitNodeDto["children"][number],
): node is ReaderTranslationNodeDto {
  return node.type === "reader_translation";
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

function textFromTranslation(node: ReaderTranslationNodeDto): string {
  return node.children.map((child) => child.text).join("");
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
  const marks = [...vocabularyMarks, ...grammarMarks, ...userHighlightMarks, ...userNoteMarks];

  return splitTextLeafByMarks(leaf, marks);
}

// --- Block builders ---

function buildParagraphBlock(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  context: UnitProjectionContext,
): ReaderRecordPlateParagraphBlock {
  const children = segment.children.flatMap((leaf) =>
    mapTextLeaf(segment, leaf, context),
  );

  return {
    type: "paragraph",
    id: `paragraph:${segment.anchor_segment_id}`,
    children,
    data: {
      anchorSegmentId: segment.anchor_segment_id,
      sentenceId: segment.sentence_id,
      unitId: segment.unit_id,
      baseId: segment.base_id,
      baseRange: range(segment.base_start_utf16, segment.base_end_utf16),
      unitRange: range(segment.unit_start_utf16, segment.unit_end_utf16),
      textHash: segment.text_hash,
      hashAlgorithm: segment.hash_algorithm,
      segmentType: segment.segment_type,
      boundaryQuality: segment.boundary_quality,
    },
  };
}

function buildBlockquoteBlock(
  node: ReaderTranslationNodeDto,
): ReaderRecordPlateBlockquoteBlock | null {
  if (node.target_scope !== "unit") {
    return null;
  }

  return {
    type: "blockquote",
    id: `blockquote:${node.layer_id}:${node.target_key}`,
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
      targetLanguage: node.target_language,
      confidence: node.confidence,
      notes: node.notes,
    },
  };
}

function buildGrammarCalloutBlocks(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
): ReaderRecordPlateCalloutBlock[] {
  const callouts: ReaderRecordPlateCalloutBlock[] = [];

  for (const leaf of segment.children) {
    const grammarMarks = leaf.reader_grammar_note_marks ?? [];
    for (const mark of grammarMarks) {
      if (!mark.show_note_chip) {
        continue;
      }
      callouts.push({
        type: "callout",
        id: `callout:grammar:${mark.item_id}`,
        variant: "grammar",
        icon: "📖",
        children: deserializeMarkdownToBlocks(mark.note),
        data: {
          anchorSegmentId: mark.anchor_segment_id,
          unitId: segment.unit_id,
          layerId: mark.layer_id,
          itemId: mark.item_id,
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
      chunks: node.chunks,
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

function mapUnitToBlocks(
  unit: ReaderUnitNodeDto,
  context: UnitProjectionContext,
): ReaderRecordPlateBlock[] {
  const blocks: ReaderRecordPlateBlock[] = [];

  for (const child of unit.children) {
    if (isSourceBlockNode(child)) {
      for (const sourceChild of child.children) {
        if (isAnchorSegmentNode(sourceChild)) {
          // 1. 原文段落 paragraph block
          blocks.push(buildParagraphBlock(sourceChild, context));
          // 2. grammar_note callout blocks (showCue=true)
          blocks.push(...buildGrammarCalloutBlocks(sourceChild));
          // 3. sentence_analysis blocks
          blocks.push(...buildSentenceAnalysisBlocks(sourceChild, context));
          // 4. ask_supplement callout blocks
          blocks.push(...buildSupplementCalloutBlocks(sourceChild, context));
        }
      }
      continue;
    }
    if (isTranslationNode(child)) {
      const blockquote = buildBlockquoteBlock(child);
      if (blockquote) {
        blocks.push(blockquote);
      }
    }
  }

  return blocks;
}

export function projectReaderPlateSnapshotToReaderRecordPlateDocument(
  snapshot: ReaderPlateSnapshotDto,
): ReaderRecordPlateDocument {
  const progress = mapProgress(snapshot.enhancement_progress);
  const userAssetsBySegment = buildUserAssetsBySegment(snapshot);
  const context: UnitProjectionContext = {
    snapshot,
    sentenceAnalysisBySegment: buildSentenceAnalysisBySegment(snapshot.value),
    userHighlightMarksBySegment: userAssetsBySegment.highlights,
    userNoteMarksBySegment: userAssetsBySegment.noteMarks,
    supplementsBySegment: buildSupplementsBySegment(snapshot),
    progressByUnit: buildProgressByUnit(snapshot, progress),
  };

  const children = snapshot.value.flatMap((unit) =>
    mapUnitToBlocks(unit, context),
  );

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
