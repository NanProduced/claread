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
  source_metadata: Record<string, unknown>;
  product_state: ReadingRecordProductState;
  readiness_state: ReadingRecordReadinessState;
  last_event_sequence: number;
}

export interface ReadingRecordListResponseDto {
  items: ReadingRecordListItemDto[];
  total: number;
  limit: number;
}
