export interface FavoriteCreateRequestDto {
  analysis_record_id?: string | null;
  target_type?: string;
  target_key: string;
  payload_json?: Record<string, unknown>;
  note?: string | null;
}

export interface FavoriteCreateResponseDto {
  id: string;
  ok: boolean;
}

export interface FavoriteResponseDto {
  id: string;
  user_id: string;
  target_type: string;
  target_key: string;
  analysis_record_id: string | null;
  payload_json: Record<string, unknown>;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface FavoriteListResponseDto {
  items: FavoriteResponseDto[];
  total: number;
}

export interface FavoriteDeleteResponseDto {
  deleted: boolean;
}
