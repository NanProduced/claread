import type {
  ContentResultState,
  ContentSummaryCompleteness,
  ContentSummaryModel,
  AnnotationType,
  InlineGlossary,
  InlineMarkModel,
  PhraseKind,
  RequestMeta,
  SentenceEntryModel,
  VisualTone,
} from "@/types/view/ReaderMockVm";

export interface ReaderPlateTextLeaf {
  text: string;
  readerSentenceId?: string;
  readerTextStartOffset?: number;
  readerTextEndOffset?: number;
  readerMarkAnnotationType?: AnnotationType;
  readerMarkAnchorText?: string;
  readerMarkClickable?: boolean;
  readerMarkGlossary?: InlineGlossary;
  readerMarkId?: string;
  readerMarkParentId?: string;
  readerMarkLookupKind?: PhraseKind;
  readerMarkLookupText?: string;
  readerMarkRenderType?: InlineMarkModel["renderType"];
  readerMarkVisualTone?: VisualTone;
}

export interface ReaderPlateDocument {
  type: "reader_document";
  schemaVersion: string;
  request: RequestMeta;
  userFacingState: ContentResultState;
  children: ReaderPlateTopLevelNode[];
}

export type ReaderPlateTopLevelNode =
  | ReaderContentSummaryNode
  | ReaderParagraphNode;

export interface ReaderParagraphNode {
  type: "reader_paragraph";
  paragraphId: string;
  sentenceIds: string[];
  children: ReaderSentenceNode[];
}

export interface ReaderSentenceNode {
  type: "reader_sentence";
  sentenceId: string;
  paragraphId: string;
  sourceText: string;
  children: ReaderSentenceChildNode[];
}

export type ReaderSentenceChildNode =
  | ReaderSentenceTextNode
  | ReaderTranslationNode
  | ReaderAnalysisBlockNode;

export interface ReaderSentenceTextNode {
  type: "reader_sentence_text";
  sentenceId: string;
  inlineMarks: InlineMarkModel[];
  children: ReaderPlateTextLeaf[];
}

export interface ReaderTranslationNode {
  type: "reader_translation";
  sentenceId: string;
  translationZh: string;
  children: ReaderPlateTextLeaf[];
}

export type ReaderAnalysisBlockNodeType =
  | "reader_grammar_note"
  | "reader_sentence_analysis"
  | "reader_term_note"
  | "reader_logic_note"
  | "reader_interpretation_note";

export interface ReaderAnalysisBlockNode {
  type: ReaderAnalysisBlockNodeType;
  entryId: string;
  sentenceId: string;
  entryType: Exclude<SentenceEntryModel["entryType"], "content_summary">;
  label: string;
  title?: string;
  content: string;
  sourceKind?: SentenceEntryModel["sourceKind"];
  supplementId?: string;
  deletable?: boolean;
  createdFromTurnRunId?: string;
  children: ReaderPlateTextLeaf[];
}

export interface ReaderContentSummaryNode {
  type: "reader_content_summary";
  completeness: ContentSummaryCompleteness;
  overview: string;
  researchQuestion?: string;
  methodology?: string;
  keyFindings: string[];
  limitations: string[];
  children: ReaderPlateTextLeaf[];
}

export type ReaderContentSummarySource = ContentSummaryModel;
