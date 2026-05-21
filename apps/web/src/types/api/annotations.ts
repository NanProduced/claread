import type {
  UserAnnotationAnchorType,
  UserAnnotationColor,
  UserAnnotationType,
} from "@claread/contracts";

export type UserAnnotationTypeDto = UserAnnotationType;
export type UserAnnotationAnchorTypeDto = UserAnnotationAnchorType;
export type UserAnnotationColorDto = UserAnnotationColor;

export interface UserAnchorSegmentDto {
  paragraph_id?: string | null;
  sentence_id: string;
  selected_text: string;
  start_offset: number;
  end_offset: number;
  text_hash: string;
}

export interface UserAnnotationCreateRequestDto {
  analysis_record_id?: string | null;
  anchor_type?: UserAnnotationAnchorTypeDto;
  target_key?: string | null;
  paragraph_id?: string | null;
  sentence_id?: string | null;
  selected_text: string;
  start_offset?: number | null;
  end_offset?: number | null;
  text_hash?: string | null;
  segments?: UserAnchorSegmentDto[];
  color?: UserAnnotationColorDto;
  payload_json?: Record<string, unknown>;
}

export interface UserAnnotationUpdateRequestDto {
  color: UserAnnotationColorDto;
}

export interface UserAnnotationResponseDto {
  id: string;
  analysis_record_id: string | null;
  anchor_type: UserAnnotationAnchorTypeDto;
  target_key: string;
  paragraph_id: string | null;
  sentence_id: string | null;
  selected_text: string;
  start_offset: number | null;
  end_offset: number | null;
  text_hash: string | null;
  segments: UserAnchorSegmentDto[];
  color: UserAnnotationColorDto;
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
  sentenceId?: string;
  selectedText: string;
  anchorType?: UserAnnotationAnchorTypeDto;
  startOffset?: number | null;
  endOffset?: number | null;
  textHash?: string | null;
  segments?: WebAnchorSegmentVm[];
  color?: UserAnnotationColorDto;
  payloadJson?: Record<string, unknown>;
}

export interface WebAnnotationUpdateRequest {
  color: UserAnnotationColorDto;
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
  segments: WebAnchorSegmentVm[];
  color: UserAnnotationColorDto;
  createdAt: string;
  updatedAt: string;
}

export interface WebAnchorSegmentVm {
  paragraphId?: string | null;
  sentenceId: string;
  selectedText: string;
  startOffset: number;
  endOffset: number;
  textHash: string;
}
