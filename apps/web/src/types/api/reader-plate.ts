/**
 * Reader Plate Snapshot DTO types.
 *
 * Mirrors the backend schemas in
 * `services/api/app/schemas/reader_orchestration.py`. These are the durable
 * API contracts for the new Reader Plate vertical slice:
 *   - POST /reader/records/plain-text
 *   - GET  /reader/records/{record_id}/snapshot
 *   - GET  /reader/records/{record_id}/events
 *
 * This Plate snapshot contract is independent from the legacy scene DTO path.
 */

export const READER_PLATE_SNAPSHOT_SCHEMA_KIND = "reader_plate_snapshot" as const;
export const READER_TEXT_RANGE_HASH_ALGORITHM = "fnv1a32-utf16" as const;
export const READER_TEXT_RANGE_OFFSET_UNIT = "utf16" as const;

export type ReaderUnitType =
  | "body"
  | "heading"
  | "list"
  | "quote"
  | "unknown"
  | "fallback";
// ``unit_type`` mirrors the DB CHECK constraint on ``reading_units``
// (infra/migrations/0001_initial_schema.sql): only the 6 legacy
// heuristic values are accepted. The stable block type
// (``paragraph`` / ``list_item`` / ``blockquote`` / ``table`` /
// ``table_row`` / ``table_cell`` / ``code_block``) is carried by the
// separate nullable ``stable_block_type`` field on navigation units
// and the ``stableBlockType`` field in the snapshot payload — it
// MUST NOT appear in ``unit_type``.

export type ReaderBoundaryQuality = "normal" | "low";

export type ReaderLayerTargetScope =
  | "unit"
  | "anchor_segment"
  | "unit_range"
  | "record";

export type ReaderLayerType =
  | "translation"
  | "vocabulary"
  | "grammar_note"
  | "sentence_analysis"
  /** Inventory-only audit layer; trusted product projection is top-level `semantic_outline`. */
  | "semantic_outline";

export type AnchorSegmentType = "sentence" | "clause" | "fallback_window";

export type ParsedDecisionState =
  | "not_started"
  | "partial"
  | "parsed"
  | "skipped"
  | "failed";

export type ReadingRecordProductState =
  | "processing"
  | "needs_confirmation"
  | "readable_enhancing"
  | "action_required"
  | "failed"
  | "deleted";

export type ReadingRecordReadinessState =
  | "submitted"
  | "candidate_base_ready"
  | "article_ready"
  | "initial_enhancement_ready"
  | "coverage_complete";

export type ReaderEnhancementProgressOverallStatus =
  | "processing"
  | "readable_enhancing"
  | "ready"
  | "failed"
  | "action_required";

export type ReaderEnhancementProgressLayerStatus =
  | "not_started"
  | "queued"
  | "processing"
  | "succeeded"
  | "failed"
  | "action_required";

export type ReaderEnhancementCapability =
  | "translation"
  | "vocabulary"
  | "grammar";

export type TranslationConfidence = "low" | "normal" | "high";
export type VocabularyItemType =
  | "vocab_highlight"
  | "phrase_gloss"
  | "context_gloss";
export type VocabularyPhraseType =
  | "verb_expression"
  | "fixed_collocation"
  | "name_or_term"
  | "idiom";

export type ReaderPlateOwner =
  | "stable"
  | "system_ai"
  | "ask_supplement"
  | "user"
  | "ephemeral";

// ---------------------------------------------------------------------------
// Reader Orchestration reading strategy contract
//
// Mirrors `ReaderOrchestrationReadingGoal` / `ReaderOrchestrationReadingVariant`
// in services/api/app/schemas/reader_orchestration.py. `academic` /
// `academic_general` from legacy AI Workflow are intentionally excluded; the
// backend rejects them at the schema layer (422) rather than silently mapping
// them to a daily/exam variant.
// ---------------------------------------------------------------------------

export type ReaderOrchestrationReadingGoalDto = "daily_reading" | "exam";

export type ReaderOrchestrationReadingVariantDto =
  | "beginner_reading"
  | "intermediate_reading"
  | "intensive_reading"
  | "gaokao"
  | "cet"
  | "kaoyan"
  | "tem"
  | "ielts_toefl";

// ---------------------------------------------------------------------------
// Plain-text submit
// ---------------------------------------------------------------------------

export interface ReaderPlainTextSubmitRequestDto {
  plain_text: string;
  title?: string | null;
  language?: string | null;
  source_metadata?: Record<string, unknown> | null;
  client_record_id?: string | null;
  reading_goal: ReaderOrchestrationReadingGoalDto;
  reading_variant: ReaderOrchestrationReadingVariantDto;
}

export interface ReaderPlainTextSubmitResponseDto {
  record_id: string;
  base_id: string;
  article_ready_sequence: number;
  snapshot: ReaderPlateSnapshotDto;
}

// ---------------------------------------------------------------------------
// Snapshot wrapper
// ---------------------------------------------------------------------------

export interface ReaderSnapshotBaseDto {
  base_id: string;
  content_sha256: string;
  canonicalizer_version: string;
  builder_version: string;
  segmenter_version: string;
  text_length_utf16: number;
  hash_algorithm: typeof READER_TEXT_RANGE_HASH_ALGORITHM;
}

export type ReaderTitleGenerationStatus =
  | "pending"
  | "succeeded"
  | "failed_retryable";

export interface ReaderSnapshotRecordDto {
  title: string;
  display_title_zh: string | null;
  title_generation_status: ReaderTitleGenerationStatus;
  title_generation_error_code: string | null;
  title_generation_error_message: string | null;
  reading_goal: ReaderOrchestrationReadingGoalDto;
  reading_variant: ReaderOrchestrationReadingVariantDto;
  created_at: string;
  source_type: string;
  source_metadata: Record<string, unknown>;
  generation: number;
  product_state: ReadingRecordProductState;
  readiness_state: ReadingRecordReadinessState;
}

export interface ReaderSnapshotNavigationUnitDto {
  unit_id: string;
  order_index: number;
  unit_type: ReaderUnitType;
  boundary_quality: ReaderBoundaryQuality;
  label?: string | null;
  base_start_utf16: number;
  base_end_utf16: number;
  text_hash: string;
  hash_algorithm: typeof READER_TEXT_RANGE_HASH_ALGORITHM;
  /**
   * Stable block metadata projected from `BuiltReadingUnit` when a
   * `StableBlockAnnotation` matched the unit's UTF-16 range. Both fields
   * are optional and absent for legacy snapshots (no annotations) so the
   * existing byte-for-byte shape stays stable.
   *
   * `stable_block_type` is the narrow stable-document block type string
   * (`heading` / `paragraph` / `list` / `code_block` / `table` / ...).
   * `heading_level` is the 1-based heading level when `stable_block_type
   * === "heading"`, otherwise `null`.
   */
  stable_block_type?: string | null;
  heading_level?: number | null;
}

export interface ReaderSnapshotNavigationDto {
  units: ReaderSnapshotNavigationUnitDto[];
}

export interface ReaderUnitAnchorDto {
  anchor_type: "unit";
  base_id: string;
  unit_id: string;
  text_hash: string;
  hash_algorithm: typeof READER_TEXT_RANGE_HASH_ALGORITHM;
}

export interface ReaderTextRangeAnchorDto {
  anchor_type: "text_range";
  base_id: string;
  unit_id: string;
  anchor_segment_id: string;
  sentence_id?: string | null;
  segment_type: AnchorSegmentType;
  offset_unit: typeof READER_TEXT_RANGE_OFFSET_UNIT;
  start_offset: number;
  end_offset: number;
  selected_text: string;
  text_hash: string;
  hash_algorithm: typeof READER_TEXT_RANGE_HASH_ALGORITHM;
}

export type UserEditorialAssetAnchorScope =
  | "stable_source"
  | "translation"
  | "system_ai_layer"
  | "ask_supplement";

export interface UserEditorialAssetAnchorDto {
  record_id: string;
  base_id: string;
  generation: number;
  unit_id: string;
  anchor_segment_id: string;
  scope?: UserEditorialAssetAnchorScope;
  offset_unit?: typeof READER_TEXT_RANGE_OFFSET_UNIT;
  start_offset: number;
  end_offset: number;
  selected_text: string;
  text_hash: string;
  hash_algorithm?: typeof READER_TEXT_RANGE_HASH_ALGORITHM;
}

export type ReaderSnapshotAnchorDto = ReaderUnitAnchorDto | ReaderTextRangeAnchorDto;

export interface ReaderSnapshotAnchorSegmentDto {
  anchor_segment_id: string;
  sentence_id: string;
  paragraph_id: string;
  unit_id: string;
  order_index: number;
  unit_order_index: number;
  segment_type: AnchorSegmentType;
  boundary_quality: ReaderBoundaryQuality;
  base_start_utf16: number;
  base_end_utf16: number;
  unit_start_utf16: number;
  unit_end_utf16: number;
  text_hash: string;
  hash_algorithm: typeof READER_TEXT_RANGE_HASH_ALGORITHM;
}

export interface TranslationGroupDto {
  group_id: string;
  anchor_segment_ids: string[];
  source_text_hash: string;
  translated_text: string;
}

export interface TranslationLayerOutputDto {
  groups: TranslationGroupDto[];
}

export interface VocabularyHighlightItemDto {
  item_type: "vocab_highlight";
  anchor: ReaderTextRangeAnchorDto;
  headword: string;
  brief_explanation?: string | null;
  reason?: string | null;
}

export interface VocabularyPhraseGlossItemDto {
  item_type: "phrase_gloss";
  anchor: ReaderTextRangeAnchorDto;
  phrase: string;
  phrase_type: VocabularyPhraseType;
  gloss: string;
  learning_note?: string | null;
  example?: string | null;
}

export interface VocabularyContextGlossItemDto {
  item_type: "context_gloss";
  anchor: ReaderTextRangeAnchorDto;
  display: string;
  gloss: string;
  reason: string;
}

export type VocabularyLayerItemDto =
  | VocabularyHighlightItemDto
  | VocabularyPhraseGlossItemDto
  | VocabularyContextGlossItemDto;

export interface VocabularyLayerOutputDto {
  schema_version: 1;
  items: VocabularyLayerItemDto[];
}

export interface GrammarNoteItemDto {
  item_type: "grammar_note";
  spans: ReaderTextRangeAnchorDto[];
  grammar_point: string;
  pattern?: string | null;
  note: string;
}

export interface GrammarNoteLayerOutputDto {
  schema_version: 1;
  items: GrammarNoteItemDto[];
}

export interface ReaderSentenceAnalysisChunkDto {
  order: number;
  label: string;
  text: string;
}

export interface SentenceAnalysisItemDto {
  item_type: "sentence_analysis";
  anchor: ReaderTextRangeAnchorDto;
  label: string;
  analysis: string;
  chunks: ReaderSentenceAnalysisChunkDto[];
}

export interface SentenceAnalysisLayerOutputDto {
  schema_version: 1;
  items: SentenceAnalysisItemDto[];
}

interface ReaderSnapshotLayerBaseDto {
  layer_id: string;
  layer_type: ReaderLayerType;
  layer_subtype?: string | null;
  owner: "system_ai";
  base_id: string;
  target_scope: ReaderLayerTargetScope;
  target_key: string;
  status: "published";
  schema_version: number;
  published_at: string;
}

export interface ReaderTranslationSnapshotLayerDto
  extends ReaderSnapshotLayerBaseDto {
  layer_type: "translation";
  output: TranslationLayerOutputDto;
}

export interface ReaderVocabularySnapshotLayerDto
  extends ReaderSnapshotLayerBaseDto {
  layer_type: "vocabulary";
  output: VocabularyLayerOutputDto;
}

export interface ReaderGrammarNoteSnapshotLayerDto
  extends ReaderSnapshotLayerBaseDto {
  layer_type: "grammar_note";
  output: GrammarNoteLayerOutputDto;
}

export interface ReaderSentenceAnalysisSnapshotLayerDto
  extends ReaderSnapshotLayerBaseDto {
  layer_type: "sentence_analysis";
  output: SentenceAnalysisLayerOutputDto;
}

/**
 * Inventory row for durable semantic_outline layers (audit only).
 * Product projection is optional top-level `ReaderPlateSnapshotDto.semantic_outline`.
 */
export interface ReaderSemanticOutlineSnapshotLayerDto
  extends ReaderSnapshotLayerBaseDto {
  layer_type: "semantic_outline";
  target_scope: "record";
  target_key: "document" | string;
  /** Wire envelope; consumers must prefer top-level semantic_outline. */
  output: Record<string, unknown>;
}

export type ReaderSnapshotLayerDto =
  | ReaderTranslationSnapshotLayerDto
  | ReaderVocabularySnapshotLayerDto
  | ReaderGrammarNoteSnapshotLayerDto
  | ReaderSentenceAnalysisSnapshotLayerDto
  | ReaderSemanticOutlineSnapshotLayerDto;

export interface ReaderSnapshotAskSupplementDto {
  supplement_id: string;
  owner: "ask_supplement";
  anchor: ReaderSnapshotAnchorDto | null;
  content: unknown;
  created_at: string;
}

export type ReaderSnapshotUserAssetType =
  | "quick_highlight"
  | "highlight"
  | "user_highlight"
  | "comment"
  | "note"
  | "reader_note"
  | (string & {});

export interface ReaderSnapshotUserAssetDto {
  asset_id: string;
  asset_type: ReaderSnapshotUserAssetType;
  owner: "user";
  reading_record_id: string;
  generation: number;
  anchor: ReaderSnapshotAnchorDto;
  note_text?: string | null;
  color?: string | null;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

export interface ReaderSnapshotParsedDecisionDto {
  unit_id: string;
  policy_code: string;
  parsed_state: ParsedDecisionState;
  rationale_code?: string | null;
}

export interface ReaderEnhancementProgressLayerDto {
  capability: ReaderEnhancementCapability;
  layer_type?: ReaderLayerType | null;
  status: ReaderEnhancementProgressLayerStatus;
  job_status?:
    | "queued"
    | "claimed"
    | "retry_later"
    | "paused"
    | "skipped"
    | "succeeded"
    | "failed_terminal"
    | "cancelled"
    | "superseded"
    | null;
  job_type?: string | null;
  layer_id?: string | null;
  job_id?: string | null;
  target_type?: string | null;
  target_scope?: ReaderLayerTargetScope | null;
  target_key?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  failure_code?: string | null;
  failure_message?: string | null;
}

export interface ReaderEnhancementProgressDto {
  overall_status: ReaderEnhancementProgressOverallStatus;
  layers: ReaderEnhancementProgressLayerDto[];
}

/**
 * Optional top-level outline projection.
 * - absent (undefined): legacy backend without the field
 * - null: new backend, no trusted published ready|partial
 * - object: only ready|partial is consumable (see hasTrustedSemanticOutline)
 */
export type ReaderPlateSnapshotSemanticOutline =
  | import("@/lib/reader-plate/projection/semantic-outline").ReaderSemanticOutlineProjectionDto
  | null
  | undefined;

export interface ReaderPlateSnapshotDto {
  schema_kind: typeof READER_PLATE_SNAPSHOT_SCHEMA_KIND;
  snapshot_id: string;
  snapshot_taken_at: string;
  last_event_sequence: number;
  record_id: string;
  record: ReaderSnapshotRecordDto;
  base: ReaderSnapshotBaseDto;
  navigation: ReaderSnapshotNavigationDto;
  anchor_segments: ReaderSnapshotAnchorSegmentDto[];
  enhancement_layers: ReaderSnapshotLayerDto[];
  enhancement_progress?: ReaderEnhancementProgressDto;
  ask_supplements: ReaderSnapshotAskSupplementDto[];
  user_assets: ReaderSnapshotUserAssetDto[];
  parsed_decisions: ReaderSnapshotParsedDecisionDto[];
  value: ReaderPlateValueDto;
  /** Server-owned Stable Document tree; absent only on legacy snapshots. */
  stable_document_tree?: ReaderStableDocumentBlockNodeDto[];
  /** Optional; undefined | null = no trusted outline. Do not parse enhancement_layers for L2. */
  semantic_outline?: import("@/lib/reader-plate/projection/semantic-outline").ReaderSemanticOutlineProjectionDto | null;
}

// ---------------------------------------------------------------------------
// Plate value node tree (snapshot.value)
// ---------------------------------------------------------------------------

/**
 * The Plate document value produced by the backend snapshot builder.
 * It is a list of top-level `reader_unit` nodes.
 */
export type ReaderPlateValueDto = ReaderPlateValueNode[];

export type ReaderPlateValueNode = ReaderUnitNodeDto;

export interface ReaderUnitNodeDto {
  type: "reader_unit";
  owner: "stable";
  base_id: string;
  unit_id: string;
  order_index: number;
  unit_type: ReaderUnitType;
  boundary_quality: ReaderBoundaryQuality;
  base_start_utf16: number;
  base_end_utf16: number;
  text_hash: string;
  hash_algorithm: typeof READER_TEXT_RANGE_HASH_ALGORITHM;
  children: ReaderUnitChildNodeDto[];
}

export type ReaderUnitChildNodeDto =
  | ReaderSourceBlockNodeDto
  | ReaderTranslationGroupNodeDto
  | ReaderTranslationNodeDto
  | ReaderSentenceAnalysisNodeDto;

/**
 * Inline mark emitted by the backend Markdown parser into
 * `reader_source_block` payload. Mirrors the backend
 * `inline_marks` shape (`{type, start, end, [href]}`) where `start` /
 * `end` are UTF-16 code unit offsets within the block's canonical text.
 *
 * Unlike `ReaderStructuredSourceInlineMark` (which carries the plain-text
 * fragment), this representation expresses marks as intervals over the
 * block text so the renderer can slice leaves at mark boundaries while
 * preserving anchor segment offsets.
 */
export interface ReaderSourceBlockInlineMarkDto {
  /** Inline mark kind (mirrors backend `type` field). */
  type:
    | "strong"
    | "em"
    | "strikethrough"
    | "inline_code"
    | "link";
  /** UTF-16 start offset within the block's canonical text. */
  start: number;
  /** UTF-16 end offset within the block's canonical text (exclusive). */
  end: number;
  /** Safe href for `type === "link"` (whitelist-filtered by parser). */
  href?: string;
}

export interface ReaderSourceBlockNodeDto {
  type: "reader_source_block";
  owner: "stable";
  base_id: string;
  unit_id: string;
  base_start_utf16: number;
  base_end_utf16: number;
  children: ReaderSourceBlockChildNodeDto[];
  /**
   * Stable block metadata projected by the backend
   * `_project_stable_block_fields`. All fields are optional and absent
   * for legacy snapshots (no `StableBlockAnnotation`) so the existing
   * byte-for-byte shape stays stable.
   *
   * `stableBlockType` is the narrow stable-document block type string
   * (`heading` / `paragraph` / `list` / `list_item` / `code_block` /
   * `table` / `table_row` / `table_cell` / `blockquote` /
   * `thematic_break`).
   * `headingLevel` is the 1-based heading level when
   * `stableBlockType === "heading"`, otherwise `null`.
   * `inlineMarks` is the list of inline mark intervals over the block's
   * canonical text (UTF-16 offsets).
   * `tableRole` is `table` / `row` / `cell` when `stableBlockType` is a
   * table-related type, otherwise `null`.
   * `parentStableBlockId` is the parent block id for nested structures
   * (table rows → table, table cells → row, list items → list).
   * `stableBlockId` is the diagnostic identifier of the stable block
   * (not a render contract; emitted only when present).
   *
   * L1 additions (emitted whenever `stableBlockType` is present; `null`
   * when not applicable to the block type):
   * `codeLanguage` is the fenced code info string for `code_block`
   * (`null` for language-less code). `tableIsHeader` is the header marker
   * for `table_row` / `table_cell`. `tableAlignment` is the per-cell
   * alignment (`left` / `center` / `right` / `default`) for `table_cell`.
   * Table-level alignment/header metadata is not part of this per-unit DTO:
   * table wrapper blocks do not produce reading units.
   */
  stableBlockType?: string | null;
  headingLevel?: number | null;
  inlineMarks?: ReaderSourceBlockInlineMarkDto[] | null;
  tableRole?: string | null;
  parentStableBlockId?: string | null;
  stableBlockId?: string | null;
  /** L1: fenced code language for `code_block`, else `null`. */
  codeLanguage?: string | null;
  /** L1: header marker for `table_row` / `table_cell`, else `null`. */
  tableIsHeader?: boolean | null;
  /** L1: per-cell alignment for `table_cell`, else `null`. */
  tableAlignment?: "left" | "center" | "right" | "default" | null;
  /**
   * Automatic-policy projection (backend-only truth).
   * Expresses whether the system will *automatically* generate a layer —
   * never whether a job is loading / published / failed. Absent on legacy
   * snapshots (no semantic contract). UI must not re-derive role via regex.
   */
  contentRole?:
    | "prose"
    | "quotation"
    | "source_callout"
    | "citation_reference"
    | "prompt_question"
    | "link_only"
    | null;
  automaticLayerPolicy?: {
    translation: boolean;
    vocabulary: boolean;
    grammarNote: boolean;
    sentenceAnalysis: boolean;
  } | null;
}

export type ReaderSourceBlockChildNodeDto =
  | ReaderAnchorSegmentNodeDto
  | ReaderStableSeparatorLeafDto;

export interface ReaderAnchorSegmentNodeDto {
  type: "reader_anchor_segment";
  owner: "stable";
  base_id: string;
  unit_id: string;
  anchor_segment_id: string;
  sentence_id: string;
  segment_type: AnchorSegmentType;
  boundary_quality: ReaderBoundaryQuality;
  base_start_utf16: number;
  base_end_utf16: number;
  unit_start_utf16: number;
  unit_end_utf16: number;
  text_hash: string;
  hash_algorithm: typeof READER_TEXT_RANGE_HASH_ALGORITHM;
  children: ReaderStableSegmentTextLeafDto[];
}

export interface ReaderTranslationGroupNodeDto {
  type: "reader_translation_group";
  owner: "system_ai";
  layer_id: string;
  layer_version: number;
  base_id: string;
  unit_id: string;
  target_scope: "unit";
  target_key: string;
  group_id: string;
  covered_anchor_segment_ids: string[];
  source_text_hash: string;
  children: ReaderTranslationTextLeafDto[];
}

/**
 * @deprecated Legacy pre-group translation projection. Reader Record Plate
 * consumes `reader_translation_group`.
 */
export interface ReaderTranslationNodeDto {
  type: "reader_translation";
  owner: "system_ai";
  layer_id: string;
  layer_version: number;
  base_id: string;
  unit_id: string;
  target_scope: "unit" | "anchor_segment";
  target_key: string;
  target_language: string;
  confidence: TranslationConfidence;
  notes: string[];
  children: ReaderTranslationTextLeafDto[];
  anchor_segment_id?: string;
}

export interface ReaderSentenceAnalysisNodeDto {
  type: "reader_sentence_analysis";
  owner: "system_ai";
  analysis_id: string;
  layer_id: string;
  layer_version: number;
  base_id: string;
  unit_id: string;
  target_scope: "unit";
  target_key: string;
  anchor_segment_id: string;
  selected_text: string;
  label: string;
  analysis: string;
  chunks: ReaderSentenceAnalysisChunkDto[];
  children: ReaderSentenceAnalysisTextLeafDto[];
}

// ---------------------------------------------------------------------------
// Stable leaves (no `type` field — Plate text leaves)
// ---------------------------------------------------------------------------

export interface ReaderStableSeparatorLeafDto {
  text: string;
  owner: "stable";
  lock_source: true;
  source_role: "separator";
  base_start_utf16: number;
  base_end_utf16: number;
}

export interface ReaderStableSegmentTextLeafDto {
  text: string;
  owner: "stable";
  lock_source: true;
  source_role: "segment_text";
  base_start_utf16: number;
  base_end_utf16: number;
  anchor_segment_id: string;
  segment_start_utf16: number;
  segment_end_utf16: number;
  reader_vocabulary_marks?: ReaderVocabularyMarkDto[];
  reader_grammar_note_marks?: ReaderGrammarNoteMarkDto[];
}

export interface ReaderTranslationTextLeafDto {
  text: string;
}

export interface ReaderSentenceAnalysisTextLeafDto {
  text: string;
}

export interface ReaderVocabularyMarkBaseDto {
  mark_id: string;
  layer_id: string;
  item_type: VocabularyItemType;
  anchor_segment_id: string;
  start_offset: number;
  end_offset: number;
  selected_text: string;
  segment_start_utf16: number;
  segment_end_utf16: number;
  starts_here: boolean;
  ends_here: boolean;
}

export interface ReaderVocabHighlightMarkDto extends ReaderVocabularyMarkBaseDto {
  item_type: "vocab_highlight";
  headword: string;
  brief_explanation?: string | null;
  reason?: string | null;
}

export interface ReaderPhraseGlossMarkDto extends ReaderVocabularyMarkBaseDto {
  item_type: "phrase_gloss";
  phrase: string;
  phrase_type: VocabularyPhraseType;
  gloss: string;
  learning_note?: string | null;
  example?: string | null;
}

export interface ReaderContextGlossMarkDto extends ReaderVocabularyMarkBaseDto {
  item_type: "context_gloss";
  display: string;
  gloss: string;
  reason: string;
}

export type ReaderVocabularyMarkDto =
  | ReaderVocabHighlightMarkDto
  | ReaderPhraseGlossMarkDto
  | ReaderContextGlossMarkDto;

export interface ReaderGrammarNoteMarkDto {
  mark_id: string;
  item_id: string;
  owner: "system_ai";
  layer_id: string;
  item_type: "grammar_note";
  anchor_segment_id: string;
  start_offset: number;
  end_offset: number;
  selected_text: string;
  segment_start_utf16: number;
  segment_end_utf16: number;
  starts_here: boolean;
  ends_here: boolean;
  span_index: number;
  span_count: number;
  show_note_chip: boolean;
  grammar_point: string;
  pattern?: string | null;
  note: string;
}

// ---------------------------------------------------------------------------
// Events polling
// ---------------------------------------------------------------------------

export type ReaderEventType =
  | "article_ready"
  | "record_product_state_updated"
  | "layer_published"
  | "layer_failed"
  | "parsed_decision_updated"
  | "record_state_changed"
  | "action_required"
  | "run_completed"
  | "record_superseded"
  | "projection_ops"
  | "projection_reset_required";

export interface ReaderEventResponseDto {
  id: string;
  reading_record_id: string;
  sequence: number;
  event_type: ReaderEventType;
  payload: Record<string, unknown>;
  source_run_id?: string | null;
  source_job_id?: string | null;
  source_layer_id?: string | null;
  created_at: string;
}

export interface ReaderEventPollResponseDto {
  reading_record_id: string;
  after_sequence: number;
  next_after_sequence: number;
  last_event_sequence: number;
  has_more: boolean;
  truncated: boolean;
  reload_required: boolean;
  reload_reason?: string | null;
  events: ReaderEventResponseDto[];
}

// ---------------------------------------------------------------------------
// POST /reader/records/input — unified input submit (text / Markdown / file ref)
//
// Mirrors `ReaderUnifiedInputSubmit*` in
// `services/api/app/schemas/reader_orchestration.py`. Response is a typed
// union discriminated by `outcome`. The frontend MUST branch on `outcome`
// before reading outcome-specific fields.
// ---------------------------------------------------------------------------

export type ReaderInputAdapterSourceTypeDto =
  | "pasted_text"
  | "txt_file"
  | "markdown_file"
  | "ocr_text"
  | "pdf_text"
  | "url_text";

export type ReaderUnifiedInputSubmitOutcomeDto =
  | "stable_document_ready"
  | "candidate_document_required"
  | "input_rejected_or_action_required";

export type ReaderSourceLossFlagDto =
  | "non_english_or_mixed_language"
  | "too_short_for_learning"
  | "too_long_requires_envelope"
  | "layout_order_uncertain"
  | "ocr_low_confidence"
  | "table_structure_uncertain"
  | "image_ocr_uncertain"
  | "footnote_or_caption_merged"
  | "document_block_degraded"
  | "code_dominant"
  | "link_list_dominant"
  | "markdown_complex_structure";

export interface ReaderInputSuitabilityResultDto {
  outcome: ReaderUnifiedInputSubmitOutcomeDto;
  source_type: ReaderInputAdapterSourceTypeDto;
  word_count: number;
  english_word_ratio: number;
  natural_language_score: number;
  flags: ReaderSourceLossFlagDto[];
  reasons: string[];
  normalized_preview: string;
}

export interface ReaderUnifiedInputSubmitRequestDto {
  source_type: ReaderInputAdapterSourceTypeDto;
  text: string;
  filename?: string | null;
  source_metadata?: Record<string, unknown> | null;
  client_record_id?: string | null;
  language?: string | null;
  reading_goal?: ReaderOrchestrationReadingGoalDto;
  reading_variant?: ReaderOrchestrationReadingVariantDto;
}

export interface ReaderUnifiedInputSubmitStableResponseDto {
  outcome: "stable_document_ready";
  reading_record_id: string;
  stable_document_id: string;
  base_id: string;
  record_generation: number;
  document_version: number;
  title: string | null;
  content_sha256: string;
  canonical_text_sha256: string;
  block_count: number;
  article_ready_event_id: string;
  article_ready_sequence: number;
  suitability: ReaderInputSuitabilityResultDto;
  snapshot: ReaderPlateSnapshotDto;
}

export type ReaderCandidateDocumentStatusDto =
  | "ready"
  | "confirmed"
  | "rejected"
  | "superseded";

export interface ReaderUnifiedInputSubmitCandidateResponseDto {
  outcome: "candidate_document_required";
  reading_record_id: string;
  candidate_document_id: string;
  original_input_id: string;
  record_generation: number;
  status: ReaderCandidateDocumentStatusDto;
  title: string | null;
  block_count: number;
  source_type: ReaderInputAdapterSourceTypeDto;
  filename: string | null;
  suitability: ReaderInputSuitabilityResultDto;
}

export interface ReaderUnifiedInputSubmitRejectedResponseDto {
  outcome: "input_rejected_or_action_required";
  suitability: ReaderInputSuitabilityResultDto;
}

export type ReaderUnifiedInputSubmitResponseDto =
  | ReaderUnifiedInputSubmitStableResponseDto
  | ReaderUnifiedInputSubmitCandidateResponseDto
  | ReaderUnifiedInputSubmitRejectedResponseDto;

// ---------------------------------------------------------------------------
// GET/PUT /reader/records/{record_id}/confirmed-source — L2 Confirmed Source
//
// Frozen contract:
// `docs/initiatives/reader-agentic-orchestration/modules/schema-and-domain-contract.md` — Confirmed Source 生命周期.
// The backend L2 endpoints are being implemented by another owner; the web
// client is developed against this frozen shape with a mock BFF.
// ---------------------------------------------------------------------------

export type ReaderAdaptationClassificationDto =
  | "silent"
  | "adaptation_notice"
  | "content_check";

/**
 * Mirrors `AdaptationRecord` in
 * `services/api/app/schemas/reader_input_adapter.py` (L1, landed).
 */
export interface ReaderAdaptationRecordDto {
  code: string;
  message: string;
  classification: ReaderAdaptationClassificationDto;
}

export type ReaderConfirmedSourceEditSourceDto =
  | "initial"
  | "extraction"
  | "wysiwyg"
  | "source_mode"
  | "content_check";

export type ReaderConfirmedSourceStatusDto = "draft" | "frozen";

export interface ReaderConfirmedSourceCandidateSummaryDto {
  candidate_document_id: string;
  status: ReaderCandidateDocumentStatusDto;
  canonical_text_preview: string;
}

/** 200 body of `GET /reader/records/{record_id}/confirmed-source` (design — Confirmed Source 生命周期 / “GET draft / resume 语义”). */
export interface ReaderConfirmedSourceReadResponseDto {
  source_document_id: string;
  record_generation: number;
  revision: number;
  status: ReaderConfirmedSourceStatusDto;
  markdown_text: string;
  content_sha256: string;
  edit_source: ReaderConfirmedSourceEditSourceDto;
  updated_at: string;
  candidate: ReaderConfirmedSourceCandidateSummaryDto | null;
  /**
   * 正式合同（真实后端已落地，与 mock 假设一致）：GET 200 始终携带这三个
   * 字段（后端 DTO 默认值 quality={}、adaptation_notice=[]、content_check=[]，
   * 取自最新 candidate 的 quality_json.suitability.adaptations）。TS 侧保持
   * 可选以容忍联调期旧响应，BFF sanitize 统一归一为 []。
   */
  quality?: Record<string, unknown> | null;
  adaptation_notice?: ReaderAdaptationRecordDto[];
  content_check?: ReaderAdaptationRecordDto[];
}

/** Request body of `PUT /reader/records/{record_id}/confirmed-source` (design — Confirmed Source 生命周期 / “PUT whole-document update”). */
export interface ReaderConfirmedSourceUpdateRequestDto {
  expected_revision: number;
  markdown_text: string;
  edit_source: ReaderConfirmedSourceEditSourceDto;
}

export type ReaderConfirmedSourceUpdateOutcomeDto =
  | "candidate_document_required"
  | "stable_document_ready"
  | "input_rejected_or_action_required"
  /**
   * 同 hash 幂等 no-op（合同 “PUT whole-document update”）：revision 不推进、candidate
   * 未 supersede。前端视为良性成功：清 dirty，不重取、不报错。
   */
  | "idempotent_noop";

/** 200 body of `PUT /reader/records/{record_id}/confirmed-source` (合同 “PUT whole-document update”). */
export interface ReaderConfirmedSourceUpdateResponseDto {
  revision: number;
  content_sha256: string;
  outcome: ReaderConfirmedSourceUpdateOutcomeDto;
  candidate: ReaderConfirmedSourceCandidateSummaryDto | null;
  /**
   * `_candidate_quality_json` 超集（creation version + suitability 五元组，
   * 合同 “PUT whole-document update”）。rejected outcome 的原因通道在 `quality.suitability.reasons`
   * 与 `content_check`，真实后端不下发顶层 suitability 字段。
   */
  quality: Record<string, unknown> | null;
  adaptation_notice: ReaderAdaptationRecordDto[];
  content_check: ReaderAdaptationRecordDto[];
}

/**
 * 409 error codes from the confirmed-source endpoints (合同 “GET draft / resume 语义” / “PUT whole-document update” / “`expected_revision` 乐观并发”):
 * - `record_state_advanced` (GET): product state left needs_confirmation;
 *   `resolution: "open_reader"`.
 * - `stale_source_revision` (PUT): optimistic-concurrency miss; body carries
 *   `current_revision`, `resolution: "reload"`; recoverable by re-GET and
 *   replaying the edit against the new revision.
 * - `source_frozen` (PUT): source already frozen; `resolution: "open_reader"`.
 * - `stale_candidate_revision` (confirm POST): the candidate references an
 *   outdated source revision; `resolution: "reload"`.
 */
export type ReaderConfirmedSourceConflictCodeDto =
  | "record_state_advanced"
  | "stale_source_revision"
  | "source_frozen"
  | "stale_candidate_revision";

// ---------------------------------------------------------------------------
// Source artifacts: init-upload / complete-upload / submit-input / pipeline-status
//
// Mirrors `ReaderSourceArtifact*` and `ReaderArtifactPipeline*` schemas in
// `services/api/app/schemas/reader_orchestration.py` plus
// `services/api/app/schemas/reader_input_adapter.py`.
//
// The init-upload response never includes the AccessKey secret. A presigned
// URL may carry the AccessKey id in the query string per the OSS presigned
// model — the id is not a secret.
// ---------------------------------------------------------------------------

export type ReaderSourceArtifactKindDto =
  | "original_upload"
  | "pdf_page_image"
  | "ocr_result"
  | "extracted_text"
  | "webpage_snapshot"
  | "derived_preview";

export type ReaderSourceArtifactStorageProviderDto = "oss" | "local";
export type ReaderSourceArtifactStatusDto =
  | "pending"
  | "available"
  | "failed"
  | "deleted";
export type ReaderSourceArtifactUploadMethodDto =
  | "oss_put_object_pending_credentials"
  | "oss_put_object_presigned";

export interface ReaderSourceArtifactUploadInitRequestDto {
  artifact_kind: "original_upload";
  source_filename?: string | null;
  content_type?: string | null;
  byte_size?: number | null;
  content_sha256?: string | null;
  reading_record_id?: string | null;
  original_input_id?: string | null;
  source_refs?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
  quality?: Record<string, unknown> | null;
}

export interface ReaderSourceArtifactUploadInitResponseDto {
  artifact_id: string;
  artifact_kind: ReaderSourceArtifactKindDto;
  storage_provider: ReaderSourceArtifactStorageProviderDto;
  bucket: string;
  endpoint: string;
  object_key: string;
  status: ReaderSourceArtifactStatusDto;
  content_type: string | null;
  byte_size: number | null;
  content_sha256: string | null;
  source_filename: string;
  upload_method: ReaderSourceArtifactUploadMethodDto;
  headers: Record<string, string>;
  presigned_url: string | null;
  presigned_method: "PUT" | null;
  presigned_expires_at: string | null;
}

export interface ReaderSourceArtifactUploadCompleteRequestDto {
  content_type?: string | null;
  byte_size?: number | null;
  content_sha256?: string | null;
  metadata?: Record<string, unknown> | null;
  quality?: Record<string, unknown> | null;
}

export interface ReaderSourceArtifactUploadCompleteResponseDto {
  artifact_id: string;
  artifact_kind: ReaderSourceArtifactKindDto;
  storage_provider: ReaderSourceArtifactStorageProviderDto;
  bucket: string;
  endpoint: string;
  object_key: string;
  status: ReaderSourceArtifactStatusDto;
  content_type: string | null;
  byte_size: number | null;
  content_sha256: string | null;
  source_filename: string;
  upload_completed: true;
  idempotent_noop: boolean;
}

export type ReaderArtifactInputSourceTypeDto = "file" | "pdf" | "image";
export type ReaderArtifactOriginalInputTypeDto = "file_ref" | "image_ref";

export interface ReaderSourceArtifactSubmitInputRequestDto {
  title?: string | null;
  language?: string | null;
  client_record_id?: string | null;
  source_metadata?: Record<string, unknown> | null;
  reading_goal?: ReaderOrchestrationReadingGoalDto;
  reading_variant?: ReaderOrchestrationReadingVariantDto;
}

export interface ReaderSourceArtifactSubmitInputResponseDto {
  reading_record_id: string;
  original_input_id: string;
  artifact_id: string;
  record_generation: number;
  source_type: ReaderArtifactInputSourceTypeDto;
  input_type: ReaderArtifactOriginalInputTypeDto;
  product_state: ReadingRecordProductState;
  readiness_state: ReadingRecordReadinessState;
  title: string;
  language: string | null;
  extraction_required: true;
  bucket: string;
  endpoint: string;
  object_key: string;
  content_type: string | null;
  byte_size: number | null;
  content_sha256: string | null;
  source_filename: string;
  extraction_job_id: string;
  extraction_job_status: string;
}

export type ReaderArtifactPipelineOutcomeDto =
  | "upload_pending"
  | "upload_available_not_submitted"
  | "extraction_queued"
  | "extraction_running"
  | "extraction_retry_later"
  | "extraction_failed"
  | "materialization_queued"
  | "materialization_running"
  | "materialization_retry_later"
  | "materialization_failed"
  | "stable_document_ready"
  | "candidate_document_required"
  | "input_rejected_or_action_required";

export type ReaderArtifactPipelineNextActionDto =
  | "complete_upload"
  | "submit_input"
  | "wait_for_worker"
  | "retry_later"
  | "show_error"
  | "open_reader"
  | "confirm_candidate_document"
  | "revise_input";

export interface ReaderArtifactPipelineArtifactSummaryDto {
  artifact_id: string;
  status: string;
  artifact_kind: string;
  storage_provider: string;
  bucket: string | null;
  endpoint: string | null;
  object_key: string;
  content_type: string | null;
  byte_size: number | null;
  content_sha256: string | null;
  source_filename: string | null;
  reading_record_id: string | null;
  original_input_id: string | null;
}

export interface ReaderArtifactPipelineRecordSummaryDto {
  reading_record_id: string;
  generation: number;
  product_state: ReadingRecordProductState;
  readiness_state: ReadingRecordReadinessState;
  active_base_id: string | null;
  source_type: string;
  title: string | null;
  language: string | null;
}

export interface ReaderArtifactPipelineOriginalInputSummaryDto {
  original_input_id: string;
  input_type: string;
  content_sha256: string;
  has_source_text: boolean;
  extraction_status: string | null;
  metadata: Record<string, unknown>;
}

export interface ReaderArtifactPipelineJobSummaryDto {
  job_id: string;
  status: string;
  attempt_count: number;
  max_attempts: number;
  // DEBUG-ONLY — must NOT be rendered to end users.
  failure_class: string | null;
  // DEBUG-ONLY — must NOT be rendered to end users.
  failure_code: string | null;
  // DEBUG-ONLY — must NOT be rendered to end users.
  rationale_code: string | null;
  available_at: string;
  updated_at: string;
}

export interface ReaderArtifactPipelineCandidateDocumentDto {
  candidate_document_id: string;
  record_generation: number;
  canonical_text_preview: string;
}

export interface ReaderArtifactPipelineStableDocumentDto {
  stable_document_id: string;
  base_id: string;
  record_generation: number;
  content_sha256: string;
  canonical_text_sha256: string;
}

export interface ReaderArtifactPipelineStatusResponseDto {
  artifact: ReaderArtifactPipelineArtifactSummaryDto;
  record: ReaderArtifactPipelineRecordSummaryDto | null;
  original_input: ReaderArtifactPipelineOriginalInputSummaryDto | null;
  extraction_job: ReaderArtifactPipelineJobSummaryDto | null;
  materialization_job: ReaderArtifactPipelineJobSummaryDto | null;
  candidate_document: ReaderArtifactPipelineCandidateDocumentDto | null;
  stable_document: ReaderArtifactPipelineStableDocumentDto | null;
  outcome: ReaderArtifactPipelineOutcomeDto;
  next_action: ReaderArtifactPipelineNextActionDto;
}

// ---------------------------------------------------------------------------
// Candidate document confirmation
//
// Mirrors `ReaderCandidateDocumentConfirm*` in
// `services/api/app/schemas/reader_orchestration.py`.
// ---------------------------------------------------------------------------

export interface ReaderCandidateDocumentConfirmRequestDto {
  language?: string | null;
}

export interface ReaderCandidateDocumentConfirmResponseDto {
  reading_record_id: string;
  candidate_document_id: string;
  stable_document_id: string;
  base_id: string;
  record_generation: number;
  document_version: number;
  content_sha256: string;
  canonical_text_sha256: string;
  block_count: number;
  candidate_confirmed: boolean;
  freeze_idempotent_noop: boolean;
  article_ready_event_id: string;
  article_ready_sequence: number;
  snapshot: ReaderPlateSnapshotDto;
}

// ---------------------------------------------------------------------------
// Candidate Recovery read DTO (mirror of upstream ReaderCandidateDocument*)
// ---------------------------------------------------------------------------

export type ReaderCandidateDocumentPreviewMode =
  | "full_text"
  | "truncated_preview"
  | "outline_only";

export type ReaderCandidateDocumentBlockTypeLabel =
  | "heading"
  | "paragraph"
  | "list"
  | "quote"
  | "code"
  | "other";

export type ReaderCandidateDocumentRiskKind =
  | "low_confidence_ocr"
  | "short_content"
  | "language_mixed"
  | "encoding_warning"
  | "structure_fragmented"
  | "other";

export type ReaderCandidateDocumentRiskSeverity = "info" | "warning";

export type ReaderCandidateDocumentSourceType =
  | "plain_text"
  | "markdown"
  | "file_ref"
  | "url"
  | "image_ref";

export interface ReaderCandidateDocumentOutlineItem {
  order_index: number;
  block_type_label: ReaderCandidateDocumentBlockTypeLabel;
  heading_text: string | null;
  char_count: number;
}

export interface ReaderCandidateDocumentRiskItem {
  risk_kind: ReaderCandidateDocumentRiskKind;
  user_message: string;
  severity: ReaderCandidateDocumentRiskSeverity;
}

export interface ReaderCandidateDocumentPreview {
  preview_mode: ReaderCandidateDocumentPreviewMode;
  preview_text: string;
  is_truncated: boolean;
  total_char_count: number;
  document_outline: ReaderCandidateDocumentOutlineItem[];
  risk_items: ReaderCandidateDocumentRiskItem[];
}

export interface ReaderCandidateDocumentReadResponse {
  record_id: string;
  candidate_document_id: string;
  record_generation: number;
  status: "ready";
  title: string | null;
  preview: ReaderCandidateDocumentPreview;
  source_type: ReaderCandidateDocumentSourceType;
  filename: string | null;
  source_label: string;
  created_at: string;
  updated_at: string;
}

export type ReaderCandidateDocumentConflictCode =
  | "record_state_advanced"
  | "multiple_ready_candidates";

export type ReaderCandidateDocumentConflictResolution =
  | "open_reader"
  | "return_to_library";

export interface ReaderCandidateDocumentConflictBody {
  ok: false;
  code: ReaderCandidateDocumentConflictCode;
  resolution: ReaderCandidateDocumentConflictResolution;
  message: string;
}

export interface ReaderCandidateDocumentNotFoundBody {
  ok: false;
  code: "not_found";
  message: string;
}

// ---------------------------------------------------------------------------
// Stable Document projection — GET /reader/records/{record_id}/stable-document
//
// Mirrors `ReaderStableDocument*` in
// `services/api/app/schemas/reader_orchestration.py`. `base.text`,
// `blocks[*].text_content`, `canonical_text_*` and `anchor_segments` are
// the only citation / anchor truth sources the frontend may consume.
// Plate JSON, Slate path and DOM selection are NOT truth.
// ---------------------------------------------------------------------------

export interface ReaderStableDocumentBaseDto {
  base_id: string;
  content_sha256: string;
  content_utf16_length: number;
  canonicalizer_version: string;
  builder_version: string;
  segmenter_version: string;
  language: string | null;
  title_snapshot: string | null;
  navigation: Record<string, unknown>;
  text: string;
}

export interface ReaderStableDocumentMetadataDto {
  stable_document_id: string;
  document_version: number;
  title: string | null;
  language: string | null;
  source_profile: Record<string, unknown>;
  content_sha256: string;
  status: string;
}

export interface ReaderStableDocumentBlockDto {
  block_id: string;
  parent_block_id: string | null;
  order_index: number;
  block_type: string;
  text_content: string | null;
  payload: Record<string, unknown>;
  source_refs: Record<string, unknown>;
  quality: Record<string, unknown>;
  canonical_text_start_utf16: number | null;
  canonical_text_end_utf16: number | null;
  interpretation_policy: Record<string, unknown>;
}

export interface ReaderStableDocumentBlockNodeDto
  extends ReaderStableDocumentBlockDto {
  semantic_contract_version?: string | null;
  content_role?: string | null;
  automatic_layer_policy?: Record<string, boolean> | null;
  automatic_layer_policy_resolver_version?: string | null;
  unit_id: string | null;
  anchor_segment_ids: string[];
  children: ReaderStableDocumentBlockNodeDto[];
}

export interface ReaderStableDocumentAnchorSegmentDto {
  anchor_segment_id: string;
  unit_id: string;
  order_index: number;
  segment_type: string;
  base_start_utf16: number;
  base_end_utf16: number;
  text_hash: string;
}

export interface ReaderStableDocumentResponseDto {
  reading_record_id: string;
  record_generation: number;
  active_base_id: string;
  base: ReaderStableDocumentBaseDto;
  stable_document: ReaderStableDocumentMetadataDto;
  blocks: ReaderStableDocumentBlockDto[];
  anchor_segments: ReaderStableDocumentAnchorSegmentDto[];
}

// ---------------------------------------------------------------------------
// Structured Source rendering contract (G0 frozen)
//
// Mirrors the parser-output contract in
// `services/api/tests/fixtures/markdown_structured_source/CONTRACT.md`.
//
// This is the wire shape emitted by the structured Markdown source parser
// adapter (markdown_it_py / v1 / commonmark_gfm_v1). It is distinct from the
// frozen `ReaderStableDocumentBlockDto` above: the BFF DTO carries
// `payload` + `canonical_text_*_utf16` (post-freeze citation truth), while
// the Structured Source contract carries `payload_json` + `source_range`
// (parser diagnostic + identity envelope) and is the input to the
// `structured-source-renderer` projection.
//
// The existing `ReaderStableDocumentBlockDto.block_type: string` is
// intentionally NOT narrowed — legacy frozen documents and the
// stable-document-resolver continue to read the wide string. The literal
// union below is the renderer's view of the G0 closed block-type set.
//
// Reference: docs/initiatives/reader-agentic-orchestration/modules/markdown-adaptation-state.md §4 / §5 合同与 Fixture
// ---------------------------------------------------------------------------

export type ReaderStructuredSourceBlockType =
  | "heading"
  | "paragraph"
  | "blockquote"
  | "thematic_break"
  | "list"
  | "list_item"
  | "code_block"
  | "table"
  | "table_row"
  | "table_cell"
  | "footnote";

export interface ReaderStructuredSourceRange {
  /** 1-based start line in normalized source. */
  line_start: number;
  /** 1-based end line (inclusive). */
  line_end: number;
  /**
   * UTF-16 code unit offset against the canonical text. `null` in the first
   * phase (M1 fills when canonical text layer integration lands).
   */
  utf16_start?: number | null;
  utf16_end?: number | null;
}

export interface ReaderStructuredSourceLink {
  text: string;
  /** Safe href (http / https / mailto only — protocol whitelist enforced by parser). */
  href: string;
}

export interface ReaderStructuredSourceStrippedLink {
  text: string;
  /** Original href that was stripped (e.g. `javascript:...`). For diagnostics only. */
  href: string;
  reason: string;
}

export interface ReaderStructuredSourceInlineMark {
  /** Inline mark kind (emphasis / strong / strikethrough / inline_code / link). */
  kind:
    | "emphasis"
    | "strong"
    | "strikethrough"
    | "inline_code"
    | "link";
  /** Plain-text fragment covered by this mark. */
  text: string;
  /** Safe href for `kind === "link"` (whitelist-filtered). */
  href?: string;
}

export interface ReaderStructuredSourceWarning {
  code:
    | "raw_html_block"
    | "inline_html"
    | "has_unclosed_fence"
    | "unsafe_link_protocol"
    | "footnote_reference"
    | "strikethrough_extension"
    | "mermaid_static_only"
    | "code_dominant"
    | "missing_source_range";
  message: string;
  blocks_freeze: boolean;
}

export interface ReaderStructuredSourceUnsupported {
  code:
    | "raw_html"
    | "unsafe_link_sanitization"
    | "footnote_full_semantics";
  message: string;
}

export type ReaderStructuredSourceOutcome =
  | "stable_document_ready"
  | "candidate_document_required"
  | "input_rejected_or_action_required";

export interface ReaderStructuredSourceDiagnostic {
  fixture_name?: string;
  warnings: ReaderStructuredSourceWarning[];
  unsupported: ReaderStructuredSourceUnsupported[];
  outcome: ReaderStructuredSourceOutcome;
}

/**
 * Structured Source block — parser-output shape (G0 fixture contract).
 *
 * `payload_json` carries per-block metadata:
 *   - heading: `{ "level": 1|2|3|4|5|6 }`
 *   - list: `{ "ordered": bool, "depth": int }`
 *   - list_item: `{ "ordered": bool, "marker": str, "ordinal": int|null, "depth": int }`
 *   - table: `{ "alignments": ("default"|"left"|"center"|"right")[], "column_count"?: int }`
 *   - table_row: `{ "is_header": bool, "row_index": int }`
 *   - table_cell: `{ "column_index": int, "alignment": "default"|"left"|"center"|"right", "is_header": bool }`
 *   - code_block: `{ "language"?: str, "fenced"?: bool, "closed"?: bool }`
 *   - paragraph (with links): `{ "links"?: Link[], "stripped_links"?: StrippedLink[] }`
 *   - paragraph (raw html extraction): `{ "extracted_from"?: "html_block"|"html_inline" }`
 *   - footnote: `{ "footnote_id": str }`
 *
 * `inline_marks` is OPTIONAL in the first-phase fixture contract — when
 * absent the renderer falls back to rendering `text_content` verbatim. The
 * G0 frozen fixtures do not emit `inline_marks`; this field is reserved for
 * a future parser-version bump that preserves inline span boundaries
 * without re-parsing raw Markdown on the client.
 */
export interface ReaderStructuredSourceBlock {
  block_id: string;
  parent_block_id: string | null;
  order_index: number;
  block_type: ReaderStructuredSourceBlockType;
  text_content: string | null;
  payload_json: Record<string, unknown>;
  source_range: ReaderStructuredSourceRange;
  inline_marks?: ReaderStructuredSourceInlineMark[];
}

export interface ReaderStructuredSourceEnvelope {
  parser_name: string;
  parser_version: string;
  profile: string;
  blocks: ReaderStructuredSourceBlock[];
  diagnostic: ReaderStructuredSourceDiagnostic;
}

// ---------------------------------------------------------------------------
// Article RAG Index lifecycle — status / ensure
//
// Mirrors `ReaderArticleRagIndex*` in
// `services/api/app/schemas/reader_orchestration.py`. `reason_code` is
// DEBUG-ONLY and MUST NOT be rendered to end users. The frontend coerces
// unknown `status` values to a safe fallback (see status-mapper).
// ---------------------------------------------------------------------------

export type ReaderArticleRagIndexLifecycleStatusDto =
  | "not_ready"
  | "not_indexed"
  | "queued"
  | "indexing"
  | "indexed"
  | "failed"
  | "superseded_or_stale"
  | "unavailable";

export type ReaderArticleRagIndexEnsureStatusDto =
  | "enqueued"
  | "idempotent_noop"
  | "not_ready"
  | "no_active_base"
  | "generation_mismatch"
  | "record_not_found"
  | "plan_hash_mismatch"
  | "bootstrap_inconsistent"
  | "error";

export interface ReaderArticleRagIndexStatusResponseDto {
  reading_record_id: string;
  status: ReaderArticleRagIndexLifecycleStatusDto;
  stable_document_id: string | null;
  base_id: string | null;
  record_generation: number | null;
  index_run_id: string | null;
  index_version: string | null;
  plan_content_sha256: string | null;
  chunk_count: number | null;
  // DEBUG-ONLY — must NOT be rendered to end users.
  reason_code: string | null;
}

export interface ReaderArticleRagIndexEnsureRequestDto {
  expected_generation: number;
  index_version?: string | null;
}

export interface ReaderArticleRagIndexEnsureResponseDto {
  reading_record_id: string;
  status: ReaderArticleRagIndexEnsureStatusDto;
  // DEBUG-ONLY — must NOT be rendered to end users.
  reason_code: string;
  idempotent_noop: boolean;
  stable_document_id: string | null;
  base_id: string | null;
  record_generation: number | null;
  index_run_id: string | null;
  job_id: string | null;
  index_version: string | null;
  chunker_version: string | null;
}

// ---------------------------------------------------------------------------
// POST /reader/records/{record_id}/section-translation
//
// Mirrors `ReaderSectionTranslation*` in
// `services/api/app/schemas/reader_orchestration.py`. The body carries the
// full section range witness only; `record_id` / `base_id` / `generation`
// / `layer_family` are server-authoritative and MUST NOT appear in the
// request. `node_id` and `outline_revision` are audit-only and never
// sufficient for admission. The response is leak-safe: no prompt, provider
// payload, envelope or secret is ever echoed.
// ---------------------------------------------------------------------------

export type ReaderSectionTranslationOutcomeDto =
  | "succeeded"
  | "retry_later"
  | "already_covered_or_inflight"
  | "budget_exhausted"
  | "rejected"
  | "superseded";

export interface ReaderSectionTranslationRequestDto {
  start_unit_id: string;
  end_unit_id: string;
  start_anchor_segment_id?: string | null;
  end_anchor_segment_id?: string | null;
  /** Audit-only; never sufficient for admission. */
  node_id?: string | null;
  /** Audit-only; never sufficient for admission. */
  outline_revision?: string | null;
}

export interface ReaderSectionTranslationResponseDto {
  outcome: ReaderSectionTranslationOutcomeDto;
  job_id: string | null;
  /** Stable reason code for diagnostics; never an exception message. */
  detail: string | null;
}
