/**
 * 前端渲染 VM
 *
 * 严格对齐 UI 需求，禁止引入后端 concerns
 * 这是前端唯一正式的渲染模型输入
 *
 * 基于 client/src/types/render-scene.ts 重构，明确 VM 边界
 *
 * NOTE: 共享基础类型（InlineMark / Dictionary 结果模型）已迁出至
 * reader-primitive.vm.ts，本文件 re-export 以保持旧文章 Analysis 主链
 * 代码在 Logical 阶段可继续 typecheck；Physical 阶段会随旧页面一并删除。
 */

export type {
  TextAnchor,
  SpanRef,
  MultiTextAnchor,
  RangePart,
  RangeAnchor,
  MultiRangeAnchor,
  InlineMarkAnchor,
  RenderType,
  InlineGlossary,
  AnnotationType,
  VisualTone,
  PhraseKind,
  InlineMarkModel,
  AcademicInlineGlossary,
  AcademicAnnotationType,
  AcademicVisualTone,
  AcademicInlineMarkModel,
  AnyInlineMarkModel,
  DictionaryMeaning,
  DictionaryExample,
  DictionaryPhrase,
  DictionaryEntryPayload,
  DictionaryCandidate,
  DictionaryEntryResult,
  DictionaryDisambiguationResult,
  DictionaryNotFoundResult,
  DictionaryResult,
} from './reader-primitive.vm'

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

export type SentenceEntryType = 'grammar_note' | 'sentence_analysis'

export interface SentenceEntryChunk {
  order?: number
  label: string
  text: string
  occurrence?: number | null
}

export interface SentenceEntryModel {
  id: string
  sentenceId: string
  entryType: SentenceEntryType
  label: string
  title?: string
  content: string
  analysisText?: string | null
  chunks?: SentenceEntryChunk[]
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

export type AnySentenceEntryModel = SentenceEntryModel | AcademicSentenceEntryModel
export type AnyRenderSceneVm = RenderSceneVm | AcademicRenderSceneVm