export type VocabularyMasteryStatusDto = "new" | "learning" | "reviewing" | "mastered" | string;

export interface VocabularySourceRefDto {
  client_record_id?: string;
  cloud_record_id?: string | null;
  source_sentence?: string | null;
  source_context?: string | null;
  source_sentence_id?: string | null;
  source_anchor_text?: string | null;
  source_occurrence?: number | null;
  collected_at?: string | null;
}

export interface VocabularyPayloadDto {
  source_refs?: VocabularySourceRefDto[];
  collected_forms?: string[];
  audio_url?: string | null;
  review?: {
    stage?: number;
    next_review_at?: string | null;
    last_result?: string | null;
    last_reviewed_at?: string | null;
  } | null;
  [key: string]: unknown;
}

export interface VocabularyResponseDto {
  id: string;
  user_id: string;
  lemma: string;
  display_word: string;
  phonetic: string | null;
  part_of_speech: string | null;
  short_meaning: string;
  meanings_json: Record<string, unknown>[] | null;
  tags: string[];
  exchange: string[];
  source_provider: string;
  dict_entry_id: number | null;
  source_sentence: string | null;
  source_context: string | null;
  mastery_status: VocabularyMasteryStatusDto;
  review_count: number;
  last_reviewed_at: string | null;
  payload_json: VocabularyPayloadDto | null;
  created_at: string;
  updated_at: string;
}

export interface VocabularyListResponseDto {
  items: VocabularyResponseDto[];
  total: number;
  page: number;
  limit: number;
}
