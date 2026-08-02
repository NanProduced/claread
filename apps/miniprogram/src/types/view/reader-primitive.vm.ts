/**
 * Reader Primitive VM
 *
 * 从 render-scene.vm 迁出的共享基础类型，供 Daily Reader / Dictionary / Vocabulary 等
 * 保留产品能力直接依赖，避免引用旧文章 Analysis render-scene VM。
 *
 * 这里只收录：
 * - InlineMark 锚点与模型（Learning + Academic）
 * - Dictionary 结果模型
 *
 * 旧 RenderSceneVm / AcademicRenderSceneVm 联合类型仍由 render-scene.vm.ts 持有，
 * 待旧文章 Analysis 主链 Physical 删除时一并清理。
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

export interface RangePart {
  start: number
  end: number
  text: string
  role?: string
  sourceQuote?: string
  resolutionKind?: string
}

export interface RangeAnchor {
  kind: 'range'
  sentenceId: string
  offsetUnit: 'utf16'
  range: RangePart
}

export interface MultiRangeAnchor {
  kind: 'multi_range'
  sentenceId: string
  offsetUnit: 'utf16'
  ranges: RangePart[]
}

export type InlineMarkAnchor = TextAnchor | MultiTextAnchor | RangeAnchor | MultiRangeAnchor

export type RenderType = 'background' | 'underline'

// ============ Learning 模式 InlineMark ============

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

// ============ Academic 模式 InlineMark ============

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

export type AnyInlineMarkModel = InlineMarkModel | AcademicInlineMarkModel

// ============ Dictionary 结果模型 ============

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
