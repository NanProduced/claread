export interface QuotaResponseDto {
  daily_free_points: number;
  daily_used_points: number;
  bonus_points: number;
  remaining_points: number;
}

export interface LedgerEntryResponseDto {
  id: string;
  entry_type: string;
  points: number;
  bucket_type: string;
  balance_after: number;
  description: string;
  article_title: string | null;
  metadata?: Record<string, unknown>;
  task_id: string | null;
  created_at: string;
}

export interface LedgerListResponseDto {
  items: LedgerEntryResponseDto[];
  cursor: string | null;
  has_more: boolean;
}
