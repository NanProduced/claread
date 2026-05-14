export type SourceTypeDto = "user_input" | "daily_article" | "imported" | "ocr";

export interface RecordResponseDto {
  id: string;
  user_id: string;
  client_record_id: string | null;
  source_type: SourceTypeDto;
  title: string | null;
  source_text: string;
  source_text_hash: string;
  request_payload_json: Record<string, unknown>;
  render_scene_json: Record<string, unknown>;
  page_state_json: Record<string, unknown>;
  reading_goal: string | null;
  reading_variant: string | null;
  extended: boolean;
  user_facing_state: string | null;
  workflow_version: string | null;
  schema_version: string | null;
  analysis_status: string;
  last_opened_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RecordListResponseDto {
  items: RecordResponseDto[];
  total: number;
  page: number;
  limit: number;
}

export interface RecordDeleteResponseDto {
  deleted: boolean;
}
