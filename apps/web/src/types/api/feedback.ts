export type FeedbackScopeDto =
  | "dictionary"
  | "app";

export type FeedbackSentimentDto = "positive" | "negative" | "neutral";
export type FeedbackClientPlatformDto = "web" | "wechat_miniprogram";

export type FeedbackTypeDto =
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
  | "other";

export interface FeedbackCreateRequestDto {
  feedback_scope: FeedbackScopeDto;
  target_id: string;
  sentiment: FeedbackSentimentDto;
  feedback_type: FeedbackTypeDto;
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
