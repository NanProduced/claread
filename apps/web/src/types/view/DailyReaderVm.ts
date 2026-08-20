export interface DailyReaderArticle {
  id: string;
  title: string;
  subtitle: string | null;
  /** A-3: 英文原题（caption 级展示）；旧文章为 null，展示回退 title。 */
  originalTitle: string | null;
  /** A-3: 中文副标题；旧文章为 null。 */
  subtitleZh: string | null;
  source: string;
  sourceUrl: string;
  publishDate: string;
  difficulty: string;
  readTimeMinutes: number;
  tags: string[];
  coverImageUrl: string | null;
  coverTheme: string;
  preReadingGuide?: DailyReaderPreReadingGuide;
  body: DailyReaderBody;
  highlights: DailyReaderHighlight[];
  footerAnalysis: DailyReaderFooterAnalysis;
}

export interface DailyReaderPreReadingGuide {
  overview: string;
  questions: string[];
}

export interface DailyReaderBody {
  paragraphs: DailyReaderParagraph[];
  /** B-1: curated news images (1 cover + 0-1 inline); rendering is Track C. */
  images?: DailyReaderImageBlock[];
}

export interface DailyReaderImageBlock {
  id: string;
  role: "cover" | "inline";
  url: string;
  width?: number | null;
  height?: number | null;
  layout: "full-bleed" | "two-third" | "half-float";
  captionZh?: string | null;
  sourceCaption?: string | null;
}

export interface DailyReaderParagraph {
  id: string;
  text: string;
  highlights: DailyReaderHighlight[];
  readingNote?: {
    focusQuestion: string;
    microSummary: string;
  };
  translation?: string;
}

export interface DailyReaderHighlight {
  id: string;
  type: "vocab_highlight" | "phrase_gloss" | "context_gloss";
  text: string;
  gloss: string;
  paragraphId: string;
  start: number;
  end: number;
  detail?: {
    phonetic?: string;
    pos?: string;
    contextExplanation?: string;
  } | null;
}

export interface DailyReaderFooterAnalysis {
  summary: string;
  keyExpressions: DailyReaderKeyExpression[];
  discussionQuestions: string[];
  articleTakeaway?: string;
  sentenceNotes?: DailyReaderSentenceNote[];
  writingMoves?: DailyReaderWritingMove[];
}

export interface DailyReaderSentenceNote {
  sentence: string;
  paragraphId?: string;
  translation: string;
  breakdown: string;
  takeaway: string;
}

export interface DailyReaderWritingMove {
  anchor: string;
  paragraphId?: string;
  moveType: string;
  explanation: string;
  reusablePattern?: string | null;
}

export interface DailyReaderKeyExpression {
  expression: string;
  gloss: string;
  contextSentence: string;
  paragraphId?: string;
  usageNote?: string;
}

export interface DailyReaderListItem {
  id: string;
  title: string;
  subtitle: string | null;
  /** A-3: 英文原题；旧文章为 null。 */
  originalTitle: string | null;
  /** A-3: 中文副标题；旧文章为 null。 */
  subtitleZh: string | null;
  source: string;
  publishDate: string;
  difficulty: string;
  readTimeMinutes: number;
  tags: string[];
  coverImageUrl: string | null;
  coverTheme: string;
}
