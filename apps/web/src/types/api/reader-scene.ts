export interface ReaderSceneRecordMetaDto {
  id: string;
  client_record_id: string | null;
  title: string | null;
  source_type: "user_input" | "daily_article" | "imported" | "ocr";
  source_text: string;
  request_payload_json: Record<string, unknown>;
  reading_goal: string | null;
  reading_variant: string | null;
  analysis_status: string;
  user_facing_state: string | null;
  workflow_version: string | null;
  schema_version: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReaderSceneDto {
  schema_version: string;
  request: Record<string, unknown>;
  article: Record<string, unknown>;
  user_facing_state: string;
  translations: Record<string, unknown>[];
  inline_marks: Record<string, unknown>[];
  sentence_entries: Record<string, unknown>[];
  warnings: Record<string, unknown>[];
  content_summary?: Record<string, unknown> | null;
  title?: string | null;
}

export interface ReaderSceneViewMetaDto {
  view_version: string;
  data_source: "render_scene_snapshot" | "source_text_fallback";
  fallback_mode: "none" | "article_rebuilt_from_source_text" | "scene_missing";
  supplements_merged: boolean;
}

export interface ReaderSceneResponseDto {
  record_meta: ReaderSceneRecordMetaDto;
  reader_scene: ReaderSceneDto;
  view_meta: ReaderSceneViewMetaDto;
}
