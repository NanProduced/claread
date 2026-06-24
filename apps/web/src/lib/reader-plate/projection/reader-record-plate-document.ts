import type {
  AnchorSegmentType,
  ParsedDecisionState,
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
  ReaderSourceBlockChildNodeDto,
  ReaderSourceBlockNodeDto,
  ReaderStableSegmentTextLeafDto,
  ReaderStableSeparatorLeafDto,
  ReaderSnapshotUserAssetDto,
  ReaderTranslationNodeDto,
  ReaderUnitNodeDto,
  ReaderUnitType,
  ReaderVocabularyMarkDto,
  TranslationConfidence,
  VocabularyItemType,
  VocabularyPhraseType,
} from "@/types/api/reader-plate";
import { computeUtf16FNV1a } from "@claread/contracts";

export const READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION =
  "reader-record-plate-document/v1" as const;

export type ReaderRecordPlateDocumentSchemaVersion =
  typeof READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION;

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
  children: ReaderRecordPlateUnitNode[];
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

export interface ReaderRecordPlateUnitNode {
  type: "reader_record_unit";
  id: string;
  baseId: string;
  unitId: string;
  orderIndex: number;
  unitType: ReaderUnitType;
  boundaryQuality: ReaderBoundaryQuality;
  baseRange: ReaderRecordPlateRange;
  textHash: string;
  hashAlgorithm: ReaderUnitNodeDto["hash_algorithm"];
  parsedDecision?: ReaderRecordPlateParsedDecision;
  progress: ReaderRecordPlateProgressLayer[];
  cues: ReaderRecordPlateCue[];
  children: ReaderRecordPlateUnitChildNode[];
}

export interface ReaderRecordPlateParsedDecision {
  state: ParsedDecisionState;
  policyCode: string;
  rationaleCode?: string | null;
}

export type ReaderRecordPlateUnitChildNode =
  | ReaderRecordPlateSourceBlockNode
  | ReaderRecordPlateTranslationBlockNode;

export interface ReaderRecordPlateSourceBlockNode {
  type: "reader_record_source_block";
  id: string;
  baseId: string;
  unitId: string;
  baseRange: ReaderRecordPlateRange;
  children: ReaderRecordPlateSourceBlockChildNode[];
}

export type ReaderRecordPlateSourceBlockChildNode =
  | ReaderRecordPlateAnchorSegmentNode
  | ReaderRecordPlateSeparatorLeaf;

export interface ReaderRecordPlateAnchorSegmentNode {
  type: "reader_record_anchor_segment";
  id: string;
  baseId: string;
  unitId: string;
  anchorSegmentId: string;
  sentenceId: string;
  segmentType: AnchorSegmentType;
  boundaryQuality: ReaderBoundaryQuality;
  baseRange: ReaderRecordPlateRange;
  unitRange: ReaderRecordPlateRange;
  textHash: string;
  hashAlgorithm: ReaderUnitNodeDto["hash_algorithm"];
  cues: ReaderRecordPlateCue[];
  children: ReaderRecordPlateTextLeaf[];
}

export interface ReaderRecordPlateSeparatorLeaf {
  text: string;
  owner: "stable";
  lockSource: true;
  sourceRole: "separator";
  baseRange: ReaderRecordPlateRange;
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
  | ReaderRecordPlateUserHighlightMark;

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
  anchor: ReaderRecordPlateTextAnchor;
  createdAt?: string | null;
  updatedAt: string;
}

export type ReaderRecordPlateCue =
  | ReaderRecordPlateGrammarCue
  | ReaderRecordPlateSentenceAnalysisCue
  | ReaderRecordPlateUserCommentCue;

export interface ReaderRecordPlateGrammarCue {
  type: "reader_record_grammar_cue";
  id: string;
  owner: "system_ai";
  anchor: ReaderRecordPlateTextAnchor;
  itemId: string;
  grammarPoint: string;
  pattern?: string | null;
  note: string;
}

export interface ReaderRecordPlateSentenceAnalysisCue {
  type: "reader_record_sentence_analysis_cue";
  id: string;
  owner: "system_ai";
  layerId: string;
  layerVersion: number;
  analysisId: string;
  baseId: string;
  unitId: string;
  targetScope: "unit";
  targetKey: string;
  anchorSegmentId: string;
  selectedText: string;
  label: string;
  analysis: string;
  chunks: ReaderSentenceAnalysisChunkDto[];
}

export interface ReaderRecordPlateUserCommentCue {
  type: "reader_record_user_comment_cue";
  id: string;
  owner: "user";
  assetId: string;
  assetType: string;
  anchor: ReaderRecordPlateTextAnchor;
  label: string;
  createdAt?: string | null;
  updatedAt: string;
}

export interface ReaderRecordPlateTranslationBlockNode {
  type: "reader_record_unit_translation";
  id: string;
  owner: "system_ai";
  placement: "unit";
  layerId: string;
  layerVersion: number;
  baseId: string;
  unitId: string;
  targetScope: "unit";
  targetKey: string;
  targetLanguage: string;
  confidence: TranslationConfidence;
  notes: string[];
  children: ReaderRecordPlateTranslationTextLeaf[];
}

export interface ReaderRecordPlateTranslationTextLeaf {
  text: string;
  owner: "system_ai";
  sourceRole: "unit_translation_text";
}

interface UnitProjectionContext {
  snapshot: ReaderPlateSnapshotDto;
  sentenceAnalysisBySegment: Map<string, ReaderRecordPlateSentenceAnalysisCue[]>;
  grammarCuesBySegment: Map<string, ReaderRecordPlateGrammarCue[]>;
  userHighlightMarksBySegment: Map<string, ReaderRecordPlateUserHighlightMark[]>;
  userCommentCuesBySegment: Map<string, ReaderRecordPlateUserCommentCue[]>;
  progressByUnit: Map<string, ReaderRecordPlateProgressLayer[]>;
}

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
): Map<string, ReaderRecordPlateSentenceAnalysisCue[]> {
  const result = new Map<string, ReaderRecordPlateSentenceAnalysisCue[]>();
  for (const unit of value) {
    for (const child of unit.children) {
      if (!isSentenceAnalysisNode(child)) {
        continue;
      }
      const cue = mapSentenceAnalysisCue(child);
      const list = result.get(cue.anchorSegmentId) ?? [];
      list.push(cue);
      result.set(cue.anchorSegmentId, list);
    }
  }
  return result;
}

function mapSentenceAnalysisCue(
  node: ReaderSentenceAnalysisNodeDto,
): ReaderRecordPlateSentenceAnalysisCue {
  return {
    type: "reader_record_sentence_analysis_cue",
    id: `sentence_analysis:${node.analysis_id}`,
    owner: "system_ai",
    layerId: node.layer_id,
    layerVersion: node.layer_version,
    analysisId: node.analysis_id,
    baseId: node.base_id,
    unitId: node.unit_id,
    targetScope: node.target_scope,
    targetKey: node.target_key,
    anchorSegmentId: node.anchor_segment_id,
    selectedText: node.selected_text,
    label: node.label,
    analysis: node.analysis,
    chunks: node.chunks,
  };
}

function addCue<T extends ReaderRecordPlateCue>(
  index: Map<string, T[]>,
  anchorSegmentId: string,
  cue: T,
): void {
  const list = index.get(anchorSegmentId) ?? [];
  list.push(cue);
  index.set(anchorSegmentId, list);
}

function isHighlightAssetType(assetType: string): boolean {
  return (
    assetType === "quick_highlight" ||
    assetType === "highlight" ||
    assetType === "user_highlight"
  );
}

function isCommentAssetType(assetType: string): boolean {
  return (
    assetType === "comment" ||
    assetType === "note" ||
    assetType === "reader_note"
  );
}

function userAssetLabel(assetType: string): string {
  if (assetType === "comment") {
    return "评论";
  }
  return "笔记";
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
  comments: Map<string, ReaderRecordPlateUserCommentCue[]>;
} {
  const highlights = new Map<string, ReaderRecordPlateUserHighlightMark[]>();
  const comments = new Map<string, ReaderRecordPlateUserCommentCue[]>();

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
        anchor,
        createdAt: asset.created_at,
        updatedAt: asset.updated_at,
      });
      highlights.set(anchor.anchorSegmentId, list);
      continue;
    }

    if (isCommentAssetType(asset.asset_type)) {
      addCue(comments, anchor.anchorSegmentId, {
        type: "reader_record_user_comment_cue",
        id: `user_comment:${asset.asset_id}`,
        owner: "user",
        assetId: asset.asset_id,
        assetType: asset.asset_type,
        anchor,
        label: userAssetLabel(asset.asset_type),
        createdAt: asset.created_at,
        updatedAt: asset.updated_at,
      });
    }
  }

  return { highlights, comments };
}

function marksForRange(
  marks: ReaderRecordPlateMark[],
  startUtf16: number,
  endUtf16: number,
): ReaderRecordPlateMark[] {
  return marks.filter(
    (mark) =>
      mark.anchor.segmentStartOffset <= startUtf16 &&
      mark.anchor.segmentEndOffset >= endUtf16,
  );
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

function mapGrammarCue(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  mark: ReaderGrammarNoteMarkDto,
): ReaderRecordPlateGrammarCue {
  return {
    type: "reader_record_grammar_cue",
    id: `grammar_note:${mark.item_id}`,
    owner: "system_ai",
    anchor: markAnchor(segment, mark),
    itemId: mark.item_id,
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
    leaf.reader_grammar_note_marks?.map((mark) => {
      const mapped = mapGrammarMark(segment, mark);
      if (mark.show_note_chip) {
        addCue(
          context.grammarCuesBySegment,
          mark.anchor_segment_id,
          mapGrammarCue(segment, mark),
        );
      }
      return mapped;
    }) ?? [];
  const userHighlightMarks =
    context.userHighlightMarksBySegment.get(segment.anchor_segment_id) ?? [];
  const marks = [...vocabularyMarks, ...grammarMarks, ...userHighlightMarks];

  return splitTextLeafByMarks(leaf, marks);
}

function mapSeparatorLeaf(
  leaf: ReaderStableSeparatorLeafDto,
): ReaderRecordPlateSeparatorLeaf {
  return {
    text: leaf.text,
    owner: "stable",
    lockSource: true,
    sourceRole: "separator",
    baseRange: range(leaf.base_start_utf16, leaf.base_end_utf16),
  };
}

function mapAnchorSegment(
  segment: Extract<ReaderSourceBlockChildNodeDto, { type: "reader_anchor_segment" }>,
  context: UnitProjectionContext,
): ReaderRecordPlateAnchorSegmentNode {
  const children = segment.children.flatMap((leaf) =>
    mapTextLeaf(segment, leaf, context),
  );
  const grammarCues = context.grammarCuesBySegment.get(segment.anchor_segment_id) ?? [];
  const sentenceAnalysisCues =
    context.sentenceAnalysisBySegment.get(segment.anchor_segment_id) ?? [];
  const userCommentCues =
    context.userCommentCuesBySegment.get(segment.anchor_segment_id) ?? [];

  return {
    type: "reader_record_anchor_segment",
    id: `anchor_segment:${segment.anchor_segment_id}`,
    baseId: segment.base_id,
    unitId: segment.unit_id,
    anchorSegmentId: segment.anchor_segment_id,
    sentenceId: segment.sentence_id,
    segmentType: segment.segment_type,
    boundaryQuality: segment.boundary_quality,
    baseRange: range(segment.base_start_utf16, segment.base_end_utf16),
    unitRange: range(segment.unit_start_utf16, segment.unit_end_utf16),
    textHash: segment.text_hash,
    hashAlgorithm: segment.hash_algorithm,
    cues: [...grammarCues, ...sentenceAnalysisCues, ...userCommentCues],
    children,
  };
}

function mapSourceBlock(
  node: ReaderSourceBlockNodeDto,
  context: UnitProjectionContext,
): ReaderRecordPlateSourceBlockNode {
  return {
    type: "reader_record_source_block",
    id: `source_block:${node.unit_id}`,
    baseId: node.base_id,
    unitId: node.unit_id,
    baseRange: range(node.base_start_utf16, node.base_end_utf16),
    children: node.children.map((child) =>
      isAnchorSegmentNode(child)
        ? mapAnchorSegment(child, context)
        : mapSeparatorLeaf(child),
    ),
  };
}

function mapTranslationBlock(
  node: ReaderTranslationNodeDto,
): ReaderRecordPlateTranslationBlockNode | null {
  if (node.target_scope !== "unit") {
    return null;
  }

  return {
    type: "reader_record_unit_translation",
    id: `translation:${node.layer_id}:${node.target_key}`,
    owner: "system_ai",
    placement: "unit",
    layerId: node.layer_id,
    layerVersion: node.layer_version,
    baseId: node.base_id,
    unitId: node.unit_id,
    targetScope: "unit",
    targetKey: node.target_key,
    targetLanguage: node.target_language,
    confidence: node.confidence,
    notes: node.notes,
    children: [
      {
        text: textFromTranslation(node),
        owner: "system_ai",
        sourceRole: "unit_translation_text",
      },
    ],
  };
}

function mapUnit(
  unit: ReaderUnitNodeDto,
  context: UnitProjectionContext,
): ReaderRecordPlateUnitNode {
  const children: ReaderRecordPlateUnitChildNode[] = [];

  for (const child of unit.children) {
    if (isSourceBlockNode(child)) {
      children.push(mapSourceBlock(child, context));
      continue;
    }
    if (isTranslationNode(child)) {
      const translation = mapTranslationBlock(child);
      if (translation) {
        children.push(translation);
      }
    }
  }

  const cues = children.flatMap((child) =>
    child.type === "reader_record_source_block"
      ? child.children.flatMap((sourceChild) =>
          "type" in sourceChild &&
          sourceChild.type === "reader_record_anchor_segment"
            ? sourceChild.cues
            : [],
        )
      : [],
  );

  const parsedDecision = context.snapshot.parsed_decisions.find(
    (decision) => decision.unit_id === unit.unit_id,
  );

  return {
    type: "reader_record_unit",
    id: `unit:${unit.unit_id}`,
    baseId: unit.base_id,
    unitId: unit.unit_id,
    orderIndex: unit.order_index,
    unitType: unit.unit_type,
    boundaryQuality: unit.boundary_quality,
    baseRange: range(unit.base_start_utf16, unit.base_end_utf16),
    textHash: unit.text_hash,
    hashAlgorithm: unit.hash_algorithm,
    parsedDecision: parsedDecision
      ? {
          state: parsedDecision.parsed_state,
          policyCode: parsedDecision.policy_code,
          rationaleCode: parsedDecision.rationale_code,
        }
      : undefined,
    progress: context.progressByUnit.get(unit.unit_id) ?? [],
    cues,
    children,
  };
}

export function projectReaderPlateSnapshotToReaderRecordPlateDocument(
  snapshot: ReaderPlateSnapshotDto,
): ReaderRecordPlateDocument {
  const progress = mapProgress(snapshot.enhancement_progress);
  const userAssetsBySegment = buildUserAssetsBySegment(snapshot);
  const context: UnitProjectionContext = {
    snapshot,
    sentenceAnalysisBySegment: buildSentenceAnalysisBySegment(snapshot.value),
    grammarCuesBySegment: new Map<string, ReaderRecordPlateGrammarCue[]>(),
    userHighlightMarksBySegment: userAssetsBySegment.highlights,
    userCommentCuesBySegment: userAssetsBySegment.comments,
    progressByUnit: buildProgressByUnit(snapshot, progress),
  };

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
    children: snapshot.value.map((unit) => mapUnit(unit, context)),
  };
}
