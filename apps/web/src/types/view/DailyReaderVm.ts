export interface DailyReaderArticle {
  id: string;
  title: string;
  subtitle: string | null;
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
  thesisAndIntent: {
    thesis: string;
    authorIntent: string;
  };
  structure: DailyReaderStructurePart[];
  keyExpressions: DailyReaderKeyExpression[];
  misreadingPoints: DailyReaderMisreadingPoint[];
  fullArticleAnalysis: string;
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

export interface DailyReaderStructurePart {
  label: string;
  title: string;
  summary: string;
}

export interface DailyReaderKeyExpression {
  expression: string;
  gloss: string;
  contextSentence: string;
  paragraphId?: string;
  usageNote?: string;
}

export interface DailyReaderMisreadingPoint {
  point: string;
  clarification: string;
}

export interface DailyReaderListItem {
  id: string;
  title: string;
  subtitle: string | null;
  source: string;
  publishDate: string;
  difficulty: string;
  readTimeMinutes: number;
  tags: string[];
  coverImageUrl: string | null;
  coverTheme: string;
}
