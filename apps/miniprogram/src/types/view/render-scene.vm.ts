/**
 * 前端渲染 VM
 *
 * 严格对齐 UI 需求，禁止引入后端 concerns
 * 这是前端唯一正式的渲染模型输入
 *
 * 基于 client/src/types/render-scene.ts 重构，明确 VM 边界
 */

// ============ 共享基础类型 ============

export interface TextAnchor {
  kind: 'text'
  sentenceId: string
  anchorText: string
  occurrence?: number
}

export interface SpanRef {
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
  sourceType: 'user_input'
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

export type ResultPageState =
  | 'loading'
  | 'normal'
  | 'degraded_light'
  | 'degraded_heavy'
  | 'empty'
  | 'failed'
  | 'timeout'
  | 'network_fail'

export type PageMode = 'immersive' | 'intensive'

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

export type VisualTone = 'vocab' | 'phrase' | 'context' | 'grammar'

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

export type SentenceEntryType = 'grammar_note' | 'sentence_analysis'

export interface SentenceEntryModel {
  id: string
  sentenceId: string
  entryType: SentenceEntryType
  label: string
  title?: string
  content: string
}

export interface RenderSceneVmBase {
  schemaVersion: '3.0.0'
  request: RequestMeta
  article: ArticleModel
  userFacingState: ContentResultState
  translations: TranslationModel[]
  inlineMarks: InlineMarkModel[]
  sentenceEntries: SentenceEntryModel[]
  warnings: WarningModel[]
}

export type RenderSceneVm = RenderSceneVmBase

// ============ Academic 模式类型 ============

export interface AcademicInlineGlossary {
  zh?: string
  zhUncertain?: boolean
  contextDefinition?: string
  termCategory?: string
  logicType?: string
  hedgingDetected?: boolean
  hedgingWords?: string[]
}

export type AcademicAnnotationType = 'term_note' | 'logic_note'
export type AcademicVisualTone = 'term' | 'logic'

export interface AcademicInlineMarkModel {
  id: string
  annotationType: AcademicAnnotationType
  anchor: InlineMarkAnchor
  renderType: RenderType
  visualTone: AcademicVisualTone
  clickable: boolean
  lookupText?: string
  lookupKind?: PhraseKind
  glossary?: AcademicInlineGlossary
  parentId?: string
}

export type AcademicSentenceEntryType = 'term_note' | 'logic_note' | 'interpretation_note' | 'content_summary'

export interface AcademicSentenceEntryModel {
  id: string
  sentenceId: string
  entryType: AcademicSentenceEntryType
  label: string
  title?: string
  content: string
}

export type ContentSummaryCompleteness = 'full' | 'partial' | 'minimal'

export interface ContentSummaryModel {
  completeness: ContentSummaryCompleteness
  overview: string
  researchQuestion?: string | null
  methodology?: string | null
  keyFindings?: string[]
  limitations?: string[]
}

export interface AcademicRenderSceneVm {
  schemaVersion: '3.0.0-academic'
  request: RequestMeta
  article: ArticleModel
  userFacingState: ContentResultState
  translations: TranslationModel[]
  inlineMarks: AcademicInlineMarkModel[]
  sentenceEntries: AcademicSentenceEntryModel[]
  contentSummary: ContentSummaryModel | null
  title?: string | null
  warnings: WarningModel[]
}

// ============ 联合类型 ============

export type AnyInlineMarkModel = InlineMarkModel | AcademicInlineMarkModel
export type AnySentenceEntryModel = SentenceEntryModel | AcademicSentenceEntryModel
export type AnyRenderSceneVm = RenderSceneVm | AcademicRenderSceneVm

export interface DictionaryMeaning {
  partOfSpeech: string
  definitions: Array<{
    meaning: string
    example?: string
    exampleTranslation?: string
  }>
}

export interface DictionaryExample {
  example: string
  exampleTranslation?: string
}

export interface DictionaryPhrase {
  phrase: string
  meaning?: string
}

export interface DictionaryEntryPayload {
  id: number
  word: string
  baseWord?: string
  homographNo?: number
  phonetic?: string
  meanings: DictionaryMeaning[]
  examples: DictionaryExample[]
  phrases: DictionaryPhrase[]
  entryKind: 'entry' | 'fragment'
  exchange?: string[]
  tags?: string[]
}

export interface DictionaryCandidate {
  entryId: number
  label: string
  partOfSpeech?: string
  preview?: string
  entryKind: 'entry' | 'fragment'
  matchKind?: string
  lookupType?: 'word' | 'phrase'
  candidateKind?: 'word' | 'phrase' | 'proper_noun' | 'variant' | 'fragment'
}

interface DictionaryResultBase {
  resultType: 'entry' | 'disambiguation' | 'not_found'
  query: string
  provider?: string
  cached?: boolean
}

export interface DictionaryEntryResult extends DictionaryResultBase {
  resultType: 'entry'
  entry: DictionaryEntryPayload
}

export interface DictionaryDisambiguationResult extends DictionaryResultBase {
  resultType: 'disambiguation'
  ambiguityKind?: 'same_headword_senses' | 'phrase_vs_word' | 'proper_vs_common' | 'lemma_competing' | 'competing_entries'
  selectionRequired?: boolean
  candidates: DictionaryCandidate[]
}

export interface DictionaryNotFoundResult extends DictionaryResultBase {
  resultType: 'not_found'
  reason: 'not_in_dictionary'
}

export type DictionaryResult = DictionaryEntryResult | DictionaryDisambiguationResult | DictionaryNotFoundResult
