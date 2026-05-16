export type ExcerptAssetStateDto =
  | "all"
  | "favorite"
  | "highlight"
  | "note"
  | "insight";

export type ExcerptAnchorTypeDto = "sentence" | "text_range" | "multi_text";

export interface ExcerptInsightDto {
  id: string;
  type: "grammar" | "sentence" | "term" | "logic" | "interpretation" | "summary";
  label: string;
  title: string;
  detail?: string | null;
}

export interface ExcerptAssetSegmentDto {
  paragraph_id?: string | null;
  sentence_id: string;
  selected_text: string;
  start_offset: number;
  end_offset: number;
  text_hash: string;
}

export interface ExcerptAssetItemDto {
  target_key: string;
  anchor_type: ExcerptAnchorTypeDto;
  sentence_id?: string | null;
  selected_text: string;
  translation?: string | null;
  start_offset?: number | null;
  end_offset?: number | null;
  text_hash?: string | null;
  segments: ExcerptAssetSegmentDto[];
  updated_at: string;
  is_favorited: boolean;
  is_highlighted: boolean;
  is_noted: boolean;
  annotation_id?: string | null;
  annotation_type?: string | null;
  annotation_color?: string | null;
  note?: string | null;
  insights: ExcerptInsightDto[];
}

export interface ExcerptAssetGroupDto {
  record_id: string;
  client_record_id?: string | null;
  title: string;
  subtitle?: string | null;
  updated_at: string;
  asset_count: number;
  items: ExcerptAssetItemDto[];
}

export interface ExcerptAssetsResponseDto {
  groups: ExcerptAssetGroupDto[];
  total_assets: number;
  total_groups: number;
  page: number;
  limit: number;
}
