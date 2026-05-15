export interface DailyReaderArticleDto {
  id: string;
  title: string;
  subtitle: string | null;
  source: string;
  source_url: string;
  publish_date: string;
  difficulty: string;
  read_time_minutes: number;
  tags: string[];
  cover_image_url: string | null;
  cover_theme: string;
  body: DailyReaderBodyDto;
  highlights: DailyReaderHighlightDto[];
  paragraph_notes?: DailyReaderParagraphNotesDto | null;
  takeaways?: DailyReaderTakeawaysDto | null;
  footer_analysis?: DailyReaderFooterAnalysisDto | null;
}

export interface DailyReaderBodyDto {
  paragraphs?: DailyReaderParagraphDto[];
}

export interface DailyReaderParagraphDto {
  id: string;
  text: string;
  highlights?: DailyReaderHighlightDto[];
  reading_note?: DailyReaderParagraphNoteDto | null;
}

export interface DailyReaderHighlightDto {
  id: string;
  type: "vocab_highlight" | "phrase_gloss" | "context_gloss";
  text: string;
  gloss: string;
  paragraph_id: string;
  start: number;
  end: number;
  detail?: {
    phonetic?: string;
    pos?: string;
    context_explanation?: string;
  } | null;
}

export interface DailyReaderParagraphNotesDto {
  article_summary?: string;
  reading_focus?: string[] | string;
  notes?: DailyReaderParagraphNoteDto[];
}

export interface DailyReaderParagraphNoteDto {
  paragraph_id: string;
  focus_question?: string;
  micro_summary?: string;
  translation?: string;
}

export interface DailyReaderTakeawaysDto {
  article_takeaway?: string;
  key_expressions?: DailyReaderTakeawayExpressionDto[];
  sentence_notes?: DailyReaderSentenceNoteDto[];
  writing_moves?: DailyReaderWritingMoveDto[];
  discussion_questions?: string[];
}

export interface DailyReaderTakeawayExpressionDto {
  expression: string;
  paragraph_id?: string;
  gloss: string;
  context_sentence: string;
  usage_note?: string;
}

export interface DailyReaderSentenceNoteDto {
  sentence: string;
  paragraph_id?: string;
  translation: string;
  breakdown: string;
  takeaway: string;
}

export interface DailyReaderWritingMoveDto {
  anchor: string;
  paragraph_id?: string;
  move_type: string;
  explanation: string;
  reusable_pattern?: string | null;
}

export interface DailyReaderFooterAnalysisDto {
  summary?: string;
  thesis_and_intent?: {
    thesis?: string;
    author_intent?: string;
  };
  structure?: DailyReaderStructurePartDto[];
  key_expressions?: DailyReaderKeyExpressionDto[];
  misreading_points?: DailyReaderMisreadingPointDto[];
  full_article_analysis?: string;
  discussion_questions?: string[];
}

export interface DailyReaderStructurePartDto {
  label: string;
  title: string;
  summary: string;
}

export interface DailyReaderKeyExpressionDto {
  expression: string;
  gloss: string;
  context_sentence: string;
}

export interface DailyReaderMisreadingPointDto {
  point: string;
  clarification: string;
}

export interface DailyReaderTodayResponseDto {
  articles: DailyReaderArticleDto[];
}

export interface DailyReaderListItemDto {
  id: string;
  title: string;
  subtitle: string | null;
  source: string;
  publish_date: string;
  difficulty: string;
  read_time_minutes: number;
  tags: string[];
  cover_image_url: string | null;
  cover_theme: string;
}

export interface DailyReaderListResponseDto {
  items: DailyReaderListItemDto[];
  cursor: string | null;
  has_more: boolean;
}
