/**
 * Reading Record list DTO types.
 *
 * Mirrors the backend schemas in
 * `services/api/app/schemas/reader_orchestration.py` for the list endpoint:
 *   - GET /reader/records
 *
 * This list source is independent from the legacy `/records` BFF and only
 * carries new Reading Record ids.
 */

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

export interface ReadingRecordListItemDto {
  record_id: string;
  title: string | null;
  created_at: string;
  source_type: string;
  product_state: ReadingRecordProductState;
  readiness_state: ReadingRecordReadinessState;
  last_event_sequence: number;
  last_opened_at: string | null;
  /**
   * S2.5: Backend-decided stable display title. The UI should prefer this
   * over `title`. Priority chain:
   * succeeded generated_title_zh → record.title → ready candidate title →
   * filename → source-type label → "未命名解读".
   */
  display_title: string;
  /**
   * S2.5: Backend-controlled friendly source label (e.g. "粘贴文本",
   * "上传文件 · report.pdf"). Raw metadata_json is never exposed.
   */
  source_label: string;
}

export interface ReadingRecordListResponseDto {
  items: ReadingRecordListItemDto[];
  total: number;
  limit: number;
}

export interface ReaderRecordOpenedResponseDto {
  record_id: string;
  last_opened_at: string;
}
