/**
 * Reader 场景 Mock VM
 *
 * 包含所有 inline mark annotation types 和 sentence entry types
 * 用于 demo 和 standalone 调试，不依赖真实 API
 */

// ============ 基础类型 ============

export interface TextAnchor {
  kind: 'text'
  sentenceId: string
  anchorText: string
  occurrence?: number
}

export interface SpanRef {
  /**
   * Text fragment referenced by a multi_text anchor. This may be non-contiguous
   * with other parts, so the Reader treats it as a structural cue rather than
   * pretending it can always be highlighted inline.
   */
  anchorText: string
  occurrence?: number
  role?: string
}

export interface MultiTextAnchor {
  kind: 'multi_text'
  sentenceId: string
  parts: SpanRef[]
}

export type InlineMarkAnchor = TextAnchor | MultiTextAnchor

export type RenderType = 'background' | 'underline'

export interface SentenceModel {
  sentenceId: string
  paragraphId: string
  text: string
}

export interface ParagraphModel {
  paragraphId: string
  sentenceIds: string[]
}

export interface ArticleModel {
  paragraphs: ParagraphModel[]
  sentences: SentenceModel[]
}

export interface TranslationModel {
  sentenceId: string
  translationZh: string
}

export interface RequestMeta {
  requestId: string
  sourceType: string
  readingGoal: string
  readingVariant: string
  profileId: string
}

export type WarningLevel = 'info' | 'warning' | 'error'

export interface WarningModel {
  code: string
  level: WarningLevel
  message: string
  sentenceId?: string
  annotationId?: string
}

export type ContentResultState = 'normal' | 'degraded_light' | 'degraded_heavy'

export type ContentSummaryCompleteness = 'full' | 'partial' | 'minimal'

export interface ContentSummaryModel {
  completeness: ContentSummaryCompleteness
  overview: string
  researchQuestion?: string
  methodology?: string
  keyFindings: string[]
  limitations: string[]
}

// ============ Learning 模式类型 ============

export interface InlineGlossary {
  zh?: string
  gloss?: string
  reason?: string
  phraseType?: 'collocation' | 'phrasal_verb' | 'idiom' | 'proper_noun' | 'compound'
}

export type AnnotationType =
  | 'vocab_highlight'
  | 'phrase_gloss'
  | 'context_gloss'
  | 'grammar_note'
  | 'term_note'
  | 'logic_note'

export type VisualTone = 'vocab' | 'phrase' | 'context' | 'grammar' | 'term' | 'logic'

export type PhraseKind =
  | 'word'
  | 'phrase'
  | 'collocation'
  | 'phrasal_verb'
  | 'idiom'
  | 'proper_noun'
  | 'compound'

export interface InlineMarkModel {
  id: string
  annotationType: AnnotationType
  anchor: InlineMarkAnchor
  renderType: RenderType
  visualTone: VisualTone
  clickable: boolean
  lookupText?: string
  lookupKind?: PhraseKind
  glossary?: InlineGlossary
  parentId?: string
}

export type SentenceEntryType =
  | 'grammar_note'
  | 'sentence_analysis'
  | 'term_note'
  | 'logic_note'
  | 'interpretation_note'
  | 'content_summary'

export interface SentenceEntryModel {
  id: string
  sentenceId: string
  entryType: SentenceEntryType
  label: string
  title?: string
  content: string
}

export interface RenderSceneVmBase {
  schemaVersion: string
  request: RequestMeta
  article: ArticleModel
  userFacingState: ContentResultState
  contentSummary?: ContentSummaryModel
  translations: TranslationModel[]
  inlineMarks: InlineMarkModel[]
  sentenceEntries: SentenceEntryModel[]
  warnings: WarningModel[]
}

export type ReaderMockVm = RenderSceneVmBase
