import type { FavoriteTargetType } from "@claread/contracts";
import type { WebAnchorSegmentVm } from "./annotations";

export interface FavoriteCreateRequestDto {
  analysis_record_id?: string | null;
  target_type?: FavoriteTargetType;
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
  target_type: FavoriteTargetType;
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

export interface WebFavoriteTargetVm {
  id: string;
  targetType: FavoriteTargetType;
  targetKey: string;
  recordId: string | null;
  anchorType: "sentence" | "text_range" | "multi_text";
  sentenceId: string | null;
  selectedText: string | null;
  startOffset: number | null;
  endOffset: number | null;
  textHash: string | null;
  segments: WebAnchorSegmentVm[];
}
