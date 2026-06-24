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
  | "sentence_analysis";

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
  | "collocation"
  | "phrasal_verb"
  | "idiom"
  | "proper_noun"
  | "compound"
  | "other";

export type ReaderPlateOwner =
  | "stable"
  | "system_ai"
  | "ask_supplement"
  | "user"
  | "ephemeral";

// ---------------------------------------------------------------------------
// Plain-text submit
// ---------------------------------------------------------------------------

export interface ReaderPlainTextSubmitRequestDto {
  plain_text: string;
  title?: string | null;
  language?: string | null;
  source_metadata?: Record<string, unknown> | null;
  client_record_id?: string | null;
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

export interface ReaderSnapshotRecordDto {
  title: string;
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

export interface TranslationLayerOutputDto {
  schema_version: 1;
  target_language: string;
  translated_text: string;
  notes: string[];
  confidence: TranslationConfidence;
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

export type ReaderSnapshotLayerDto =
  | ReaderTranslationSnapshotLayerDto
  | ReaderVocabularySnapshotLayerDto
  | ReaderGrammarNoteSnapshotLayerDto
  | ReaderSentenceAnalysisSnapshotLayerDto;

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
  | ReaderTranslationNodeDto
  | ReaderSentenceAnalysisNodeDto;

export interface ReaderSourceBlockNodeDto {
  type: "reader_source_block";
  owner: "stable";
  base_id: string;
  unit_id: string;
  base_start_utf16: number;
  base_end_utf16: number;
  children: ReaderSourceBlockChildNodeDto[];
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
