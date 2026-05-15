export type UserAnnotationTypeDto = "highlight" | "note";
export type UserAnnotationAnchorTypeDto = "sentence" | "paragraph" | "text_range";
export type UserAnnotationColorDto =
  | "soft_green"
  | "soft_blue"
  | "soft_purple"
  | "warm_yellow"
  | "sage_green";

export interface UserAnnotationCreateRequestDto {
  analysis_record_id?: string | null;
  annotation_type?: UserAnnotationTypeDto;
  anchor_type?: UserAnnotationAnchorTypeDto;
  target_key?: string | null;
  paragraph_id?: string | null;
  sentence_id?: string | null;
  selected_text: string;
  start_offset?: number | null;
  end_offset?: number | null;
  text_hash?: string | null;
  color?: UserAnnotationColorDto;
  note?: string | null;
  payload_json?: Record<string, unknown>;
}

export interface UserAnnotationUpdateRequestDto {
  color?: UserAnnotationColorDto | null;
  note?: string | null;
}

export interface UserAnnotationResponseDto {
  id: string;
  analysis_record_id: string | null;
  annotation_type: UserAnnotationTypeDto;
  anchor_type: UserAnnotationAnchorTypeDto;
  target_key: string;
  paragraph_id: string | null;
  sentence_id: string | null;
  selected_text: string;
  start_offset: number | null;
  end_offset: number | null;
  text_hash: string | null;
  color: UserAnnotationColorDto;
  note: string | null;
  payload_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface UserAnnotationListResponseDto {
  items: UserAnnotationResponseDto[];
}

export interface WebAnnotationCreateRequest {
  recordId: string;
  paragraphId?: string;
  sentenceId: string;
  selectedText: string;
  anchorType?: UserAnnotationAnchorTypeDto;
  startOffset?: number | null;
  endOffset?: number | null;
  textHash?: string | null;
  color?: UserAnnotationColorDto;
  note?: string;
  payloadJson?: Record<string, unknown>;
}

export interface WebAnnotationUpdateRequest {
  color?: UserAnnotationColorDto | null;
  note?: string | null;
}

export interface WebAnnotationVm {
  id: string;
  recordId: string | null;
  type: UserAnnotationTypeDto;
  anchorType: UserAnnotationAnchorTypeDto;
  targetKey: string;
  paragraphId: string | null;
  sentenceId: string | null;
  selectedText: string;
  startOffset: number | null;
  endOffset: number | null;
  textHash: string | null;
  color: UserAnnotationColorDto;
  note: string | null;
  createdAt: string;
  updatedAt: string;
}
