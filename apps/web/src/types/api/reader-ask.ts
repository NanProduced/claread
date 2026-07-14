export type ReaderAskAnchorTypeDto =
  | "sentence"
  | "text_range"
  | "multi_text"
  | "sentence_entry"
  | "user_annotation"
  | "reader_note"
  | "dictionary_entry";

export type ReaderAskMessageRoleDto = "user" | "assistant" | "system";
export type ReaderAskMessageStatusDto = "pending" | "streaming" | "completed" | "failed" | "interrupted";
export type ReaderAskCitationKindDto =
  | "anchor"
  | "vocabulary"
  | "dictionary_entry"
  | "dictionary_ai";
export type ReaderAskActionTypeDto =
  | "save_note"
  | "save_highlight"
  | "create_supplement_grammar_note";
export type ReaderAskActionStatusDto = "pending" | "executing" | "confirmed" | "executed" | "rejected";
export type ReaderAskToolStatusDto = "started" | "completed" | "failed";
export type ReaderAskResolvedIntentDto =
  | "explain"
  | "breakdown"
  | "vocabulary"
  | "grammar"
  | "practice"
  | "general";
export type ReaderAskClarificationModeDto =
  | "none"
  | "must_clarify"
  | "can_answer_with_followup";
export type ReaderAskReferenceResolutionStatusDto =
  | "not_needed"
  | "resolved"
  | "ambiguous"
  | "not_found";
export type ReaderAskEntryActionDto =
  | "ask_about_this"
  | "explain_this"
  | "why_here"
  | "lookup_in_context";
export type ReaderAskAttachmentKindDto =
  | "text_selection"
  | "annotation_ref"
  | "analysis_ref"
  | "supplement_ref"
  | "record_ref";
export type ReaderAskSupplementTypeDto = "grammar_note";
export type ReaderAskSupplementLifecycleStatusDto = "candidate" | "persisted" | "deleted";
export type ReaderAskEvidenceKindDto =
  | "attachment"
  | "citation"
  | "resolved_reference"
  | "supplement_candidate"
  | "clarification"
  | "disambiguation_candidate";
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
  has_reader_notes: boolean;
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
  record_id?: string | null;
  record_title?: string | null;
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
  /** BFF-only field used by Reading Record Ask routes; never forwarded to generic Reader Ask upstream. */
  reading_record_anchor?: Record<string, unknown> | null;
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
  result_json?: Record<string, unknown> | null;
}

export interface ReaderAskToolTraceEntryDto {
  tool_name: string;
  status: ReaderAskToolStatusDto;
  started_at?: string | null;
  completed_at?: string | null;
  input_summary?: string | null;
  summary?: string | null;
  next_actions: string[];
  artifacts: string[];
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
  used_cross_record_context: boolean;
  current_sentence_used: boolean;
  current_paragraph_used: boolean;
  used_record_insights: boolean;
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
  used_cross_record_context: boolean;
  cross_record_context_reason?: string | null;
  used_record_context: boolean;
  record_context_reason?: string | null;
  used_record_insights: boolean;
  record_insights_reason?: string | null;
  used_article_overview: boolean;
  article_overview_reason?: string | null;
  used_dictionary: boolean;
  dictionary_reason?: string | null;
  external_record_context_reason?: string | null;
  structured_asset_lookup_reason?: string | null;
  external_asset_selection_reason?: string | null;
  clarification_reason?: string | null;
  source_labels: string[];
}

export interface ReaderAskCurrentRecordContextDto {
  record_id: string;
  record_title?: string | null;
  local_context?: Record<string, unknown> | null;
  record_insights: Record<string, unknown>[];
  article_overview?: string | null;
  article_overview_status?: string | null;
  article_overview_source?: string | null;
  article_overview_confidence?: string | null;
  source_labels: string[];
}

export interface ReaderAskExternalRecordContextDto {
  record_id: string;
  record_title?: string | null;
  article_overview?: string | null;
  article_overview_status?: string | null;
  article_overview_source?: string | null;
  article_overview_confidence?: string | null;
  record_insights: string[];
  source_labels: string[];
  reason?: string | null;
}

export interface ReaderAskExternalAssetContextDto {
  record_id: string;
  record_title?: string | null;
  asset_type: "analysis" | "supplement";
  asset_id: string;
  entry_type?: string | null;
  asset_title?: string | null;
  content_md?: string | null;
  content_summary?: string | null;
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
  external_asset_contexts: ReaderAskExternalAssetContextDto[];
}

export interface ReaderAskRunInfoDto {
  turn_id: string;
  run_id: string;
  run_attempt: number;
  supersedes_run_id?: string | null;
}

export interface ReaderAskDisambiguationCandidateDto {
  record_id: string;
  title?: string | null;
  updated_at?: string | null;
  overview_hint?: string | null;
}

export interface ReaderAskDisambiguationDto {
  required: boolean;
  reason?: string | null;
  query?: string | null;
  selection_mode: "panel_cards";
  candidates: ReaderAskDisambiguationCandidateDto[];
}

export interface ReaderAskAssetDisambiguationCandidateDto {
  asset_type: "analysis" | "supplement";
  asset_id: string;
  entry_type?: string | null;
  title?: string | null;
  summary?: string | null;
}

export interface ReaderAskAssetDisambiguationDto {
  required: boolean;
  reason?: string | null;
  record_id?: string | null;
  record_title?: string | null;
  candidates: ReaderAskAssetDisambiguationCandidateDto[];
}

export interface ReaderAskTraceSummaryDto {
  planner_mode:
    | "direct_answer"
    | "needs_local_clarification"
    | "partial_answer_with_followup"
    | "known_reference_resolved"
    | "known_reference_ambiguous"
    | "known_reference_not_found";
  reference_resolution_status: ReaderAskReferenceResolutionStatusDto;
  working_set_mode: ReaderAskWorkingSetModeDto;
  used_known_reference_resolution: boolean;
  used_external_record_context: boolean;
  used_structured_asset_lookup: boolean;
  used_hitp_disambiguation: boolean;
  used_external_asset_context: boolean;
  used_external_asset_disambiguation: boolean;
  supplement_generation_used: boolean;
  supplement_persisted_count: number;
  supplement_deleted_count: number;
  cross_record_context_allowed: boolean;
  cross_record_context_used: boolean;
  tool_steps: string[];
  notes: string[];
}

export interface ReaderAskContextRecordItemDto {
  record_id: string;
  title?: string | null;
  updated_at?: string | null;
  overview_hint?: string | null;
  overview_hint_status?: string | null;
  overview_hint_source?: string | null;
}

export interface ReaderAskContextRecordSearchResponseDto {
  items: ReaderAskContextRecordItemDto[];
}

export interface ReaderAskSupplementCandidateDto {
  candidate_id: string;
  supplement_type: ReaderAskSupplementTypeDto;
  lifecycle_status: "candidate";
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

export interface ReaderAskPersistedSupplementDto {
  supplement_id: string;
  supplement_type: ReaderAskSupplementTypeDto;
  lifecycle_status: Extract<ReaderAskSupplementLifecycleStatusDto, "persisted" | "deleted">;
  record_id: string;
  record_title?: string | null;
  target_key: string;
  sentence_id: string;
  paragraph_id?: string | null;
  title: string;
  content: string;
  source_kind: "assistant_supplement";
  schema_version: string;
  created_from_turn_run_id: string;
  created_at?: string | null;
}

export interface ReaderAskSentenceBreakdownPartDto {
  label: string;
  text: string;
  note?: string | null;
}

export interface ReaderAskGrammarNoteCardSpanDto {
  text: string;
  role?: string | null;
}

export interface ReaderAskGrammarNoteCardDto {
  card_type: "grammar_note_card";
  sentence_text: string;
  focus_text: string;
  label: string;
  note_zh: string;
  spans: ReaderAskGrammarNoteCardSpanDto[];
  analysis_scope: "focus_span" | "full_sentence";
  origin: "ask_ai";
}

export interface ReaderAskSentenceBreakdownCardDto {
  card_type: "sentence_breakdown_card";
  sentence_text: string;
  translation_zh?: string | null;
  main_clause?: string | null;
  analysis_zh?: string | null;
  parts: ReaderAskSentenceBreakdownPartDto[];
  origin: "ask_ai";
}

export interface ReaderAskFollowUpSuggestionDto {
  label: string;
  prompt: string;
}

export type ReaderAskResponseCardDto =
  | ReaderAskGrammarNoteCardDto
  | ReaderAskSentenceBreakdownCardDto;

export interface ReaderAskMessageDto {
  id: string;
  thread_id: string;
  role: ReaderAskMessageRoleDto;
  status: ReaderAskMessageStatusDto;
  content_md: string;
  submission_mode?: "chat" | "quick_action";
  resolved_intent?: ReaderAskResolvedIntentDto | null;
  context_anchors: ReaderAskAnchorRefDto[];
  citations: ReaderAskCitationDto[];
  action_proposals: ReaderAskActionProposalDto[];
  tool_trace: ReaderAskToolTraceEntryDto[];
  evidence: ReaderAskEvidenceItemDto[];
  trace_summary?: ReaderAskTraceSummaryDto | null;
  disambiguation?: ReaderAskDisambiguationDto | null;
  external_asset_disambiguation?: ReaderAskAssetDisambiguationDto | null;
  response_cards: ReaderAskResponseCardDto[];
  resolved_context?: ReaderAskResolvedContextSummaryDto | null;
  context_plan?: ReaderAskContextPlanDto | null;
  resolved_context_input?: ReaderAskResolvedContextInputDto | null;
  run_info?: ReaderAskRunInfoDto | null;
  supplement_candidates: ReaderAskSupplementCandidateDto[];
  persisted_supplements: ReaderAskPersistedSupplementDto[];
  reasoning_md?: string | null;
  reasoning_status?: "idle" | "streaming" | "completed" | null;
  follow_up_suggestions?: ReaderAskFollowUpSuggestionDto[] | null;
  usage_event_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReaderAskMessageUiStateDto {
  replan_status?: "idle" | "replanning" | null;
  compacting?: boolean | null;
  regenerate_preview?: boolean | null;
  /**
   * UI-safe Ask article RAG sidecar. Frontend-only projection of the raw
   * `article_rag` field on {@link ReaderAskCompletedPayloadDto}. MUST be
   * produced via `mapAskArticleRagSidecar` so debug-only fields are
   * stripped and unknown statuses are coerced. `null` means the sidecar
   * was not present on the completed payload (silent fallback).
   */
  article_rag?: ReaderAskArticleRagSidecarSafeDto | null;
  /**
   * Frontend-only storage for agentic Reading Record Ask evidence from
   * {@link ReaderAskAgenticCompletedPayloadDto}. Distinct from legacy
   * {@link ReaderAskEvidenceItemDto} and from `article_rag`. Project to
   * display items via `projectAgenticEvidenceForDisplay` before render.
   * `null` / empty means no agentic evidence for this message.
   */
  agentic_evidence?: ReaderAskAgenticEvidenceItemDto[] | null;
}

export type ReaderAskUiMessageDto = ReaderAskMessageDto & ReaderAskMessageUiStateDto;

export interface ReaderAskThreadSummaryDto {
  id: string;
  record_id: string;
  title?: string | null;
  is_default: boolean;
  selected_model?: ReaderAskSelectedModelDto | null;
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

export interface ReaderAskSelectedModelDto {
  key: string;
  label: string;
  description?: string | null;
  model_name?: string | null;
  replan_model_name?: string | null;
  price_multiplier: number;
}

export interface ReaderAskModelOptionSummaryDto extends ReaderAskSelectedModelDto {
  is_default: boolean;
}

export interface ReaderAskModelOptionListResponseDto {
  default_key: string;
  items: ReaderAskModelOptionSummaryDto[];
}

export interface ReaderAskActionConfirmResponseDto {
  ok: boolean;
  action_id: string;
  status: ReaderAskActionStatusDto;
  result: {
    note_id?: string | null;
    annotation_id?: string | null;
    annotation_type?: string | null;
    target_key?: string | null;
    record_id?: string | null;
    supplement_projection?: Record<string, unknown> | null;
    persisted_supplement?: ReaderAskPersistedSupplementDto | null;
  };
}

export interface ReaderAskDeleteSupplementResponseDto {
  deleted: boolean;
  supplement_id: string;
  record_id: string;
  target_key?: string | null;
  lifecycle_status: "deleted";
  persisted_supplement?: ReaderAskPersistedSupplementDto | null;
}

export interface ReaderAskCompletedPayloadDto {
  id: string;
  thread_id: string;
  content_md: string;
  submission_mode?: "chat" | "quick_action";
  resolved_intent?: ReaderAskResolvedIntentDto | null;
  citations: ReaderAskCitationDto[];
  action_proposals: ReaderAskActionProposalDto[];
  tool_trace: ReaderAskToolTraceEntryDto[];
  evidence: ReaderAskEvidenceItemDto[];
  trace_summary?: ReaderAskTraceSummaryDto | null;
  disambiguation?: ReaderAskDisambiguationDto | null;
  external_asset_disambiguation?: ReaderAskAssetDisambiguationDto | null;
  response_cards: ReaderAskResponseCardDto[];
  usage_summary?: Record<string, unknown> | null;
  billed_points: number;
  resolved_context: ReaderAskResolvedContextSummaryDto;
  context_plan?: ReaderAskContextPlanDto | null;
  resolved_context_input?: ReaderAskResolvedContextInputDto | null;
  run_info?: ReaderAskRunInfoDto | null;
  supplement_candidates: ReaderAskSupplementCandidateDto[];
  persisted_supplements: ReaderAskPersistedSupplementDto[];
  reasoning_md?: string | null;
  reasoning_status?: "idle" | "streaming" | "completed" | null;
  follow_up_suggestions?: ReaderAskFollowUpSuggestionDto[] | null;
  usage_event_id?: string | null;
  article_rag?: ReaderAskArticleRagSidecarDto | null;
  article_rag_citations?: ReaderAskArticleRagCitationDto[] | null;
}

export interface ReaderAskThreadCreateRequestDto {
  record_id: string;
  title?: string | null;
  model?: string | null;
}

export interface ReaderAskMessageStreamRequestDto {
  content: string;
  page_identity: ReaderAskPageIdentityDto;
  attachments: ReaderAskAttachmentDto[];
  entry_action: ReaderAskEntryActionDto;
  model?: string | null;
}

export interface ReaderAskMessageRetryRequestDto {
  model?: string | null;
}

export interface ReaderAskActionConfirmRequestDto {
  confirmed: boolean;
}

export type ReaderAskStreamEventName =
  | "thread.ready"
  | "message.started"
  | "message.delta"
  | "reasoning.started"
  | "reasoning.delta"
  | "reasoning.completed"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "context.compacting"
  | "replan.started"
  | "message.interrupted"
  | "message.completed"
  // Agentic Reading Record Ask — emitted only when
  // `reader_record_ask_agentic_enabled` is on. Legacy clients may ignore these.
  | "agentic.run_started"
  | "agentic.progress"
  | "agentic.terminal"
  | "error";

/**
 * Generic envelope kept for existing call sites that only need event+data.
 * Prefer {@link ReaderAskTypedStreamEnvelopeDto} when narrowing agentic payloads.
 */
export interface ReaderAskStreamEnvelopeDto<TData = Record<string, unknown>> {
  event: ReaderAskStreamEventName;
  data: TData;
}

// ---------------------------------------------------------------------------
// Agentic Reading Record Ask SSE contract
//
// Mirrors `services/api/app/schemas/reader_record_ask_stream.py`.
// `execution_version` is the wire discriminator vs legacy Ask payloads.
// Agentic `message.completed` carries answer_text/evidence (NOT content_md /
// article_rag). Non-ok terminals use message.interrupted + agentic.terminal
// and must never be treated as successful completions.
// ---------------------------------------------------------------------------

export const READER_ASK_AGENTIC_EXECUTION_VERSION =
  "reader_record_ask_agentic_v1" as const;

export type ReaderAskAgenticExecutionVersionDto =
  typeof READER_ASK_AGENTIC_EXECUTION_VERSION;

export type ReaderAskAgenticFinalStatusDto =
  | "ok"
  | "context_stale"
  | "invalid_citations"
  | "failed"
  | "cancelled";

export type ReaderAskAgenticEvidenceKindDto =
  | "initial_anchor"
  | "read_range"
  | "search_hit"
  | "observation";

export type ReaderAskAgenticRagSourceScopeDto = "main_reading_text" | "heading";

/** Public RAG citation fields safe for SSE / thread reload. */
export interface ReaderAskAgenticRagCitationDto {
  rag_substrate_id: string;
  index_run_id: string;
  index_version: string;
  plan_content_sha256: string;
  source_scope: ReaderAskAgenticRagSourceScopeDto;
  block_type: string;
  chunk_id: string;
  content_sha256: string;
  canonical_text_start_utf16: number;
  canonical_text_end_utf16: number;
  snippet: string;
  score?: number | null;
  stable_document_id: string;
  base_id: string;
  record_generation: number;
  block_ids: string[];
  unit_ids: string[];
  anchor_segment_ids: string[];
}

export interface ReaderAskAgenticEvidenceItemDto {
  handle_id: string;
  kind: ReaderAskAgenticEvidenceKindDto;
  source_tool: string;
  snippet?: string | null;
  unit_id?: string | null;
  anchor_segment_id?: string | null;
  rag_citation?: ReaderAskAgenticRagCitationDto | null;
}

/**
 * Agentic `message.completed` payload. Only emitted for final_status=ok.
 * Distinct from legacy {@link ReaderAskCompletedPayloadDto} (content_md/citations).
 */
export interface ReaderAskAgenticCompletedPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  final_status: "ok";
  answer_text: string;
  message_id: string;
  thread_id: string;
  turn_run_id: string;
  envelope_fingerprint: string;
  evidence: ReaderAskAgenticEvidenceItemDto[];
}

/** Non-ok terminal statuses only — `ok` belongs exclusively to message.completed. */
export type ReaderAskAgenticTerminalStatusDto = Exclude<
  ReaderAskAgenticFinalStatusDto,
  "ok"
>;

/**
 * Typed non-ok terminal (stale / invalid citations / cancelled / failed).
 * Emitted as both `agentic.terminal` and `message.interrupted`.
 * Never carries a displayable answer for stale/invalid paths.
 */
export interface ReaderAskAgenticTerminalPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  final_status: ReaderAskAgenticTerminalStatusDto;
  message_id?: string | null;
  thread_id?: string | null;
  turn_run_id?: string | null;
  envelope_fingerprint?: string | null;
  terminal_reason?: string | null;
  rejected_handles: string[];
}

export interface ReaderAskAgenticRunStartedPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  message_id: string;
  thread_id: string;
  turn_run_id: string;
  envelope_fingerprint: string;
  has_initial_selection: boolean;
}

/** Safe progress signal (no raw document text / tool args). */
export interface ReaderAskAgenticProgressPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  phase: string;
  summary: string;
}

/** Legacy interrupt payload (partial streamed answer). */
export interface ReaderAskInterruptedPayloadDto {
  content_md?: string;
  reasoning_md?: string | null;
  reasoning_status?: "idle" | "streaming" | "completed" | null;
}

/**
 * Discriminated stream envelope union.
 * Agentic-only events carry strict DTOs; shared event names keep loose data so
 * legacy partial payloads remain assignable at the transport boundary.
 */
export type ReaderAskTypedStreamEnvelopeDto =
  | {
      event: "agentic.run_started";
      data: ReaderAskAgenticRunStartedPayloadDto;
    }
  | {
      event: "agentic.progress";
      data: ReaderAskAgenticProgressPayloadDto;
    }
  | {
      event: "agentic.terminal";
      data: ReaderAskAgenticTerminalPayloadDto;
    }
  | {
      event: "message.completed";
      data:
        | ReaderAskCompletedPayloadDto
        | ReaderAskAgenticCompletedPayloadDto
        | Record<string, unknown>;
    }
  | {
      event: "message.interrupted";
      data:
        | ReaderAskInterruptedPayloadDto
        | ReaderAskAgenticTerminalPayloadDto
        | Record<string, unknown>;
    }
  | {
      event: Exclude<
        ReaderAskStreamEventName,
        | "agentic.run_started"
        | "agentic.progress"
        | "agentic.terminal"
        | "message.completed"
        | "message.interrupted"
      >;
      data: Record<string, unknown>;
    };

const READER_ASK_AGENTIC_EVIDENCE_KINDS = new Set<string>([
  "initial_anchor",
  "read_range",
  "search_hit",
  "observation",
]);

const READER_ASK_AGENTIC_TERMINAL_STATUSES = new Set<string>([
  "context_stale",
  "invalid_citations",
  "failed",
  "cancelled",
]);

function isReaderAskAgenticRagCitation(
  value: unknown,
): value is ReaderAskAgenticRagCitationDto {
  if (!value || typeof value !== "object") {
    return false;
  }
  const citation = value as Record<string, unknown>;
  return (
    typeof citation.rag_substrate_id === "string" &&
    typeof citation.index_run_id === "string" &&
    typeof citation.index_version === "string" &&
    typeof citation.plan_content_sha256 === "string" &&
    (citation.source_scope === "main_reading_text" ||
      citation.source_scope === "heading") &&
    typeof citation.block_type === "string" &&
    typeof citation.chunk_id === "string" &&
    typeof citation.content_sha256 === "string" &&
    typeof citation.canonical_text_start_utf16 === "number" &&
    typeof citation.canonical_text_end_utf16 === "number" &&
    typeof citation.snippet === "string" &&
    typeof citation.stable_document_id === "string" &&
    typeof citation.base_id === "string" &&
    typeof citation.record_generation === "number" &&
    Array.isArray(citation.block_ids) &&
    Array.isArray(citation.unit_ids) &&
    Array.isArray(citation.anchor_segment_ids)
  );
}

function isReaderAskAgenticEvidenceItem(
  value: unknown,
): value is ReaderAskAgenticEvidenceItemDto {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Record<string, unknown>;
  if (
    typeof item.handle_id !== "string" ||
    typeof item.kind !== "string" ||
    !READER_ASK_AGENTIC_EVIDENCE_KINDS.has(item.kind) ||
    typeof item.source_tool !== "string"
  ) {
    return false;
  }
  if (item.rag_citation == null) {
    return true;
  }
  return isReaderAskAgenticRagCitation(item.rag_citation);
}

export function isReaderAskAgenticCompletedPayload(
  data: unknown,
): data is ReaderAskAgenticCompletedPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  return (
    payload.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION &&
    payload.final_status === "ok" &&
    typeof payload.answer_text === "string" &&
    typeof payload.message_id === "string" &&
    typeof payload.thread_id === "string" &&
    typeof payload.turn_run_id === "string" &&
    typeof payload.envelope_fingerprint === "string" &&
    Array.isArray(payload.evidence) &&
    payload.evidence.every(isReaderAskAgenticEvidenceItem)
  );
}

export function isReaderAskAgenticTerminalPayload(
  data: unknown,
): data is ReaderAskAgenticTerminalPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  const status = payload.final_status;
  // agentic.terminal is non-ok only; final_status "ok" belongs exclusively to
  // message.completed and must never narrow as a terminal payload.
  return (
    payload.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION &&
    typeof status === "string" &&
    READER_ASK_AGENTIC_TERMINAL_STATUSES.has(status) &&
    Array.isArray(payload.rejected_handles) &&
    payload.rejected_handles.every((handle) => typeof handle === "string")
  );
}

export function isReaderAskAgenticRunStartedPayload(
  data: unknown,
): data is ReaderAskAgenticRunStartedPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  return (
    payload.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION &&
    typeof payload.message_id === "string" &&
    typeof payload.thread_id === "string" &&
    typeof payload.turn_run_id === "string" &&
    typeof payload.envelope_fingerprint === "string" &&
    typeof payload.has_initial_selection === "boolean"
  );
}

export function isReaderAskAgenticProgressPayload(
  data: unknown,
): data is ReaderAskAgenticProgressPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  return (
    payload.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION &&
    typeof payload.phase === "string" &&
    typeof payload.summary === "string"
  );
}

// ---------------------------------------------------------------------------
// Article RAG sidecar — `article_rag` block on Ask user-visible output.
//
// Mirrors `ReaderAskArticleRagSidecar` and `ReaderAskArticleRagCitation` in
// `services/api/app/schemas/reader_ask.py`. The `citation` field is the
// I4A 9-key truth pointer into Postgres-backed stable document facts;
// `failure_code` / `retryable` / `fallback_allowed` / `query_sha256` /
// `source_pack_hash` are DEBUG-ONLY and MUST NOT be rendered to end users.
// Frontend MUST coerce unknown `status` values to
// `not_indexed_or_unavailable` (see lib/reader-orchestration/status-mapper).
// ---------------------------------------------------------------------------

export type ReaderAskArticleRagStatusDto =
  | "available"
  | "empty"
  | "not_indexed_or_unavailable"
  | "composer_rejected"
  | "disabled"
  | "stale_due_to_repair";

export interface ReaderAskArticleRagCitationContentDto {
  reading_record_id: string;
  stable_document_id: string;
  base_id: string;
  record_generation: number;
  block_ids: string[];
  unit_ids: string[];
  anchor_segment_ids: string[];
  canonical_text_start_utf16: number;
  canonical_text_end_utf16: number;
}

export interface ReaderAskArticleRagCitationDto {
  context_id: string;
  chunk_id: string;
  citation: ReaderAskArticleRagCitationContentDto;
}

export interface ReaderAskArticleRagSidecarDto {
  status: ReaderAskArticleRagStatusDto;
  // DEBUG-ONLY — must NOT be rendered to end users.
  failure_code: string | null;
  // DEBUG-ONLY — must NOT be rendered to end users.
  retryable: boolean;
  // DEBUG-ONLY — must NOT be rendered to end users.
  fallback_allowed: boolean;
  should_attach: boolean;
  context_ids: string[];
  // DEBUG-ONLY — must NOT be rendered to end users.
  source_pack_hash: string | null;
  // DEBUG-ONLY — must NOT be rendered to end users.
  query_sha256: string | null;
  citations: ReaderAskArticleRagCitationDto[];
}

/**
 * UI-safe projection of {@link ReaderAskArticleRagSidecarDto}.
 *
 * This shape is produced by `mapAskArticleRagSidecar` in
 * `lib/reader-orchestration/status-mapper.ts`: every debug-only field
 * (`failure_code`, `retryable`, `fallback_allowed`, `source_pack_hash`,
 * `query_sha256`) is stripped at the type level so UI components cannot
 * accidentally read them off mapped state. `citations` is retained but
 * is only populated when `status === "available"`.
 */
export interface ReaderAskArticleRagSidecarSafeDto {
  status: ReaderAskArticleRagStatusDto;
  should_attach: boolean;
  context_ids: string[];
  citations: ReaderAskArticleRagCitationDto[];
}
