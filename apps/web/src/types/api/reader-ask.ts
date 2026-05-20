export type ReaderAskAnchorTypeDto =
  | "sentence"
  | "text_range"
  | "multi_text"
  | "sentence_entry"
  | "user_annotation"
  | "favorite"
  | "dictionary_entry";

export type ReaderAskMessageRoleDto = "user" | "assistant" | "system";
export type ReaderAskMessageStatusDto = "pending" | "streaming" | "completed" | "failed";
export type ReaderAskCitationKindDto =
  | "anchor"
  | "record_excerpt_asset"
  | "user_excerpt_asset"
  | "vocabulary"
  | "dictionary_entry"
  | "dictionary_ai";
export type ReaderAskActionTypeDto =
  | "save_note"
  | "save_excerpt"
  | "favorite_anchor"
  | "save_answer_note"
  | "create_supplement_grammar_note";
export type ReaderAskActionStatusDto = "pending" | "confirmed" | "executed" | "rejected";
export type ReaderAskToolStatusDto = "started" | "completed" | "failed";
export type ReaderAskResolvedIntentDto =
  | "explain"
  | "breakdown"
  | "vocabulary"
  | "grammar"
  | "practice";
export type ReaderAskReferenceResolutionStatusDto =
  | "not_needed"
  | "resolved"
  | "ambiguous"
  | "not_found";
export type ReaderAskEntryActionDto =
  | "ask_about_this"
  | "explain_this"
  | "why_here"
  | "lookup_in_context"
  | "compare_translation";
export type ReaderAskAttachmentKindDto =
  | "text_selection"
  | "annotation_ref"
  | "analysis_ref"
  | "supplement_ref"
  | "record_ref";
export type ReaderAskSupplementTypeDto = "grammar_note";
export type ReaderAskEvidenceKindDto =
  | "attachment"
  | "citation"
  | "resolved_reference"
  | "supplement_candidate"
  | "clarification";
export type ReaderAskEvidenceScopeDto = "current_record" | "external_record";
export type ReaderAskWorkingSetModeDto =
  | "anchor_local"
  | "article_overview"
  | "explicit_external_record"
  | "known_reference"
  | "clarification";

export interface ReaderAskAnchorSegmentDto {
  paragraph_id?: string | null;
  sentence_id: string;
  selected_text: string;
  start_offset: number;
  end_offset: number;
  text_hash: string;
}

export interface ReaderAskAnchorRefDto {
  anchor_type: ReaderAskAnchorTypeDto;
  anchor_id?: string | null;
  target_key?: string | null;
  target_type?: string | null;
  sentence_id?: string | null;
  paragraph_id?: string | null;
  entry_type?: string | null;
  label?: string | null;
  selected_text?: string | null;
  start_offset?: number | null;
  end_offset?: number | null;
  text_hash?: string | null;
  dict_entry_id?: number | null;
  query?: string | null;
  note?: string | null;
  segments: ReaderAskAnchorSegmentDto[];
  payload_json: Record<string, unknown>;
}

export interface ReaderAskPageIdentityDto {
  record_id: string;
  title?: string | null;
  surface: "reader";
  source: "reader_2_0";
  available_context_capabilities: string[];
  has_article_overview: boolean;
  has_sentence_entries: boolean;
  has_annotations: boolean;
  has_user_assets: boolean;
}

export interface ReaderAskAttachmentPayloadDto {
  anchor_type: Extract<ReaderAskAnchorTypeDto, "sentence" | "text_range" | "multi_text">;
  target_key?: string | null;
  record_id?: string | null;
  paragraph_id?: string | null;
  sentence_id?: string | null;
  selected_text?: string | null;
  start_offset?: number | null;
  end_offset?: number | null;
  text_hash?: string | null;
  segments: ReaderAskAnchorSegmentDto[];
}

export interface ReaderAskAttachmentMetadataDto {
  source_surface: string;
  entry_action?: ReaderAskEntryActionDto | null;
  sentence_id?: string | null;
  paragraph_id?: string | null;
  entry_id?: string | null;
  entry_type?: string | null;
  asset_id?: string | null;
  annotation_type?: string | null;
  start_offset?: number | null;
  end_offset?: number | null;
  translation_zh?: string | null;
  note?: string | null;
  title?: string | null;
  query?: string | null;
  lookup_text?: string | null;
  visual_tone?: string | null;
}

export interface ReaderAskAttachmentDto {
  kind: ReaderAskAttachmentKindDto;
  subtype: string;
  label: string;
  selected_text?: string | null;
  target_key?: string | null;
  anchor_payload?: ReaderAskAttachmentPayloadDto | null;
  metadata: ReaderAskAttachmentMetadataDto;
}

export interface ReaderAskCitationDto {
  citation_id: string;
  kind: ReaderAskCitationKindDto;
  label: string;
  anchor_type?: ReaderAskAnchorTypeDto | null;
  sentence_id?: string | null;
  target_key?: string | null;
  selected_text?: string | null;
  record_id?: string | null;
  source_article_title?: string | null;
  metadata_json: Record<string, unknown>;
}

export interface ReaderAskActionProposalDto {
  id: string;
  action_type: ReaderAskActionTypeDto;
  label: string;
  description?: string | null;
  requires_confirmation: boolean;
  status: ReaderAskActionStatusDto;
  payload_json: Record<string, unknown>;
}

export interface ReaderAskToolTraceEntryDto {
  tool_name: string;
  status: ReaderAskToolStatusDto;
  started_at?: string | null;
  completed_at?: string | null;
  summary?: string | null;
  metadata_json: Record<string, unknown>;
}

export interface ReaderAskEvidenceItemDto {
  kind: ReaderAskEvidenceKindDto;
  label: string;
  detail?: string | null;
  scope: ReaderAskEvidenceScopeDto;
  record_id?: string | null;
  record_title?: string | null;
  source_article_title?: string | null;
  reason?: string | null;
  target_key?: string | null;
  metadata_json: Record<string, unknown>;
}

export interface ReaderAskResolvedContextSummaryDto {
  record_id: string;
  record_title?: string | null;
  anchor_count: number;
  explicit_attachment_count: number;
  used_history_lookup: boolean;
  current_sentence_used: boolean;
  current_paragraph_used: boolean;
  used_record_assets: boolean;
  used_dictionary: boolean;
  source_labels: string[];
}

export interface ReaderAskContextPlanDto {
  entry_action: ReaderAskEntryActionDto;
  explicit_attachment_count: number;
  normalized_anchor_count: number;
  primary_anchor_type?: ReaderAskAnchorTypeDto | null;
  reference_query?: string | null;
  reference_resolution_attempted: boolean;
  reference_resolution_status: ReaderAskReferenceResolutionStatusDto;
  reference_resolution_reason?: string | null;
  expanded_record_ids: string[];
  used_history_lookup: boolean;
  history_lookup_reason?: string | null;
  used_record_context: boolean;
  record_context_reason?: string | null;
  used_record_insights: boolean;
  record_insights_reason?: string | null;
  used_article_overview: boolean;
  article_overview_reason?: string | null;
  used_dictionary: boolean;
  dictionary_reason?: string | null;
  clarification_reason?: string | null;
  source_labels: string[];
}

export interface ReaderAskCurrentRecordContextDto {
  record_id: string;
  record_title?: string | null;
  local_context?: Record<string, unknown> | null;
  record_insights: Record<string, unknown>[];
  article_overview?: string | null;
  source_labels: string[];
}

export interface ReaderAskExternalRecordContextDto {
  record_id: string;
  record_title?: string | null;
  article_overview?: string | null;
  source_labels: string[];
  reason?: string | null;
}

export interface ReaderAskResolvedContextInputDto {
  page_identity: ReaderAskPageIdentityDto;
  entry_action: ReaderAskEntryActionDto;
  attachments: ReaderAskAttachmentDto[];
  normalized_anchors: ReaderAskAnchorRefDto[];
  current_record_context?: ReaderAskCurrentRecordContextDto | null;
  external_record_contexts: ReaderAskExternalRecordContextDto[];
}

export interface ReaderAskRunInfoDto {
  turn_id: string;
  run_id: string;
  run_attempt: number;
  supersedes_run_id?: string | null;
}

export interface ReaderAskTraceSummaryDto {
  planner_mode:
    | "direct_answer"
    | "needs_local_clarification"
    | "known_reference_resolved"
    | "known_reference_ambiguous"
    | "known_reference_not_found";
  reference_resolution_status: ReaderAskReferenceResolutionStatusDto;
  working_set_mode: ReaderAskWorkingSetModeDto;
  used_known_reference_resolution: boolean;
  used_external_record_context: boolean;
  history_lookup_allowed: boolean;
  history_lookup_used: boolean;
  tool_steps: string[];
  notes: string[];
}

export interface ReaderAskContextRecordItemDto {
  record_id: string;
  title?: string | null;
  updated_at?: string | null;
}

export interface ReaderAskContextRecordSearchResponseDto {
  items: ReaderAskContextRecordItemDto[];
}

export interface ReaderAskSupplementCandidateDto {
  candidate_id: string;
  supplement_type: ReaderAskSupplementTypeDto;
  target_key: string;
  sentence_id: string;
  paragraph_id?: string | null;
  title: string;
  content: string;
  anchor: ReaderAskAnchorRefDto;
  schema_version: string;
  created_from_turn_run_id: string;
  label: string;
}

export interface ReaderAskSentenceBreakdownPartDto {
  label: string;
  text: string;
  note?: string | null;
}

export interface ReaderAskSentenceBreakdownCardDto {
  card_type: "sentence_breakdown_card";
  sentence_text: string;
  translation_zh?: string | null;
  main_clause?: string | null;
  analysis_zh?: string | null;
  parts: ReaderAskSentenceBreakdownPartDto[];
}

export interface ReaderAskVocabularyInContextCardDto {
  card_type: "vocabulary_in_context_card";
  query: string;
  display_word?: string | null;
  phonetic?: string | null;
  meaning_zh?: string | null;
  why_here?: string | null;
  translation_zh?: string | null;
  learning_tip?: string | null;
  source_sentence?: string | null;
}

export interface ReaderAskPracticeCardDto {
  card_type: "practice_card";
  title: string;
  prompt: string;
  expected_focus?: string | null;
  hints: string[];
  answer_guidance?: string | null;
  source_sentence?: string | null;
}

export type ReaderAskResponseCardDto =
  | ReaderAskSentenceBreakdownCardDto
  | ReaderAskVocabularyInContextCardDto
  | ReaderAskPracticeCardDto;

export interface ReaderAskMessageDto {
  id: string;
  thread_id: string;
  role: ReaderAskMessageRoleDto;
  status: ReaderAskMessageStatusDto;
  content_md: string;
  resolved_intent?: ReaderAskResolvedIntentDto | null;
  context_anchors: ReaderAskAnchorRefDto[];
  citations: ReaderAskCitationDto[];
  action_proposals: ReaderAskActionProposalDto[];
  tool_trace: ReaderAskToolTraceEntryDto[];
  evidence: ReaderAskEvidenceItemDto[];
  trace_summary?: ReaderAskTraceSummaryDto | null;
  response_cards: ReaderAskResponseCardDto[];
  resolved_context?: ReaderAskResolvedContextSummaryDto | null;
  context_plan?: ReaderAskContextPlanDto | null;
  resolved_context_input?: ReaderAskResolvedContextInputDto | null;
  run_info?: ReaderAskRunInfoDto | null;
  supplement_candidates: ReaderAskSupplementCandidateDto[];
  usage_event_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReaderAskThreadSummaryDto {
  id: string;
  record_id: string;
  title?: string | null;
  is_default: boolean;
  archived_at?: string | null;
  created_at: string;
  updated_at: string;
  last_message_at?: string | null;
}

export interface ReaderAskThreadDetailDto extends ReaderAskThreadSummaryDto {
  messages: ReaderAskMessageDto[];
}

export interface ReaderAskThreadListResponseDto {
  items: ReaderAskThreadSummaryDto[];
}

export interface ReaderAskActionConfirmResponseDto {
  ok: boolean;
  action_id: string;
  status: ReaderAskActionStatusDto;
  result: Record<string, unknown>;
}

export interface ReaderAskCompletedPayloadDto {
  id: string;
  thread_id: string;
  content_md: string;
  resolved_intent?: ReaderAskResolvedIntentDto | null;
  citations: ReaderAskCitationDto[];
  action_proposals: ReaderAskActionProposalDto[];
  tool_trace: ReaderAskToolTraceEntryDto[];
  evidence: ReaderAskEvidenceItemDto[];
  trace_summary?: ReaderAskTraceSummaryDto | null;
  response_cards: ReaderAskResponseCardDto[];
  usage_summary?: Record<string, unknown> | null;
  billed_points: number;
  resolved_context: ReaderAskResolvedContextSummaryDto;
  context_plan?: ReaderAskContextPlanDto | null;
  resolved_context_input?: ReaderAskResolvedContextInputDto | null;
  run_info?: ReaderAskRunInfoDto | null;
  supplement_candidates: ReaderAskSupplementCandidateDto[];
}

export interface ReaderAskThreadCreateRequestDto {
  record_id: string;
  mode?: "default" | "new_chat";
  title?: string | null;
}

export interface ReaderAskMessageStreamRequestDto {
  content: string;
  page_identity: ReaderAskPageIdentityDto;
  attachments: ReaderAskAttachmentDto[];
  entry_action: ReaderAskEntryActionDto;
  model?: string | null;
}

export interface ReaderAskActionConfirmRequestDto {
  confirmed: boolean;
}

export type ReaderAskStreamEventName =
  | "thread.ready"
  | "message.started"
  | "message.delta"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "message.completed"
  | "error";

export interface ReaderAskStreamEnvelopeDto<TData = Record<string, unknown>> {
  event: ReaderAskStreamEventName;
  data: TData;
}
