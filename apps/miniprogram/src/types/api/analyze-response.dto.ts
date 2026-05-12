/**
 * 后端响应 DTO
 *
 * 严格对齐后端 RenderSceneModel schema (snake_case)
 * 禁止在此文件引入 UI concerns
 *
 * @see server/app/schemas/analysis.py::RenderSceneModel
 * @see server/app/schemas/analysis.py::AcademicRenderSceneModel
 */

/** 后端 schema 版本 */
export const BACKEND_SCHEMA_VERSION = '3.0.0' as const
export const ACADEMIC_SCHEMA_VERSION = '3.0.0-academic' as const

// ============ 基础类型 ============

export interface TextSpan {
  start: number
  end: number
}

// ============ 请求元信息 ============

export type SourceType = 'user_input' | 'daily_article' | 'ocr'
export type ServerReadingGoal = 'exam' | 'daily_reading' | 'academic'
export type ReadingVariant =
  | 'gaokao' | 'cet' | 'kaoyan' | 'tem' | 'ielts_toefl'
  | 'beginner_reading' | 'intermediate_reading' | 'intensive_reading'
  | 'academic_general'

export interface AnalyzeRequestMeta {
  request_id: string
  source_type: SourceType
  reading_goal: ServerReadingGoal
  reading_variant: ReadingVariant
  profile_id: string
}

// ============ 文章结构 ============

export interface ArticleSentence {
  sentence_id: string
  paragraph_id: string
  text: string
  sentence_span: TextSpan
}

export interface ArticleParagraph {
  paragraph_id: string
  text: string
  render_span: TextSpan
  sentence_ids: string[]
}

export interface ArticleStructure {
  source_type: SourceType
  source_text: string
  render_text: string
  paragraphs: ArticleParagraph[]
  sentences: ArticleSentence[]
}

// ============ 翻译 ============

export interface TranslationItem {
  sentence_id: string
  translation_zh: string
}

// ============ 警告 ============

export type WarningLevel = 'info' | 'warning' | 'error'

export interface Warning {
  code: string
  level: WarningLevel
  message: string
  sentence_id?: string
  annotation_id?: string
}

// ============ 页面状态 ============

export type UserFacingState = 'normal' | 'degraded_light' | 'degraded_heavy'

// ============ Learning 模式行内标注 ============

export type InlineMarkRenderType = 'background' | 'underline'
export type VisualTone = 'vocab' | 'phrase' | 'context' | 'grammar'
export type AnnotationType = 'vocab_highlight' | 'phrase_gloss' | 'context_gloss' | 'grammar_note'

export interface InlineGlossary {
  zh?: string
  gloss?: string
  reason?: string
  phrase_type?: 'collocation' | 'phrasal_verb' | 'idiom' | 'proper_noun' | 'compound'
}

export interface TextAnchor {
  kind: 'text'
  sentence_id: string
  anchor_text: string
  occurrence?: number
}

export interface SpanRefPart {
  anchor_text: string
  occurrence?: number
  role?: string
}

export interface MultiTextAnchor {
  kind: 'multi_text'
  sentence_id: string
  parts: SpanRefPart[]
}

export type InlineMarkAnchor = TextAnchor | MultiTextAnchor

export interface InlineMark {
  id: string
  annotation_type: AnnotationType
  anchor: InlineMarkAnchor
  render_type: InlineMarkRenderType
  visual_tone: VisualTone
  clickable: boolean
  lookup_text?: string
  lookup_kind?: 'word' | 'phrase'
  glossary?: InlineGlossary
}

// ============ Learning 模式句尾入口 ============

export type SentenceEntryType = 'grammar_note' | 'sentence_analysis'

export interface SentenceEntry {
  id: string
  sentence_id: string
  entry_type: SentenceEntryType
  label: string
  title?: string
  content: string
}

// ============ Learning 模式完整响应 ============

export interface AnalyzeResponseDto {
  schema_version: typeof BACKEND_SCHEMA_VERSION
  request: AnalyzeRequestMeta
  article: ArticleStructure
  user_facing_state: UserFacingState
  translations: TranslationItem[]
  inline_marks: InlineMark[]
  sentence_entries: SentenceEntry[]
  warnings: Warning[]
}

// ============ Academic 模式行内标注 ============

export type AcademicAnnotationType = 'term_note' | 'logic_note'
export type AcademicVisualTone = 'term' | 'logic'

export interface AcademicInlineGlossary {
  zh?: string
  zh_uncertain?: boolean
  context_definition?: string
  term_category?: string
  logic_type?: string
  hedging_detected?: boolean
  hedging_words?: string[]
}

export interface AcademicInlineMark {
  id: string
  annotation_type: AcademicAnnotationType
  anchor: InlineMarkAnchor
  render_type: InlineMarkRenderType
  visual_tone: AcademicVisualTone
  clickable: boolean
  lookup_text?: string
  glossary?: AcademicInlineGlossary
}

// ============ Academic 模式句尾入口 ============

export type AcademicSentenceEntryType = 'term_note' | 'logic_note' | 'interpretation_note' | 'content_summary'

export interface AcademicSentenceEntry {
  id: string
  sentence_id: string
  entry_type: AcademicSentenceEntryType
  label: string
  title?: string
  content: string
}

// ============ Academic 模式内容概要 ============

export type ContentSummaryCompleteness = 'full' | 'partial' | 'minimal'

export interface ContentSummaryDto {
  completeness: ContentSummaryCompleteness
  overview: string
  research_question?: string | null
  methodology?: string | null
  key_findings?: string[]
  limitations?: string[]
}

// ============ Academic 模式完整响应 ============

export interface AcademicAnalyzeResponseDto {
  schema_version: typeof ACADEMIC_SCHEMA_VERSION
  request: AnalyzeRequestMeta
  article: ArticleStructure
  user_facing_state: UserFacingState
  translations: TranslationItem[]
  inline_marks: AcademicInlineMark[]
  sentence_entries: AcademicSentenceEntry[]
  content_summary: ContentSummaryDto | null
  title?: string | null
  warnings: Warning[]
}

// ============ 联合类型 ============

export type AnyAnalyzeResponseDto = AnalyzeResponseDto | AcademicAnalyzeResponseDto
