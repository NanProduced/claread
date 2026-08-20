export interface DailyReaderArticleDto {
  id: string;
  title: string;
  subtitle: string | null;
  /** A-3: 英文原题（caption 级展示）；旧文章可能缺失。 */
  original_title?: string | null;
  /** A-3: 中文副标题；旧文章可能缺失。 */
  subtitle_zh?: string | null;
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
}

export interface DailyReaderBodyDto {
  paragraphs?: DailyReaderParagraphDto[];
  /** B-1: curated news images (1 cover + 0-1 inline); rendering is Track C. */
  images?: DailyReaderImageBlockDto[];
}

export interface DailyReaderImageBlockDto {
  id: string;
  role: "cover" | "inline";
  url: string;
  width?: number | null;
  height?: number | null;
  /** Layout slot decided by the pipeline rule engine (surface brief §4). */
  layout: "full-bleed" | "two-third" | "half-float";
  /** Chinese caption grounded in article title + text (LLM-generated). */
  caption_zh?: string | null;
  /** Original caption / photo credit, preserved verbatim. */
  source_caption?: string | null;
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
  /** A-3: 中文主标题（已由 pipeline 落库为 title 列）；旧文章缺失。 */
  title_zh?: string;
  /** A-3: 中文副标题；旧文章缺失。 */
  subtitle_zh?: string;
  /** A-3: 全中文主题标签（已由 pipeline 落库为 tags 列）；旧文章缺失。 */
  tags_zh?: string[];
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

export interface DailyReaderTodayResponseDto {
  articles: DailyReaderArticleDto[];
}

export interface DailyReaderListItemDto {
  id: string;
  title: string;
  subtitle: string | null;
  /** A-3: 英文原题（caption 级展示）；旧文章可能缺失。 */
  original_title?: string | null;
  /** A-3: 中文副标题；旧文章可能缺失。 */
  subtitle_zh?: string | null;
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
