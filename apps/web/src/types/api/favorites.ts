export interface FavoriteCreateRequestDto {
  // API only accepts "daily_reader_article" today; other values are rejected
  // upstream with 400 until the D2 schema work widens the DB CHECK.
  target_type?: string;
  target_key: string;
  payload_json?: Record<string, unknown>;
}

export interface FavoriteCreateResponseDto {
  id: string;
  ok: boolean;
}

export interface FavoriteResponseDto {
  id: string;
  user_id: string;
  // Plain string: historical rows may still carry legacy target types.
  target_type: string;
  target_key: string;
  payload_json: Record<string, unknown>;
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
