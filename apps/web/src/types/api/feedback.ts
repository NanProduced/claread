export type FeedbackScopeDto =
  | "analysis_result"
  | "annotation"
  | "sentence"
  | "dictionary"
  | "app";

export type FeedbackSentimentDto = "positive" | "negative" | "neutral";
export type FeedbackClientPlatformDto = "web" | "wechat_miniprogram";

export type FeedbackTypeDto =
  | "thumbs_up"
  | "translation_inaccurate"
  | "too_few_annotations"
  | "too_many_annotations"
  | "wrong_difficulty"
  | "wrong_label"
  | "inaccurate"
  | "wrong_boundary"
  | "should_not_annotate"
  | "sentence_analysis_wrong"
  | "annotation_conflict"
  | "selection_issue"
  | "wrong_definition"
  | "missing_definition"
  | "wrong_pos"
  | "wrong_phonetic"
  | "bad_example"
  | "bug_report"
  | "feature_request"
  | "quota_issue"
  | "input_page_issue"
  | "ux_issue"
  | "helpful"
  | "other";

export interface FeedbackCreateRequestDto {
  feedback_scope: FeedbackScopeDto;
  target_id: string;
  analysis_record_id?: string | null;
  sentiment: FeedbackSentimentDto;
  feedback_type: FeedbackTypeDto;
  annotation_type?: string | null;
  content?: string | null;
  context_json: Record<string, unknown>;
  context_summary?: string | null;
  client_platform: FeedbackClientPlatformDto;
  client_surface?: string | null;
  entry_point?: string | null;
  app_version?: string | null;
}

export interface FeedbackResponseDto {
  id: string;
  feedback_scope: FeedbackScopeDto;
  target_id: string;
  sentiment: FeedbackSentimentDto;
  feedback_type: FeedbackTypeDto;
  client_platform: FeedbackClientPlatformDto;
  client_surface?: string | null;
  entry_point?: string | null;
  context_summary?: string | null;
  status: string;
  created_at: string;
}

export interface FeedbackListItemDto {
  id: string;
  feedback_scope: FeedbackScopeDto;
  feedback_type: FeedbackTypeDto;
  sentiment: FeedbackSentimentDto;
  content: string | null;
  context_summary?: string | null;
  client_platform: FeedbackClientPlatformDto;
  client_surface?: string | null;
  entry_point?: string | null;
  resolution_note?: string | null;
  status: string;
  reward_points: number;
  created_at: string;
}

export interface FeedbackListResponseDto {
  items: FeedbackListItemDto[];
  cursor: string | null;
  has_more: boolean;
}
