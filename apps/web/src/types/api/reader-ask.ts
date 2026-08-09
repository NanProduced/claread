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
  /**
   * Reasoning UI state shared by both lanes (no parallel state):
   * - legacy reader_ask `reasoning.*` uses idle/streaming/completed;
   * - agentic `agentic.reasoning.*` (ASK-REASONING-R1) additionally freezes
   *   session-visible partial reasoning as `interrupted` on cancel/failure
   *   terminals — cold history never carries interrupted reasoning.
   */
  reasoning_status?: "idle" | "streaming" | "completed" | "interrupted" | null;
  /**
   * ASK-LEARNER-REASONING-PROJECTOR-R1 — public learner summary fields.
   * Prefer these over ambiguous reasoning_md on agentic v2.
   */
  learner_reasoning_text?: string | null;
  learner_reasoning_status?: "streaming" | "completed" | null;
  learner_reasoning_stage?:
    | "analyzing"
    | "article"
    | "web"
    | "synthesizing"
    | null;
  /** Client-only sequence gate for replace snapshots (not a public API field). */
  learner_reasoning_sequence?: number | null;
  follow_up_suggestions?: ReaderAskFollowUpSuggestionDto[] | null;
  usage_event_id?: string | null;
  /**
   * Reading Record Agentic history only. Present on RR thread-detail when
   * the current turn_run.execution_version is reader_record_ask_agentic_v1.
   * Absent (exclude_none) for legacy RR / Analysis Ask messages — do not
   * treat missing fields as agentic.
   */
  execution_version?: ReaderAskAgenticExecutionVersionDto | null;
  /** Agentic finalizer status from history projection; absent on legacy. */
  final_status?: ReaderAskAgenticFinalStatusDto | null;
  /**
   * Wire agentic evidence from RR history DTO. Cold-load maps this into UI
   * state only after strict guard validation. Not present on legacy messages.
   */
  agentic_evidence?: ReaderAskAgenticEvidenceItemDto[] | null;
  /**
   * Message-level article identity for agentic evidence (R3B0).
   * Cold-load only after strict guard. Null/missing on old v1 rows means
   * Sources may still display, but all navigation must fail closed as
   * `unavailable.legacy_scope_missing` — no page-identity or rag_citation-only
   * fallback. UI wiring is R3B0-B / R3C (not this transport slice).
   */
  agentic_evidence_scope?: ReaderAskAgenticEvidenceScopeDto | null;
  /**
   * Semantic answer blocks with public message-local citation_ids (c1, c2…).
   * Hot SSE and cold history both populate this for completed agentic turns.
   * Null/missing on legacy. Never carries internal evidence handles.
   */
  agentic_answer_blocks?: ReaderAskAgenticAnswerBlockDto[] | null;
  /**
   * Public citations (citation_id + source_kind + optional snippet).
   * Hot SSE and cold history both populate this for completed agentic turns.
   * Null/missing on legacy. Never carries internal evidence handles.
   */
  agentic_citations?: ReaderAskAgenticCitationDto[] | null;
  created_at: string;
  updated_at: string;
}

/**
 * ASK-COT — one frozen agentic process step, captured from the pure
 * `reduceAgenticActivityEvent` output at turn settle time. Redeclared
 * here so the DTO layer never imports component modules.
 *
 * Privacy: no tool args, queries, URLs, evidence handles, fingerprints,
 * raw errors, or server summary copy (see agentic-activity.ts privacy
 * header). Step labels are fixed typed mapping at project time.
 *
 * Internal control fields (for deterministic same-session reprojection
 * only — never public DOM / view-model / server wire):
 * - `localOrdinal`, `sequence`, `toolName`, `activityId`, `attemptCount`,
 *   `callSequence`
 *
 * This type is UI-memory only; it is not a server DTO and must not be
 * written to the wire or cold history.
 */
export type AgenticProcessSnapshotStep = {
  /** Internal: local first-seen/synthetic order for stable reprojection. */
  localOrdinal: number;
  /** Internal: accepted server sequence (synthetic rows reuse the watermark). */
  sequence: number;
  phase:
    | "agent_running"
    | "analysis"
    | "reading_context"
    | "searching_article"
    | "searching_web"
    | "composing_answer"
    | "validating_evidence";
  activity: "started" | "completed" | "unavailable" | "failed";
  elapsedMs: number;
  /** Internal: tool identity for fold / upsert rules. */
  toolName:
    | "read_range"
    | "search_current_article"
    | "expand_evidence"
    | "search_web"
    | null;
  status: "running" | "ok" | "unavailable" | "failed" | null;
  /** Public provider-neutral result state; null while the step is started. */
  outcome: "success" | "empty" | "degraded" | "failed" | null;
  durationMs: number | null;
  /** Internal: web-search activity id for attempt upsert. */
  activityId: "article_evidence" | "web_search" | null;
  /** Internal: authoritative attempt count when present. */
  attemptCount: number | null;
  /** Internal: authoritative call sequence when present. */
  callSequence: number | null;
};

/**
 * ASK-COT — in-memory-only frozen snapshot of the agentic process for one
 * settled turn, written by AiWorkspacePanel when the stream ends (before
 * the live activity state resets to idle). Lets a completed/interrupted
 * bubble keep showing its typed process (Chain of Thought) until reload.
 *
 * - NEVER serialized to the server, DTO wire, or DOM (UI-state only).
 * - Cold history NEVER carries it: `normalizeReaderAskMessages` nulls it
 *   in both branches; a reloaded v2 turn renders reasoning-only (G3 —
 *   cold process steps are not persisted yet).
 * - Written whenever `run_started` was observed (turnRunId present), even
 *   with zero progress steps, so reasoning-only v2 turns stay detectable
 *   after settle (the hot path never sets `execution_version`).
 * - Steps retain internal control fields for reprojection; projected
 *   public view models strip them.
 */
export type AgenticProcessSnapshot = {
  execution_version: typeof READER_ASK_AGENTIC_EXECUTION_VERSION;
  status: "completed" | "failed" | "cancelled" | "running" | "degraded";
  elapsedMs: number;
  hasUnavailable: boolean;
  steps: AgenticProcessSnapshotStep[];
};

export type ReaderAskContextCompactionUiStatusDto =
  | "running"
  | "completed"
  | "fallback"
  | "failed";

/**
 * Same-session UI projection of context compaction. This is intentionally
 * narrow: provider detail codes and transcript data never enter the message
 * state or DOM. Cold history does not persist this process-only status.
 */
export interface ReaderAskContextCompactionUiStateDto {
  status: ReaderAskContextCompactionUiStatusDto;
  elapsedMs: number;
}

export interface ReaderAskMessageUiStateDto {
  replan_status?: "idle" | "replanning" | null;
  compacting?: boolean | null;
  context_compaction?: ReaderAskContextCompactionUiStateDto | null;
  regenerate_preview?: boolean | null;
  /**
   * ASK-TURN-LIFECYCLE R2 — provisional answer preview accumulated from
   * `message.delta` frames during streaming. Strictly separated from the
   * canonical {@link ReaderAskMessageDto.content_md}:
   *
   * - `provisional_content_md` accumulates deltas while streaming;
   *   `content_md` stays empty (or holds the previous canonical answer
   *   during a retry/regenerate cycle).
   * - On `message.completed` the validated `answer_text` atomically
   *   replaces `content_md` and `provisional_content_md` is cleared.
   * - On any non-committed terminal (failed / cancelled / interrupted /
   *   error / abort) `provisional_content_md` is dropped — never promoted
   *   to canonical. This prevents half answers from being preserved when
   *   the output validator fails or the user stops the turn.
   * - Cold history never carries `provisional_content_md`; reload always
   *   shows the committed canonical answer or nothing.
   *
   * Renderers must display `provisional_content_md` while streaming and
   * fall back to `content_md` once committed. `null` / empty means no
   * provisional preview is active (canonical should be used).
   */
  provisional_content_md?: string | null;
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
  /**
   * Hot/cold agentic evidence_scope for navigation fence. Transport field
   * only until R3B0-B wires AiWorkspacePanel state. Null/missing ⇒
   * legacy_scope_missing for all source navigation (including search_hit).
   */
  agentic_evidence_scope?: ReaderAskAgenticEvidenceScopeDto | null;
  /**
   * Semantic answer blocks with public citation_ids for inline render.
   * Null/missing means the completed payload predates blocks or is legacy.
   */
  agentic_answer_blocks?: ReaderAskAgenticAnswerBlockDto[] | null;
  /**
   * Public citations for InlineCitation (citation_id + snippet).
   * Null/missing means no verified citations for this message.
   * Article Sources list is not used for v2 article evidence.
   */
  agentic_citations?: ReaderAskAgenticCitationDto[] | null;
  /**
   * Turn-level web search summary (mirrors backend
   * `ReaderRecordAskCompletedDTO.web_search`). Hot SSE and cold history
   * both populate this for completed agentic turns. `null` / missing
   * means web search was not invoked this turn. Used by WebSources.
   */
  agentic_web_search?: ReaderAskWebSearchSummaryDto | null;
  /**
   * Frontend-only record of the user's web search request mode for this
   * turn. Set on the user message at send time so retry can replay the
   * original turn capability (not the current UI toggle state). Absent
   * on cold history (backend does not echo the request mode back); retry
   * falls back to inferring from `agentic_web_search` when missing.
   */
  web_search_mode?: WebSearchModeDto | null;
  /**
   * ASK-TURN-LIFECYCLE R3 — typed reasoning truncation flag. Mirrors the
   * backend `ReaderAskAgenticReasoningCompletedPayloadDto.truncated`
   * field. When `true`, the visible `reasoning_md` was truncated by the
   * server-side projection char cap; the UI must surface an explicit
   * "达到展示上限" indicator rather than embedding a marker in the body.
   *
   * - Hot path: set to `payload.truncated` when
   *   `agentic.reasoning.completed` arrives (and persisted alongside
   *   `reasoning_status: "completed"`).
   * - Cold path: backend persists this flag on the message row so a
   *   reload shows the same indicator (no marker in the body).
   * - `null` / `false` / missing ⇒ not truncated; render normally.
   */
  reasoning_truncated?: boolean | null;
  /**
   * ASK-COT — in-memory-only frozen snapshot of the agentic process for
   * this settled turn (see {@link AgenticProcessSnapshot}). Written by
   * AiWorkspacePanel when the SSE stream ends, before the live activity
   * state resets to idle, so completed/interrupted bubbles keep showing
   * their typed Chain of Thought until reload.
   *
   * - NEVER serialized to the server (UI-state only).
   * - Cold history NEVER carries it: `normalizeReaderAskMessages` nulls
   *   it in both branches; a reloaded v2 turn renders reasoning-only.
   * - `handleRetry` clears it when a new attempt starts.
   */
  agentic_process_snapshot?: AgenticProcessSnapshot | null;
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
  /**
   * ASK-WEB-G1-R2: server-declared Web Search capability for this model
   * option. ``"available"`` only when a real provider is wired via
   * the selected model's ResolvedModelConfig binding. The frontend
   * gates Search toggle visibility on this signal (in addition to the
   * page scope). Optional on the wire for backward compat with legacy
   * backends — defaults to ``"unavailable"`` when absent (fail-closed).
   */
  web_search_capability?: "unavailable" | "available";
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
  /**
   * User-visible web search request mode (mirrors backend `WebSearchMode`).
   * `allowed` only grants turn capability; it never forces a search.
   * Omitted / `disabled` means web search is not requested this turn.
   */
  web_search_mode?: WebSearchModeDto;
  /**
   * ASK-RETRY-CONTRACT-R2 — client-generated UUID for idempotent claim.
   * Same value re-submitted after a network blip must not create a second
   * user/assistant pair or re-call the model. Optional on legacy clients;
   * new web clients always send it.
   */
  client_submission_id?: string | null;
}

/**
 * ASK-RETRY-CONTRACT-R2 — typed reconciliation snapshot for a client
 * submission (after claim or on re-submit of the same id).
 */
/** Safe public message projection returned by reconcile hydrate (R5). */
export interface ReaderAskSubmissionPublicMessageDto {
  id: string;
  thread_id: string;
  role: "user" | "assistant" | "system";
  status: "pending" | "streaming" | "completed" | "failed" | "interrupted";
  content_md: string;
  reasoning_md?: string | null;
  reasoning_status?: "idle" | "streaming" | "completed" | null;
  reasoning_truncated?: boolean | null;
  citations?: unknown[];
  agentic_citations?: unknown[] | null;
  agentic_answer_blocks?: unknown[] | null;
  agentic_web_search?: unknown | null;
  execution_version?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ReaderAskSubmissionReconcileDto {
  client_submission_id: string;
  thread_id: string;
  status: "claimed" | "streaming" | "completed" | "failed" | "cancelled" | "not_found";
  user_message_id?: string | null;
  assistant_message_id?: string | null;
  /** When status is completed/failed/cancelled, a short typed reason code. */
  terminal_code?: string | null;
  claim_generation?: number | null;
  action_hint?: "resend" | "retry" | "reask" | "wait" | "none" | null;
  /** Full public projections — required for completed hydrate. */
  user_message?: ReaderAskSubmissionPublicMessageDto | null;
  assistant_message?: ReaderAskSubmissionPublicMessageDto | null;
}

export interface ReaderAskMessageRetryRequestDto {
  model?: string | null;
  // ASK-WEB-G1-R3: ``web_search_mode`` is intentionally absent. The
  // FastAPI ``ReaderAskMessageRetryRequest`` schema is ``extra="forbid"``
  // and only accepts ``model``; sending ``web_search_mode`` would 422.
  // The backend replays the persisted mode from the original user
  // message metadata after verifying message/thread/record/user
  // ownership — there is no client input for retry capability.
}

export interface ReaderAskActionConfirmRequestDto {
  confirmed: boolean;
}

export type ReaderAskStreamEventName =
  | "thread.ready"
  | "message.started"
  | "message.delta"
  | "message.preview_reset"
  | "reasoning.started"
  | "reasoning.delta"
  | "reasoning.completed"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "context.compacting"
  | "context.compaction.started"
  | "context.compaction.completed"
  | "context.compaction.failed"
  | "context.compaction.fallback"
  | "replan.started"
  | "message.interrupted"
  | "message.completed"
  // Agentic Reading Record Ask — emitted only when
  // `reader_record_ask_agentic_enabled` is on. Legacy clients may ignore these.
  | "agentic.run_started"
  | "agentic.progress"
  | "agentic.terminal"
  // ASK-REASONING-R1: safe reasoning projection (redacted + quota-bounded
  // server-side). Distinct from legacy `reasoning.*` (raw CoT passthrough
  // on the legacy reader_ask path). Clients append deltas verbatim — all
  // security filtering happens server-side.
  | "agentic.reasoning.started"
  | "agentic.reasoning.delta"
  | "agentic.reasoning.completed"
  // ASK-LEARNER-REASONING-PROJECTOR-R1 — learner stage summary (replace).
  | "agentic.learner_reasoning.snapshot"
  // ASK-RETRY-CONTRACT-R6: duplicate client_submission_id short-circuits
  // the model; payload is a public reconcile snapshot (no secrets).
  | "submission.reconcile"
  | "error";

/**
 * ASK-RETRY-CONTRACT-R6 — public SSE payload for `submission.reconcile`.
 * No internal metadata, query, provider payload, or handle.
 */
export interface ReaderAskSubmissionReconcileSseDto {
  client_submission_id: string;
  thread_id: string;
  status: ReaderAskSubmissionReconcileDto["status"];
  user_message_id?: string | null;
  assistant_message_id?: string | null;
  terminal_code?: string | null;
  action_hint?: ReaderAskSubmissionReconcileDto["action_hint"];
  claim_generation?: number | null;
}

/**
 * Agentic context-compaction lifecycle. `detail_code` is a server-whitelisted
 * diagnostic and must not be rendered or copied into message UI state.
 */
export interface ReaderAskContextCompactionPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  message_id: string;
  thread_id: string;
  turn_run_id: string;
  detail_code?: string | null;
  attempt_count: number;
  elapsed_ms: number;
}

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

/** Canonical public agentic execution version (SSE / history / completed). */
export const READER_ASK_AGENTIC_EXECUTION_VERSION =
  "reader_record_ask_agentic_v2" as const;
/** Historical cold-load only; never mint new production payloads as v1. */
export const READER_ASK_AGENTIC_EXECUTION_VERSION_V1 =
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
  | "observation"
  | "article_seed";

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
 * Message-level article identity for agentic evidence (mirrors backend
 * `ReaderRecordAskEvidenceScope`). Projected from Context Envelope only.
 *
 * Optional/null on completed payloads for historical v1 compatibility only.
 * New production always emits a complete object. Missing/null scope ⇒
 * navigation must return `unavailable.legacy_scope_missing` for every
 * evidence kind (including complete search_hit); do not use page identity
 * or envelope_fingerprint for navigation.
 */
export interface ReaderAskAgenticEvidenceScopeDto {
  reading_record_id: string;
  base_id: string;
  record_generation: number;
  stable_document_id: string | null;
}

export interface ReaderAskAgenticCitationDto {
  citation_id: string;
  source_kind: "article" | "web";
  snippet?: string | null;
  /**
   * Web-specific fields (mirrors backend `PublicCitation`). Required when
   * `source_kind === "web"`; ignored for article citations. v1 exposes only
   * url / title / optional description — no provider, query, rank, score,
   * or internal handle.
   */
  url?: string | null;
  title?: string | null;
  description?: string | null;
  /** Strict provider-supplied ISO publication date; null when unknown. */
  published_at?: string | null;
  /** Host-recorded retrieval timestamp; UI must label this as retrieved. */
  retrieved_at?: string | null;
}

export interface ReaderAskAgenticAnswerBlockDto {
  text: string;
  citation_ids: string[];
}

export type ReaderAskAgenticKnowledgeModeDto =
  | "article_grounded"
  | "general_knowledge"
  | "web_grounded"
  | "mixed";

export type ReaderAskAgenticSourceStatusDto = "article_source_unavailable";

export type ReaderAskAgenticLegacyClassificationDto = "legacy_unclassified";

/**
 * User-visible web search request mode (mirrors backend `WebSearchMode`).
 * `allowed` only grants turn capability; it never forces a search.
 */
export type WebSearchModeDto = "disabled" | "allowed";

/**
 * Closed outcome set for web search (mirrors backend `WebSearchOutcome`).
 * Used by both the fake-provider vertical slice and future real adapters.
 */
export type WebSearchOutcomeDto =
  | "completed"
  | "no_results"
  | "unavailable"
  | "failed"
  | "timeout";

export type ReaderAskAgenticProgressOutcomeDto =
  | "success"
  | "empty"
  | "degraded"
  | "failed";

/**
 * Turn-level web search outcome summary (mirrors backend
 * `PublicWebSearchSummary`). Carried on the completed DTO so hot SSE, DB
 * persistence, and cold history replay all observe the same state.
 *
 * `cited_source_count` counts message-local public web citations that
 * were actually attached to the answer — never the raw provider result
 * count.
 */
export interface ReaderAskWebSearchSummaryDto {
  outcome: WebSearchOutcomeDto;
  cited_source_count: number;
}

/**
 * Agentic `message.completed` payload. Only emitted for final_status=ok.
 * Public surface is no-evh: no handles, fingerprints, or raw evidence.
 * answer_blocks / citations are required arrays (may be empty for
 * source_unavailable). knowledge_mode / source_status are host-derived.
 */
export interface ReaderAskAgenticCompletedPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  final_status: "ok";
  answer_text: string;
  answer_blocks: ReaderAskAgenticAnswerBlockDto[];
  citations: ReaderAskAgenticCitationDto[];
  knowledge_mode: ReaderAskAgenticKnowledgeModeDto | null;
  source_status: ReaderAskAgenticSourceStatusDto | null;
  /**
   * Turn-level web search outcome summary (mirrors backend
   * `ReaderRecordAskCompletedDTO.web_search`). `null` means search was
   * not invoked this turn. Mutually independent from `citations`: a turn
   * may complete web search with no_results (summary set, no web
   * citations) or may have web citations (summary outcome=completed with
   * cited_source_count > 0).
   */
  web_search: ReaderAskWebSearchSummaryDto | null;
  message_id: string;
  thread_id: string;
  turn_run_id: string;
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
  terminal_reason?: string | null;
}

export interface ReaderAskAgenticRunStartedPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  message_id: string;
  thread_id: string;
  turn_run_id: string;
  has_initial_selection: boolean;
  /**
   * ASK-WEB-G1-R2: echoes the **resolved** web search capability mode
   * (not the raw request toggle). ``allowed`` only when a real provider
   * was wired and ``enabled_for_turn=True`` at send time. The frontend
   * gates Search toggle visibility/enablement on this signal, not on
   * ``isReadingRecordScope`` alone. Optional on the wire for backward
   * compat with legacy streams — defaults to ``"disabled"`` when
   * absent (fail-closed: Search toggle not visible).
   */
  web_search_mode?: WebSearchModeDto;
}

/** Safe progress signal (no raw document text / tool args). */
export interface ReaderAskAgenticProgressPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  phase: string;
  summary: string;
  sequence?: number;
  activity?: string | null;
  tool_name?: string | null;
  status?: string | null;
  /** Strict public outcome; absent is accepted only for legacy frames. */
  outcome?: ReaderAskAgenticProgressOutcomeDto | null;
  elapsed_ms?: number | null;
  duration_ms?: number | null;
  /** Stable activity identity for article evidence and Web Search retries. */
  activity_id?: "article_evidence" | "web_search" | null;
  /** Confirmed provider invocation count; null while a call is only started. */
  attempt_count?: number | null;
  /** Host tool invocation sequence within this search activity. */
  call_sequence?: number | null;
}

/**
 * ASK-REASONING-R1 reasoning projection payloads.
 *
 * `delta` is already projected by the server-side chokepoint (deterministic
 * redaction of internal handles / identity / auth material / system
 * fragments + host quota). Clients never filter — only append. `seq` is
 * strictly monotonic: started=0, deltas 1..n, completed=n+1.
 */
export interface ReaderAskAgenticReasoningStartedPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  message_id: string;
  thread_id: string;
  turn_run_id: string;
  seq: number;
  projection_policy_version: string;
}

export interface ReaderAskAgenticReasoningDeltaPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  message_id: string;
  thread_id: string;
  turn_run_id: string;
  seq: number;
  delta: string;
}

/**
 * Replayable completion promise — emitted only after the projection and
 * the answer were persisted in the same successful transaction, and
 * before `message.completed`. Never emitted on cancel / failure.
 */
export interface ReaderAskAgenticReasoningCompletedPayloadDto {
  execution_version: ReaderAskAgenticExecutionVersionDto;
  message_id: string;
  thread_id: string;
  turn_run_id: string;
  seq: number;
  has_content: boolean;
  truncated: boolean;
  projection_policy_version: string;
}

/** Legacy interrupt payload (partial streamed answer). */
export interface ReaderAskInterruptedPayloadDto {
  content_md?: string;
  reasoning_md?: string | null;
  reasoning_status?: "idle" | "streaming" | "completed" | null;
}

/**
 * ASK-LEARNER-REASONING-PROJECTOR-R1 — replace-semantics snapshot.
 * Host-owned identity + policy; only validated Chinese text is public.
 */
export interface ReaderAskLearnerReasoningSnapshotPayloadDto {
  execution_version: "reader_record_ask_agentic_v2";
  message_id: string;
  thread_id: string;
  turn_run_id: string;
  sequence: number;
  revision: number;
  generation_id: number;
  stage: "analyzing" | "article" | "web" | "synthesizing";
  text: string;
  basis?: Array<"article" | "web" | "general">;
  policy_version: "learner_reasoning_v1";
  projection_policy_version?: "learner_reasoning_v1";
}

export function isReaderAskLearnerReasoningSnapshotPayload(
  data: unknown
): data is ReaderAskLearnerReasoningSnapshotPayloadDto {
  if (!data || typeof data !== "object") return false;
  const d = data as Record<string, unknown>;
  if (typeof d.text !== "string" || !d.text.trim() || d.text.length > 80) {
    return false;
  }
  if (typeof d.sequence !== "number" || d.sequence < 1) return false;
  if (typeof d.revision !== "number" || d.revision < 1) return false;
  if (typeof d.generation_id !== "number" || d.generation_id < 0) return false;
  if (!Number.isInteger(d.generation_id)) return false;
  if (d.execution_version !== "reader_record_ask_agentic_v2") return false;
  if (
    d.stage !== "analyzing" &&
    d.stage !== "article" &&
    d.stage !== "web" &&
    d.stage !== "synthesizing"
  ) {
    return false;
  }
  if (typeof d.message_id !== "string" || !d.message_id) return false;
  if (typeof d.thread_id !== "string" || !d.thread_id) return false;
  if (typeof d.turn_run_id !== "string" || !d.turn_run_id) return false;
  const policy = d.policy_version ?? d.projection_policy_version;
  if (policy !== "learner_reasoning_v1") return false;
  return true;
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
      event: "agentic.reasoning.started";
      data: ReaderAskAgenticReasoningStartedPayloadDto;
    }
  | {
      event: "agentic.reasoning.delta";
      data: ReaderAskAgenticReasoningDeltaPayloadDto;
    }
  | {
      event: "agentic.reasoning.completed";
      data: ReaderAskAgenticReasoningCompletedPayloadDto;
    }
  | {
      event: "agentic.learner_reasoning.snapshot";
      data: ReaderAskLearnerReasoningSnapshotPayloadDto;
    }
  | {
      event:
        | "context.compaction.started"
        | "context.compaction.completed"
        | "context.compaction.failed"
        | "context.compaction.fallback";
      data: ReaderAskContextCompactionPayloadDto;
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
        | "agentic.reasoning.started"
        | "agentic.reasoning.delta"
        | "agentic.reasoning.completed"
        | "context.compaction.started"
        | "context.compaction.completed"
        | "context.compaction.failed"
        | "context.compaction.fallback"
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
  "article_seed",
]);

const READER_ASK_AGENTIC_TERMINAL_STATUSES = new Set<string>([
  "context_stale",
  "invalid_citations",
  "failed",
  "cancelled",
]);

const READER_ASK_AGENTIC_PROGRESS_OUTCOMES = new Set<string>([
  "success",
  "empty",
  "degraded",
  "failed",
]);

/**
 * Legal (kind, source_tool) pairs — mirrors backend
 * `LEGAL_EVIDENCE_KIND_SOURCE` in `services/api/app/services/reader_record_ask/evidence.py`.
 *
 * Rejects inconsistent combinations such as kind=article_seed +
 * source_tool=initial_anchor. The source_tool field on the wire is a string,
 * not a union, so this map is the single source of truth for cold/hot
 * evidence contract validation on the Web side.
 */
const READER_ASK_AGENTIC_LEGAL_KIND_SOURCE: ReadonlyMap<
  string,
  ReadonlySet<string>
> = new Map<string, ReadonlySet<string>>([
  ["initial_anchor", new Set(["initial_anchor"])],
  ["read_range", new Set(["read_range"])],
  ["search_hit", new Set(["search_current_article"])],
  [
    "observation",
    new Set(["initial_anchor", "read_range", "search_current_article"]),
  ],
  ["article_seed", new Set(["baseline_context"])],
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

export function isReaderAskAgenticEvidenceItem(
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
  // Strict legal-map check: kind ↔ source_tool must be a legal pair.
  // Mirrors backend `LEGAL_EVIDENCE_KIND_SOURCE` validator.
  const allowedSources = READER_ASK_AGENTIC_LEGAL_KIND_SOURCE.get(item.kind);
  if (!allowedSources || !allowedSources.has(item.source_tool)) {
    return false;
  }
  // rag_citation presence rules:
  // - search_hit MUST carry a complete rag_citation.
  // - All other kinds MUST NOT carry rag_citation.
  if (item.kind === "search_hit") {
    if (item.rag_citation == null) {
      return false;
    }
    return isReaderAskAgenticRagCitation(item.rag_citation);
  }
  // Non-search_hit kinds must not carry rag_citation at all.
  if (item.rag_citation != null) {
    return false;
  }
  return true;
}

/** Strict list guard for RR history / completed agentic evidence arrays. */
export function isReaderAskAgenticEvidenceList(
  value: unknown,
): value is ReaderAskAgenticEvidenceItemDto[] {
  return Array.isArray(value) && value.every(isReaderAskAgenticEvidenceItem);
}

/**
 * Strict runtime guard for message-level evidence_scope.
 *
 * - `null` / `undefined` are not accepted here (completed guard treats
 *   missing/null as old-v1-compatible before calling this).
 * - Exactly four allowlisted fields; extra keys rejected.
 * - `record_generation` must be a finite integer >= 1.
 * - `stable_document_id` may be null (RAG off / no active document).
 */
export function isReaderAskAgenticEvidenceScope(
  value: unknown,
): value is ReaderAskAgenticEvidenceScopeDto {
  if (!value || typeof value !== "object") {
    return false;
  }
  const scope = value as Record<string, unknown>;
  const keys = Object.keys(scope);
  if (keys.length !== 4) {
    return false;
  }
  for (const key of keys) {
    if (
      key !== "reading_record_id" &&
      key !== "base_id" &&
      key !== "record_generation" &&
      key !== "stable_document_id"
    ) {
      return false;
    }
  }
  if (typeof scope.reading_record_id !== "string" || scope.reading_record_id.length < 1) {
    return false;
  }
  if (typeof scope.base_id !== "string" || scope.base_id.length < 1) {
    return false;
  }
  if (
    typeof scope.record_generation !== "number" ||
    !Number.isInteger(scope.record_generation) ||
    !Number.isFinite(scope.record_generation) ||
    scope.record_generation < 1
  ) {
    return false;
  }
  if (
    scope.stable_document_id !== null &&
    typeof scope.stable_document_id !== "string"
  ) {
    return false;
  }
  if (
    typeof scope.stable_document_id === "string" &&
    scope.stable_document_id.length < 1
  ) {
    return false;
  }
  return true;
}

export function isReaderAskAgenticFinalStatus(
  value: unknown,
): value is ReaderAskAgenticFinalStatusDto {
  return (
    value === "ok" ||
    value === "context_stale" ||
    value === "invalid_citations" ||
    value === "failed" ||
    value === "cancelled"
  );
}

export function isReaderAskAgenticAnswerBlockList(
  value: unknown,
): value is ReaderAskAgenticAnswerBlockDto[] {
  if (!Array.isArray(value)) {
    return false;
  }
  return value.every((item) => {
    if (!item || typeof item !== "object") {
      return false;
    }
    const block = item as Record<string, unknown>;
    return (
      typeof block.text === "string" &&
      Array.isArray(block.citation_ids) &&
      block.citation_ids.every((id) => typeof id === "string")
    );
  });
}

export function isReaderAskAgenticCitationList(
  value: unknown,
): value is ReaderAskAgenticCitationDto[] {
  if (!Array.isArray(value)) {
    return false;
  }
  return value.every((item) => {
    if (!item || typeof item !== "object") {
      return false;
    }
    const citation = item as Record<string, unknown>;
    if (
      typeof citation.citation_id !== "string" ||
      (citation.source_kind !== "article" && citation.source_kind !== "web") ||
      (citation.snippet != null && typeof citation.snippet !== "string") ||
      (citation.url != null && typeof citation.url !== "string") ||
      (citation.title != null && typeof citation.title !== "string") ||
      (citation.description != null && typeof citation.description !== "string") ||
      (citation.published_at != null && typeof citation.published_at !== "string") ||
      (citation.retrieved_at != null && typeof citation.retrieved_at !== "string") ||
      "handle_id" in citation ||
      "rag_navigation" in citation ||
      "web_snapshot" in citation ||
      "page_age" in citation ||
      "query" in citation ||
      "provider" in citation ||
      "provider_payload" in citation ||
      "raw_payload" in citation ||
      "rank" in citation ||
      "score" in citation
    ) {
      return false;
    }
    // Web citations must carry url + title (mirrors backend PublicCitation
    // validator). Article citations must not carry url/title/description.
    if (citation.source_kind === "web") {
      if (
        typeof citation.url !== "string" ||
        citation.url.length === 0 ||
        typeof citation.title !== "string" ||
        citation.title.length === 0
      ) {
        return false;
      }
    } else {
      if (
        citation.url != null ||
        citation.title != null ||
        citation.description != null ||
        citation.published_at != null ||
        citation.retrieved_at != null
      ) {
        return false;
      }
    }
    return true;
  });
}

const READER_ASK_AGENTIC_KNOWLEDGE_MODES = new Set<string>([
  "article_grounded",
  "general_knowledge",
  "web_grounded",
  "mixed",
]);

const READER_ASK_AGENTIC_SOURCE_STATUSES = new Set<string>([
  "article_source_unavailable",
]);

const READER_ASK_WEB_SEARCH_OUTCOMES = new Set<string>([
  "completed",
  "no_results",
  "unavailable",
  "failed",
  "timeout",
]);

/**
 * Validate the turn-level web search summary (mirrors backend
 * `PublicWebSearchSummary`). `null` means search was not invoked this
 * turn. Object form requires a closed `outcome` and a non-negative
 * integer `cited_source_count`.
 */
export function isReaderAskWebSearchSummary(
  value: unknown,
): value is ReaderAskWebSearchSummaryDto | null {
  if (value === null) {
    return true;
  }
  if (!value || typeof value !== "object") {
    return false;
  }
  const summary = value as Record<string, unknown>;
  return (
    typeof summary.outcome === "string" &&
    READER_ASK_WEB_SEARCH_OUTCOMES.has(summary.outcome) &&
    typeof summary.cited_source_count === "number" &&
    Number.isInteger(summary.cited_source_count) &&
    summary.cited_source_count >= 0
  );
}

export function isReaderAskAgenticCompletedPayload(
  data: unknown,
): data is ReaderAskAgenticCompletedPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  if (
    payload.execution_version !== READER_ASK_AGENTIC_EXECUTION_VERSION ||
    payload.final_status !== "ok" ||
    typeof payload.answer_text !== "string" ||
    typeof payload.message_id !== "string" ||
    typeof payload.thread_id !== "string" ||
    typeof payload.turn_run_id !== "string"
  ) {
    return false;
  }
  // Public no-evh: reject legacy fields if present.
  if (
    "envelope_fingerprint" in payload ||
    "evidence" in payload ||
    "evidence_scope" in payload ||
    "handle_id" in payload ||
    "content_md" in payload ||
    "reasoning_md" in payload ||
    "article_rag" in payload ||
    "action_proposals" in payload ||
    "tool_trace" in payload ||
    "response_cards" in payload ||
    "disambiguation" in payload ||
    "external_asset_disambiguation" in payload ||
    "supplement_candidates" in payload ||
    "persisted_supplements" in payload ||
    "context_plan" in payload ||
    "resolved_context" in payload
  ) {
    return false;
  }
  // Canonical v2 requires answer_blocks + citations arrays (may be empty).
  // Reject answer-only forgeries that omit structured projection.
  if (!isReaderAskAgenticAnswerBlockList(payload.answer_blocks)) {
    return false;
  }
  if (!isReaderAskAgenticCitationList(payload.citations)) {
    return false;
  }
  // knowledge_mode: null or legal enum (host-derived).
  if (
    !("knowledge_mode" in payload) ||
    (payload.knowledge_mode != null &&
      (typeof payload.knowledge_mode !== "string" ||
        !READER_ASK_AGENTIC_KNOWLEDGE_MODES.has(payload.knowledge_mode)))
  ) {
    return false;
  }
  // source_status: null or legal enum.
  if (
    !("source_status" in payload) ||
    (payload.source_status != null &&
      (typeof payload.source_status !== "string" ||
        !READER_ASK_AGENTIC_SOURCE_STATUSES.has(payload.source_status)))
  ) {
    return false;
  }
  // web_search: null or valid summary (mirrors backend
  // ReaderRecordAskCompletedDTO.web_search). Required key (may be null).
  if (
    !("web_search" in payload) ||
    !isReaderAskWebSearchSummary(payload.web_search)
  ) {
    return false;
  }
  return true;
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
    !("rejected_handles" in payload) &&
    !("envelope_fingerprint" in payload) &&
    !("content_md" in payload) &&
    !("reasoning_md" in payload) &&
    !("article_rag" in payload) &&
    !("evidence" in payload) &&
    !("action_proposals" in payload) &&
    !("tool_trace" in payload) &&
    !("response_cards" in payload) &&
    !("supplement_candidates" in payload) &&
    !("persisted_supplements" in payload)
  );
}

export function isReaderAskAgenticRunStartedPayload(
  data: unknown,
): data is ReaderAskAgenticRunStartedPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  // ASK-WEB-G1-R2: ``web_search_mode`` is optional on the wire for
  // backward compat with legacy streams. When present, it must be one
  // of the typed values; absence is treated as ``"disabled"`` (fail-
  // closed) by the consumer. The field is the **resolved** capability
  // signal — never the raw request toggle.
  const webSearchModeOk =
    !("web_search_mode" in payload) ||
    payload.web_search_mode === "disabled" ||
    payload.web_search_mode === "allowed";
  return (
    payload.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION &&
    typeof payload.message_id === "string" &&
    typeof payload.thread_id === "string" &&
    typeof payload.turn_run_id === "string" &&
    typeof payload.has_initial_selection === "boolean" &&
    webSearchModeOk &&
    !("envelope_fingerprint" in payload)
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
    typeof payload.summary === "string" &&
    (payload.activity_id == null ||
      payload.activity_id === "article_evidence" ||
      payload.activity_id === "web_search") &&
    (payload.attempt_count == null || isNonNegativeInt(payload.attempt_count)) &&
    (payload.call_sequence == null || isPositiveInt(payload.call_sequence)) &&
    (payload.outcome == null ||
      (typeof payload.outcome === "string" &&
        READER_ASK_AGENTIC_PROGRESS_OUTCOMES.has(payload.outcome))) &&
    !(
      "query" in payload ||
      "url" in payload ||
      "provider_payload" in payload ||
      "raw_payload" in payload
    )
  );
}

export function isReaderAskContextCompactionPayload(
  data: unknown,
): data is ReaderAskContextCompactionPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  return (
    payload.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION &&
    typeof payload.message_id === "string" &&
    typeof payload.thread_id === "string" &&
    typeof payload.turn_run_id === "string" &&
    isNonNegativeInt(payload.attempt_count) &&
    isNonNegativeInt(payload.elapsed_ms) &&
    (payload.detail_code == null || typeof payload.detail_code === "string") &&
    !(
      "query" in payload ||
      "url" in payload ||
      "provider_payload" in payload ||
      "raw_payload" in payload ||
      "transcript" in payload
    )
  );
}

function isNonNegativeInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isPositiveInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1;
}

/** ASK-REASONING-R1: identity binding + seq only; no content fields. */
export function isReaderAskAgenticReasoningStartedPayload(
  data: unknown,
): data is ReaderAskAgenticReasoningStartedPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  return (
    payload.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION &&
    typeof payload.message_id === "string" &&
    typeof payload.thread_id === "string" &&
    typeof payload.turn_run_id === "string" &&
    payload.seq === 0 &&
    typeof payload.projection_policy_version === "string" &&
    !("envelope_fingerprint" in payload) &&
    !("delta" in payload)
  );
}

/** ASK-REASONING-R1: projected increment — already sanitized server-side. */
export function isReaderAskAgenticReasoningDeltaPayload(
  data: unknown,
): data is ReaderAskAgenticReasoningDeltaPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  return (
    payload.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION &&
    typeof payload.message_id === "string" &&
    typeof payload.thread_id === "string" &&
    typeof payload.turn_run_id === "string" &&
    isNonNegativeInt(payload.seq) &&
    payload.seq >= 1 &&
    typeof payload.delta === "string" &&
    payload.delta.length > 0 &&
    !("envelope_fingerprint" in payload)
  );
}

/** ASK-REASONING-R1: completion promise — flags only, no content. */
export function isReaderAskAgenticReasoningCompletedPayload(
  data: unknown,
): data is ReaderAskAgenticReasoningCompletedPayloadDto {
  if (!data || typeof data !== "object") {
    return false;
  }
  const payload = data as Record<string, unknown>;
  return (
    payload.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION &&
    typeof payload.message_id === "string" &&
    typeof payload.thread_id === "string" &&
    typeof payload.turn_run_id === "string" &&
    isNonNegativeInt(payload.seq) &&
    payload.seq >= 1 &&
    typeof payload.has_content === "boolean" &&
    typeof payload.truncated === "boolean" &&
    typeof payload.projection_policy_version === "string" &&
    !("delta" in payload) &&
    !("envelope_fingerprint" in payload)
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
