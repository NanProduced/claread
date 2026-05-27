import type { WebAnchorSegmentVm } from "./annotations";

export interface ReaderNoteCreateRequestDto {
  analysis_record_id: string;
  quote_mode: "sentence" | "text_range" | "multi_text";
  anchor_sentence_id: string;
  target_key?: string | null;
  paragraph_id?: string | null;
  sentence_id?: string | null;
  selected_text: string;
  start_offset?: number | null;
  end_offset?: number | null;
  text_hash?: string | null;
  segments?: Array<{
    paragraph_id?: string | null;
    sentence_id: string;
    selected_text: string;
    start_offset: number;
    end_offset: number;
    text_hash: string;
  }>;
  note_text: string;
  payload_json?: Record<string, unknown>;
}

export interface ReaderNoteUpdateRequestDto {
  note_text: string;
}

export interface ReaderNoteResponseDto {
  id: string;
  analysis_record_id: string;
  anchor_sentence_id: string;
  quote_mode: "sentence" | "text_range" | "multi_text";
  target_key: string;
  paragraph_id: string | null;
  sentence_id: string | null;
  selected_text: string;
  start_offset: number | null;
  end_offset: number | null;
  text_hash: string | null;
  segments: Array<{
    paragraph_id?: string | null;
    sentence_id: string;
    selected_text: string;
    start_offset: number;
    end_offset: number;
    text_hash: string;
  }>;
  note_text: string;
  payload_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ReaderNoteListResponseDto {
  items: ReaderNoteResponseDto[];
}

export interface WebReaderNoteVm {
  id: string;
  recordId: string;
  anchorSentenceId: string;
  quoteMode: "sentence" | "text_range" | "multi_text";
  targetKey: string;
  paragraphId: string | null;
  sentenceId: string | null;
  selectedText: string;
  startOffset: number | null;
  endOffset: number | null;
  textHash: string | null;
  segments: WebAnchorSegmentVm[];
  noteText: string;
  createdAt: string;
  updatedAt: string;
}

export interface WebReaderNoteCreateRequest {
  recordId: string;
  quoteMode: "sentence" | "text_range" | "multi_text";
  anchorSentenceId: string;
  paragraphId?: string;
  sentenceId?: string;
  selectedText: string;
  startOffset?: number | null;
  endOffset?: number | null;
  textHash?: string | null;
  segments?: WebAnchorSegmentVm[];
  noteText: string;
  payloadJson?: Record<string, unknown>;
}
