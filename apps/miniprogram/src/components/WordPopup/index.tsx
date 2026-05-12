import { View, Text, ScrollView } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState, useEffect, useRef, useMemo } from 'react'
import { AnyInlineMarkModel, type VisualTone, type AcademicVisualTone, type InlineGlossary, type AcademicInlineGlossary, type DictionaryEntryPayload, type DictionaryDisambiguationResult, type DictionaryResult } from '../../types/view/render-scene.vm'
import { ApiError, fetchDict, fetchDictEntry } from '../../services/api/client'
import { dictResponseDtoToVm } from '../../services/api/adapters/dict.adapter'
import { getDictCache, setDictCache, getEntryCache, setEntryCache } from '../../services/dictCache'
import { filterExamTags } from '../../config/purpose'
import LucideIcon from '../LucideIcon'
import AnnotationGlyph from '../AnnotationGlyph'
import FeedbackSheet from '../FeedbackSystem/FeedbackSheet'
import { getLookupSaveState, getSaveActionCopy } from './lookupSaveState'
import type { SourceRef } from '../../types/view/vocabulary.vm'
import './index.scss'

interface WordPopupProps {
  visible: boolean
  mode?: 'mini' | 'full'
  mark: AnyInlineMarkModel | null
  word: string
  contextSentence?: string
  occurrence?: number
  x?: number
  y?: number
  readingVariant?: string
  readingGoal?: string
  cloudId?: string
  isSaved?: boolean
  savedMasteryStatus?: string
  savedSourceRefs?: SourceRef[]
  currentSentenceId?: string
  onClose: () => void
  onExpand?: () => void
  onAddVocab?: (word: string, dictResult: DictionaryResult | null) => void
}

type HeightTier = 'compact' | 'standard' | 'rich' | 'expanded'
type DictTab = 'meanings' | 'phrases' | 'examples'
type SheetMode = 'answer' | 'dictionary' | 'entry_picker' | 'not_found' | 'lookup_error'
type DictionaryLookupErrorKind = 'network' | 'server' | 'unknown'

interface DefinitionLine {
  text: string
  example?: string
  exampleTranslation?: string
}

interface ExamplePair {
  example: string
  exampleTranslation?: string
}

interface DictionaryLookupError {
  kind: DictionaryLookupErrorKind
  title: string
  message: string
  miniMessage: string
}

interface MeaningGroup {
  partOfSpeech: string
  definitions: DefinitionLine[]
}

interface PrimaryAnswer {
  label: '本文含义' | '短语含义' | '常用释义' | '未收录'
  text: string
  detail?: string
  confidence: 'context' | 'phrase' | 'dictionary' | 'missing'
}

function getEntrySummary(entry: DictionaryEntryPayload | null | undefined): string {
  if (!entry?.meanings?.length) {
    if (entry?.entryKind === 'fragment' && entry.baseWord) {
      return `派生词，详见 ${entry.baseWord}`
    }
    return ''
  }
  return entry.meanings
    .map((m) => {
      const firstDef = m.definitions?.[0]?.meaning
      if (!firstDef) return null
      return m.partOfSpeech ? `${m.partOfSpeech} ${firstDef}` : firstDef
    })
    .filter(Boolean)
    .join('；')
}

function isLearningGlossary(g: InlineGlossary | AcademicInlineGlossary | undefined): g is InlineGlossary {
  return !!g && ('gloss' in g || 'reason' in g || 'phraseType' in g)
}

const TONE_META: Record<VisualTone | AcademicVisualTone, { label: string; color: string; bg: string }> = {
  vocab: { label: '词汇', color: 'var(--tone-vocab-color)', bg: 'var(--tone-vocab-bg)' },
  phrase: { label: '短语', color: 'var(--tone-phrase-color)', bg: 'var(--tone-phrase-bg)' },
  context: { label: '语境', color: 'var(--tone-context-color)', bg: 'var(--tone-context-bg)' },
  grammar: { label: '语法', color: 'var(--tone-grammar-color)', bg: 'var(--tone-grammar-bg)' },
  term: { label: '术语', color: 'var(--tone-term-color)', bg: 'var(--tone-term-bg)' },
  logic: { label: '逻辑', color: 'var(--tone-logic-color)', bg: 'var(--tone-logic-bg)' },
}

const PHRASE_KIND_LABELS: Record<string, string> = {
  phrase: '短语',
  collocation: '固定搭配',
  phrasal_verb: '动词短语',
  idiom: '习语',
  proper_noun: '专有名词',
  compound: '复合概念',
}

const MINI_LABEL_MAP: Record<string, string> = {
  vocab: '词汇',
  context: '语境',
  phrase: '短语',
  collocation: '搭配',
  phrasal_verb: '短语',
  idiom: '习语',
  proper_noun: '专名',
  compound: '复合',
  term: '术语',
  logic: '逻辑',
}

function isBlockingDisambiguation(result: DictionaryResult | null | undefined): result is DictionaryDisambiguationResult {
  return result?.resultType === 'disambiguation' && result.selectionRequired !== false
}

function isSoftDisambiguation(result: DictionaryResult | null | undefined): result is DictionaryDisambiguationResult {
  return result?.resultType === 'disambiguation' && result.selectionRequired === false
}

function getDisambiguationMiniMeaning(result: DictionaryResult | null | undefined): string | undefined {
  if (!isSoftDisambiguation(result)) return undefined
  const first = result.candidates[0]
  if (!first) return undefined
  if (first.preview) return cleanMeaningText(first.preview)
  return first.label ? `可先查看 ${first.label}` : undefined
}

const PROPER_NOUN_POS = new Set(['pn', 'propn', 'proper_noun', 'proper noun', '专名', '专有名词'])

function classifyLookupError(error: unknown): DictionaryLookupError {
  if (error instanceof ApiError) {
    if (error.statusCode === 0 || error.code === 'NETWORK_ERROR' || error.code === 'TIMEOUT') {
      return {
        kind: 'network',
        title: '网络连接异常',
        message: '暂时无法连接词典服务，请检查网络后再试。',
        miniMessage: '网络异常，稍后重试',
      }
    }
    if (error.statusCode >= 500) {
      return {
        kind: 'server',
        title: '词典服务暂不可用',
        message: '服务端查询失败，请稍后重试；这不是词库未收录。',
        miniMessage: '词典服务暂不可用',
      }
    }
  }

  return {
    kind: 'unknown',
    title: '查询失败',
    message: '暂时无法完成词典查询，请稍后重试。',
    miniMessage: '查询失败，稍后重试',
  }
}

function cleanMeaningText(text: string): string {
  return text
    .replace(/\s+/g, ' ')
    .replace(/;+/g, '；')
    .replace(/；\s*/g, '；')
    .trim()
}

function splitDefinitionText(text: string, maxParts = 3): string[] {
  const cleaned = cleanMeaningText(text)
  if (!cleaned) return []
  const parts = cleaned
    .split(/[；;]+/)
    .map((part) => part.trim())
    .filter(Boolean)
  return (parts.length ? parts : [cleaned]).slice(0, maxParts)
}

function splitJoinedText(text: string | undefined): string[] {
  if (!text) return []
  return text
    .split('\uFF1B')
    .map((part) => part.trim())
    .filter(Boolean)
}

function splitExamplePairs(
  example: string | undefined,
  exampleTranslation: string | undefined,
  limit = 2
): ExamplePair[] {
  const exampleParts = splitJoinedText(example)
  if (exampleParts.length <= 1) {
    return example ? [{ example, exampleTranslation }] : []
  }

  const translationParts = splitJoinedText(exampleTranslation)
  const canPairTranslation = translationParts.length === exampleParts.length

  return exampleParts.slice(0, limit).map((part, idx) => ({
    example: part,
    exampleTranslation: canPairTranslation ? translationParts[idx] : undefined,
  }))
}

function buildMeaningGroups(entry: DictionaryEntryPayload | null | undefined, expanded: boolean): MeaningGroup[] {
  if (!entry?.meanings?.length) return []
  const maxPosGroups = expanded ? entry.meanings.length : 1
  const maxDefsPerGroup = expanded ? 8 : 3

  return entry.meanings.slice(0, maxPosGroups).map((meaning) => ({
    partOfSpeech: meaning.partOfSpeech || '',
    definitions: meaning.definitions
      .flatMap((def) => splitDefinitionText(def.meaning, expanded ? 6 : 3).map((part, idx) => ({
        text: part,
        example: idx === 0 ? def.example : undefined,
        exampleTranslation: idx === 0 ? def.exampleTranslation : undefined,
      })))
      .slice(0, maxDefsPerGroup),
  })).filter((group) => group.definitions.length > 0)
}

function getPrimaryAnswer(
  entry: DictionaryEntryPayload | null,
  glossary: InlineGlossary | AcademicInlineGlossary | undefined,
  professionalLabel: string
): PrimaryAnswer {
  const glossaryText = glossary?.zh || (isLearningGlossary(glossary) ? glossary.gloss : '')
  const glossaryReason = isLearningGlossary(glossary) ? glossary.reason : undefined

  if (glossaryText) {
    return {
      label: professionalLabel.includes('短语') || professionalLabel.includes('搭配') ? '短语含义' : '本文含义',
      text: cleanMeaningText(glossaryText),
      detail: glossaryReason ? cleanMeaningText(glossaryReason) : undefined,
      confidence: professionalLabel.includes('短语') || professionalLabel.includes('搭配') ? 'phrase' : 'context',
    }
  }

  const firstDef = entry?.meanings?.[0]?.definitions?.[0]?.meaning
  if (firstDef) {
    return {
      label: '常用释义',
      text: splitDefinitionText(firstDef, 2).join('；'),
      confidence: 'dictionary',
    }
  }

  return {
    label: '未收录',
    text: '本地词库暂未收录',
    confidence: 'missing',
  }
}

function getExpandCopy(entry: DictionaryEntryPayload | null): string {
  if (!entry) return '查看更多释义'
  if ((entry.phrases?.length || 0) > 0 || (entry.examples?.length || 0) > 0) return '查看短语和例句'
  return '查看更多释义'
}

function isLikelyProperCandidate(candidate: { label: string; partOfSpeech?: string }, query: string): boolean {
  const pos = candidate.partOfSpeech?.toLowerCase().trim()
  if (pos && PROPER_NOUN_POS.has(pos)) return true
  return query === query.toLowerCase() && candidate.label !== candidate.label.toLowerCase()
}

function WordLookupSlip({
  lookupText,
  dictResult,
  loading,
  miniMeaning,
  miniLabel,
  isLLMAnnotated,
  isDisambiguationResult,
  isSavedState,
  saveBtnCopy,
  mark,
  x,
  y,
  screenWidth,
  screenHeight,
  onClose,
  onExpand,
  onAddVocab,
}: {
  lookupText: string
  dictResult: DictionaryResult | null
  loading: boolean
  miniMeaning?: string
  miniLabel: string
  isLLMAnnotated: boolean
  isDisambiguationResult: boolean
  isSavedState: boolean
  saveBtnCopy: string
  mark: AnyInlineMarkModel | null
  x: number
  y: number
  screenWidth: number
  screenHeight: number
  onClose: () => void
  onExpand?: () => void
  onAddVocab?: (word: string, dictResult: DictionaryResult | null) => void
  onSelectEntry?: (entryId: number, expand?: boolean) => void
}) {
  const popupWidth = (screenWidth * 408) / 750
  const offset = 18
  let left = x - popupWidth / 2
  let top = y + offset
  let isFlipped = false

  if (left < 20) left = 20
  if (left + popupWidth > screenWidth - 20) left = screenWidth - popupWidth - 20

  if (y + 300 > screenHeight) {
    top = y - offset
    isFlipped = true
  }

  const popupStyle: React.CSSProperties = {
    position: 'fixed',
    left: `${left}px`,
    top: `${top}px`,
    zIndex: 1000,
    width: `${popupWidth}px`,
    transform: isFlipped ? 'translateY(-100%)' : 'none',
  }

  const entry = dictResult?.resultType === 'entry' ? dictResult.entry : null
  const headword = entry?.word || lookupText

  return (
    <View className='word-popup-overlay mini-overlay' onClick={onClose} onTouchMove={onClose}>
      <View
        className={`mini-word-card ${isLLMAnnotated ? 'is-ai' : ''} ${isFlipped ? 'is-flipped' : ''}`}
        style={popupStyle}
      >
        <View className='mini-main-content' onClick={(e) => {
          e.stopPropagation()
          onExpand?.()
        }}>
          <View className='mini-header'>
            <Text className='mini-word'>{headword}</Text>
          </View>

          {(entry?.phonetic || (isLLMAnnotated && mark)) && (
            <View className='mini-sub-info'>
              {entry?.phonetic && (
                <View className='mini-phonetic-row'>
                  <Text className='mini-phonetic'>/{entry.phonetic}/</Text>
                </View>
              )}
              {isLLMAnnotated && mark && (
                <View className='ai-tag'>
                  <AnnotationGlyph type={mark.visualTone as any} size='sm' state='active' />
                  <Text className='ai-tag-text'>{miniLabel}</Text>
                </View>
              )}
            </View>
          )}

          <View className='mini-content'>
            {loading && !miniMeaning ? (
              <View>
                <View className='mini-skeleton-line' />
                <View className='mini-skeleton-line' />
              </View>
            ) : (
              <View className='mini-def-row'>
                <Text
                  className={`mini-def ${isLLMAnnotated ? 'is-ai-def' : ''}`}
                  numberOfLines={2}
                >
                  {miniMeaning || (isDisambiguationResult ? '找到多个词条，点开选择' : '暂未找到稳定释义，查看上下文')}
                </Text>
              </View>
            )}
          </View>
        </View>

        {entry && entry.id > 0 && (
          <View
            className={`mini-action-bar ${isSavedState ? 'saved' : 'not-saved'}`}
            onClick={(e) => {
              e.stopPropagation()
              onAddVocab?.(entry.word, dictResult)
            }}
          >
            <View className='mini-action-left'>
              <AnnotationGlyph type='saved_vocab' size={24} state={isSavedState ? 'active' : 'default'} />
              <Text className='mini-action-text'>{saveBtnCopy}</Text>
            </View>
            <LucideIcon name='chevron-right' size={14} color={isSavedState ? 'var(--reader-subtle)' : 'var(--reader-muted)'} />
          </View>
        )}

        <View className='mini-arrow' style={{ left: `${Math.max(20, Math.min(popupWidth - 20, x - left))}px` }} />
      </View>
    </View>
  )
}

function DictionaryNoteSheet({
  lookupText,
  dictResult,
  softDisambiguation,
  lookupError,
  loading,
  glossary,
  mark,
  professionalLabel,
  contextSentence,
  readingGoal,
  readingVariant,
  activeTab,
  isSavedState,
  saveBtnCopy,
  setActiveTab,
  onClose,
  onAddVocab,
  onSelectEntry,
  setShowDictFeedback,
  renderContextExcerpt,
}: {
  lookupText: string
  dictResult: DictionaryResult | null
  softDisambiguation: DictionaryDisambiguationResult | null
  lookupError: DictionaryLookupError | null
  loading: boolean
  glossary: InlineGlossary | AcademicInlineGlossary | undefined
  mark: AnyInlineMarkModel | null
  professionalLabel: string
  contextSentence?: string
  readingGoal?: string
  readingVariant?: string
  activeTab: DictTab
  isSavedState: boolean
  saveBtnCopy: string
  setActiveTab: (tab: DictTab) => void
  onClose: () => void
  onAddVocab?: (word: string, dictResult: DictionaryResult | null) => void
  onSelectEntry?: (entryId: number) => void
  setShowDictFeedback: (v: boolean) => void
  renderContextExcerpt: () => React.ReactNode
}) {
  const entry = dictResult?.resultType === 'entry' ? dictResult.entry : null
  const isDisambiguationResult = isBlockingDisambiguation(dictResult)
  const isEntryResult = dictResult?.resultType === 'entry'
  const isNotFoundResult = dictResult?.resultType === 'not_found'
  const hasGlossary = !!glossary

  const [dragY, setDragY] = useState(0)
  const [sheetMode, setSheetMode] = useState<SheetMode>('answer')
  const startYRef = useRef(0)
  const lastDeltaYRef = useRef(0)
  const isDraggingRef = useRef(false)
  const isDictionaryMode = sheetMode === 'dictionary'
  const isEntryPickerMode = sheetMode === 'entry_picker'
  const isNotFoundMode = sheetMode === 'not_found'
  const isLookupErrorMode = sheetMode === 'lookup_error'
  const canExpandSheet = sheetMode === 'answer' && isEntryResult

  const heightTier: HeightTier = useMemo(() => {
    if (isDictionaryMode) return 'expanded'
    if (isEntryPickerMode) return 'standard'
    if (loading) return 'compact'
    if (isLookupErrorMode) return 'compact'
    if (isNotFoundMode) return 'compact'
    if (!entry) return 'compact'
    if (hasGlossary || contextSentence) return 'standard'
    return 'compact'
  }, [entry, loading, contextSentence, hasGlossary, isDictionaryMode, isEntryPickerMode, isNotFoundMode, isLookupErrorMode])

  useEffect(() => {
    if (lookupError && !hasGlossary) {
      setSheetMode('lookup_error')
    } else if (isBlockingDisambiguation(dictResult)) {
      setSheetMode('entry_picker')
    } else if (dictResult?.resultType === 'not_found') {
      setSheetMode('not_found')
    } else {
      setSheetMode('answer')
    }
    setDragY(0)
    lastDeltaYRef.current = 0
  }, [lookupText, dictResult?.resultType, lookupError, hasGlossary])

  const showSaveAction = !!(isEntryResult && entry && entry.id > 0 && !isEntryPickerMode && !isNotFoundMode && !isLookupErrorMode)
  const sheetMetrics = useMemo(() => {
    const windowInfo = Taro.getWindowInfo()
    const windowHeight = windowInfo.windowHeight || 667
    const tierRatios: Record<HeightTier, number> = {
      compact: 0.48,
      standard: 0.6,
      rich: 0.66,
      expanded: 0.68,
    }
    const maxHeight = Math.round(windowHeight * tierRatios[heightTier])
    const headerReserve = isDictionaryMode ? 126 : 150
    const footerReserve = showSaveAction ? 92 : 34
    const scrollMaxHeight = Math.max(220, maxHeight - headerReserve - footerReserve)

    return { maxHeight, scrollMaxHeight }
  }, [heightTier, showSaveAction, isDictionaryMode])

  const expandDictionary = (tab: DictTab = 'meanings') => {
    setActiveTab(tab)
    setSheetMode('dictionary')
  }

  const collapseDictionary = () => {
    setActiveTab('meanings')
    setSheetMode('answer')
  }

  const handleTouchStart = (e: any) => {
    startYRef.current = e.touches[0].clientY
    isDraggingRef.current = true
  }

  const handleTouchMove = (e: any) => {
    if (!isDraggingRef.current) return
    const currentY = e.touches[0].clientY
    const deltaY = currentY - startYRef.current
    lastDeltaYRef.current = deltaY
    if (deltaY > 0) {
      setDragY(deltaY)
    } else {
      setDragY(0)
    }
  }

  const handleTouchEnd = () => {
    if (!isDraggingRef.current) return
    isDraggingRef.current = false
    if (dragY > 80) {
      onClose()
    } else if (lastDeltaYRef.current < -60 && canExpandSheet) {
      setSheetMode('dictionary')
      setDragY(0)
    } else {
      setDragY(0)
    }
    lastDeltaYRef.current = 0
  }

  const softCandidate = isSoftDisambiguation(dictResult) ? dictResult.candidates[0] : undefined
  const primaryAnswer: PrimaryAnswer = softCandidate && !entry
    ? {
      label: '常用释义',
      text: cleanMeaningText(softCandidate.preview || softCandidate.label),
      confidence: 'dictionary',
    }
    : getPrimaryAnswer(entry, glossary, professionalLabel)
  const answerGroups = buildMeaningGroups(entry, false)
  const detailGroups = buildMeaningGroups(entry, true)
  const showContextAnswer = primaryAnswer.confidence === 'context' || primaryAnswer.confidence === 'phrase'
  const hasDictionaryTabs = isEntryResult && entry && ((entry.phrases?.length || 0) > 0 || (entry.examples?.length || 0) > 0)
  const shouldShowExpandDictionary = isEntryResult && entry && sheetMode === 'answer' && (
    entry.meanings.length > 1 ||
    entry.meanings.some((meaning) => meaning.definitions.length > 1) ||
    (entry.phrases?.length || 0) > 0 ||
    (entry.examples?.length || 0) > 0
  )
  const sortedCandidates = isDisambiguationResult
    ? [...dictResult.candidates].sort((a, b) => {
      const aProper = isLikelyProperCandidate(a, lookupText)
      const bProper = isLikelyProperCandidate(b, lookupText)
      if (aProper !== bProper) return aProper ? 1 : -1
      const aExact = a.label.toLowerCase() === lookupText.toLowerCase()
      const bExact = b.label.toLowerCase() === lookupText.toLowerCase()
      if (aExact !== bExact) return aExact ? -1 : 1
      return 0
    })
    : []
  const alternativeCandidates = softDisambiguation?.candidates.filter((candidate) => candidate.entryId !== entry?.id) ?? []

  const renderDictionaryTabs = () => {
    if (!isEntryResult || !entry || !hasDictionaryTabs) return null
    return (
      <View className='dict-tabs'>
        <View className={`dict-tab ${activeTab === 'meanings' ? 'active' : ''}`} onClick={() => setActiveTab('meanings')}>释义</View>
        {entry.phrases?.length > 0 && <View className={`dict-tab ${activeTab === 'phrases' ? 'active' : ''}`} onClick={() => setActiveTab('phrases')}>短语</View>}
        {entry.examples?.length > 0 && <View className={`dict-tab ${activeTab === 'examples' ? 'active' : ''}`} onClick={() => setActiveTab('examples')}>例句</View>}
      </View>
    )
  }

  const renderMeaningGroups = (groups: MeaningGroup[], includeExamples: boolean) => (
    <View className='meanings-list'>
      {groups.map((meaning, idx) => {
        const examples = includeExamples
          ? meaning.definitions
            .flatMap((def) => splitExamplePairs(def.example, def.exampleTranslation, 2))
            .slice(0, 2)
          : []

        return (
          <View key={`${meaning.partOfSpeech}-${idx}`} className='meaning-item'>
            <View className='pos-column'>
              {meaning.partOfSpeech && <Text className='pos-tag'>{meaning.partOfSpeech}</Text>}
            </View>
            <View className='definitions'>
              {meaning.definitions.map((def, defIdx) => (
                <View key={`${def.text.slice(0, 20)}-${defIdx}`} className='def-row'>
                  <Text className='def-text'>{def.text}</Text>
                </View>
              ))}
              {examples.length > 0 && (
                <View className='meaning-example-list'>
                  {examples.map((example, exampleIdx) => (
                    <View key={`${example.example?.slice(0, 18)}-${exampleIdx}`} className='meaning-example-item'>
                      <Text className='def-example-en' numberOfLines={2}>{example.example}</Text>
                      {example.exampleTranslation && (
                        <Text className='def-example-zh' numberOfLines={2}>{example.exampleTranslation}</Text>
                      )}
                    </View>
                  ))}
                </View>
              )}
            </View>
          </View>
        )
      })}
    </View>
  )

  return (
    <View className='word-popup-overlay full-overlay' onClick={onClose} catchMove>
      <View
        className={`word-popup-container tier-${heightTier} mode-${sheetMode} confidence-${primaryAnswer.confidence} ${hasGlossary ? 'has-glossary' : 'plain-dict'}`}
        onClick={(e) => e.stopPropagation()}
        style={{
          maxHeight: `${sheetMetrics.maxHeight}px`,
          transform: dragY > 0 ? `translateY(${dragY}px)` : '',
          transition: dragY > 0 ? 'none' : 'transform 0.28s var(--ease-reader-out)'
        }}
      >
        <View
          className='popup-header-touch-area'
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
        >
          <View className='popup-drag-handle' />
          <View className='popup-header'>
            <View className='word-info'>
              <View className='word-text-row'>
                <Text className='word-text'>{entry?.word || lookupText}</Text>
              </View>
              <View className='word-sub-info'>
                {entry?.phonetic && (
                  <View className='phonetic-row'>
                    <Text className='word-phonetic'>/{entry.phonetic}/</Text>
                  </View>
                )}
                {readingGoal === 'exam' && entry?.tags && entry.tags.length > 0 && (() => {
                  const filtered = filterExamTags(entry.tags, readingVariant)
                  return filtered.length > 0 ? (
                    <View className='exam-tags-row'>
                      {filtered.map(tag => (
                        <Text key={tag} className='exam-tag-pill'>{tag}</Text>
                      ))}
                    </View>
                  ) : null
                })()}
              </View>
            </View>
            <View className='header-right-actions'>
              <View className='popup-feedback-btn' onClick={() => setShowDictFeedback(true)}>
                <LucideIcon name='messageSquare' size={18} color='var(--reader-muted)' />
              </View>
              <View className='popup-close-btn' onClick={onClose}>
                <LucideIcon name='x' size={22} color='var(--reader-muted)' />
              </View>
            </View>
          </View>
        </View>

        <ScrollView
          className='popup-scroll-content'
          scrollY
          enhanced
          showScrollbar={false}
          style={{ maxHeight: `${sheetMetrics.scrollMaxHeight}px` }}
        >
          {isEntryPickerMode && isDisambiguationResult ? (
            <View className='entry-picker-panel'>
              <View className='answer-kicker'>
                <AnnotationGlyph type={mark?.visualTone as any || 'context'} size='sm' state='active' />
                <Text>找到多个词条</Text>
              </View>
              <Text className='picker-copy'>选一个继续查看。</Text>
              {contextSentence && renderContextExcerpt()}
              <View className='candidate-list'>
                {loading && (
                  <View className='disambiguation-loading-overlay'>
                    <View className='sheet-skeleton-line' style={{ width: '72%' }} />
                  </View>
                )}
                {sortedCandidates.map((candidate) => {
                  const isProper = candidate.candidateKind === 'proper_noun' || isLikelyProperCandidate(candidate, lookupText)
                  const kindCopy = candidate.candidateKind === 'phrase'
                    ? '短语'
                    : candidate.candidateKind === 'variant'
                      ? '变形'
                      : candidate.candidateKind === 'fragment'
                        ? '片段词条'
                        : isProper
                          ? '专名词条'
                          : '普通词'
                  return (
                    <View
                      key={candidate.entryId}
                      className={`candidate-item ${loading ? 'is-loading' : ''} ${isProper ? 'is-proper' : 'is-ordinary'}`}
                      onClick={() => {
                        if (loading) return
                        onSelectEntry?.(candidate.entryId)
                      }}
                    >
                      <View className='candidate-main'>
                        <View className='candidate-title-row'>
                          <Text className='candidate-label'>{candidate.label}</Text>
                          {candidate.partOfSpeech && <Text className='candidate-pos'>{candidate.partOfSpeech}</Text>}
                          <Text className='candidate-kind'>{kindCopy}</Text>
                        </View>
                        {candidate.preview && <View className='candidate-preview'>{candidate.preview}</View>}
                      </View>
                      <LucideIcon name='chevron-right' size={16} color='var(--reader-muted)' />
                    </View>
                  )
                })}
              </View>
            </View>
          ) : isLookupErrorMode && lookupError ? (
            <View className={`not-found-panel lookup-error-panel is-${lookupError.kind}`}>
              <View className='answer-kicker'>
                <Text>词典查询</Text>
              </View>
              <Text className='not-found-title'>{lookupError.title}</Text>
              <Text className='not-found-copy'>{lookupError.message}</Text>
              {contextSentence && renderContextExcerpt()}
            </View>
          ) : isNotFoundMode ? (
            <View className='not-found-panel'>
              <View className='answer-kicker'>
                <Text>{primaryAnswer.label}</Text>
              </View>
              <Text className='not-found-title'>本地词库暂未收录</Text>
              <Text className='not-found-copy'>你仍可继续阅读；如果这里应该有释义，可以提交反馈。</Text>
              {contextSentence && renderContextExcerpt()}
            </View>
          ) : isDictionaryMode ? (
            <>
              {showContextAnswer && (
                <View className='context-hint-row' onClick={collapseDictionary}>
                  <Text className='context-hint-label'>{primaryAnswer.label}</Text>
                  <Text className='context-hint-text' numberOfLines={1}>{primaryAnswer.text}</Text>
                  <LucideIcon name='chevronDown' size={14} color='var(--reader-muted)' />
                </View>
              )}
              <View className='dict-section is-detail'>
                <View className='detail-tabs-row'>
                  {renderDictionaryTabs()}
                </View>
                {loading ? (
                  <View className='popup-loading-state'>
                    <View className='sheet-skeleton-line' style={{ width: '60%', marginBottom: '16rpx' }} />
                    <View className='sheet-skeleton-line' style={{ width: '100%', marginBottom: '16rpx' }} />
                    <View className='sheet-skeleton-line' style={{ width: '80%' }} />
                  </View>
                ) : isEntryResult && entry ? (
                  <View className='dict-content-area'>
                    {activeTab === 'meanings' && renderMeaningGroups(detailGroups, true)}
                    {activeTab === 'phrases' && (
                      <View className='phrases-list'>
                        {entry.phrases.map((p) => (
                          <View key={p.phrase} className='phrase-item'>
                            <View className='phrase-text'>{p.phrase}</View>
                            {p.meaning && <View className='phrase-meaning'>{p.meaning}</View>}
                          </View>
                        ))}
                      </View>
                    )}
                    {activeTab === 'examples' && (
                      <View className='examples-list'>
                        {entry.examples.map((ex, idx) => (
                          <View key={`${ex.example?.slice(0, 20)}-${idx}`} className='example-item'>
                            <View className='example-en'>{ex.example}</View>
                            {ex.exampleTranslation && <View className='example-zh'>{ex.exampleTranslation}</View>}
                          </View>
                        ))}
                      </View>
                    )}
                  </View>
                ) : null}
              </View>
            </>
          ) : (
            <>
              {showContextAnswer && (
                <View className='answer-panel'>
                  <View className='answer-kicker'>
                    <AnnotationGlyph type={mark?.visualTone as any || 'context'} size='sm' state='active' />
                    <Text>{primaryAnswer.label}</Text>
                  </View>
                  <Text className='answer-text'>{primaryAnswer.text}</Text>
                  {primaryAnswer.detail && <Text className='answer-detail'>{primaryAnswer.detail}</Text>}
                  {contextSentence && renderContextExcerpt()}
                </View>
              )}

              <View className='dict-section is-compact'>
                <View className='section-title-row'>
                  <View className='section-title'>
                    <Text>词典补充</Text>
                  </View>
                </View>
                {loading ? (
                  <View className='popup-loading-state'>
                    <View className='sheet-skeleton-line' style={{ width: '60%', marginBottom: '16rpx' }} />
                    <View className='sheet-skeleton-line' style={{ width: '100%', marginBottom: '16rpx' }} />
                    <View className='sheet-skeleton-line' style={{ width: '80%' }} />
                  </View>
                ) : isEntryResult && entry ? (
                  <View className='dict-content-area'>
                    {renderMeaningGroups(answerGroups, false)}
                    {shouldShowExpandDictionary && (
                      <View className='dict-expand-row' onClick={() => expandDictionary('meanings')}>
                        <Text>{getExpandCopy(entry)}</Text>
                        <LucideIcon name='chevronUp' size={16} color='var(--reader-muted)' />
                      </View>
                    )}
                    {alternativeCandidates.length > 0 && (
                      <View className='alternate-senses'>
                        <Text className='alternate-senses-title'>其他义项</Text>
                        {alternativeCandidates.slice(0, 3).map((candidate) => (
                          <View
                            key={candidate.entryId}
                            className='alternate-sense-row'
                            onClick={() => onSelectEntry?.(candidate.entryId)}
                          >
                            <View className='alternate-sense-main'>
                              <Text className='alternate-sense-label'>{candidate.label}</Text>
                              {candidate.partOfSpeech && <Text className='alternate-sense-pos'>{candidate.partOfSpeech}</Text>}
                            </View>
                            {candidate.preview && <Text className='alternate-sense-preview' numberOfLines={1}>{candidate.preview}</Text>}
                          </View>
                        ))}
                      </View>
                    )}
                  </View>
                ) : !loading && !softCandidate && (
                  <View className='popup-empty-state'>
                    <Text className='empty-text'>{lookupError ? lookupError.message : (isNotFoundResult ? '本地词库暂未收录' : '未找到词条释义')}</Text>
                  </View>
                )}
              </View>
            </>
          )}
        </ScrollView>

        <View className={`popup-footer-actions safe-area-bottom ${showSaveAction ? 'has-footer' : 'no-footer'}`}>
          {showSaveAction && (
            <View
              className={`footer-action-btn save-action ${isSavedState ? 'saved' : 'primary'}`}
              onClick={() => onAddVocab?.(entry!.word, dictResult)}
            >
              <AnnotationGlyph type='saved_vocab' size={30} state={isSavedState ? 'active' : 'default'} />
              <Text>{saveBtnCopy}</Text>
            </View>
          )}
        </View>
      </View>
    </View>
  )
}

export default function WordPopup({
  visible, mode = 'mini', mark, word, contextSentence, occurrence, x = 0, y = 0, readingVariant, readingGoal,
  cloudId, isSaved = false, savedMasteryStatus, savedSourceRefs, currentSentenceId, onClose, onExpand, onAddVocab,
}: WordPopupProps) {
  const [dictResult, setDictResult] = useState<DictionaryResult | null>(null)
  const [softDisambiguation, setSoftDisambiguation] = useState<DictionaryDisambiguationResult | null>(null)
  const [lookupError, setLookupError] = useState<DictionaryLookupError | null>(null)
  const [loading, setLoading] = useState(false)
  const [screenWidth, setScreenWidth] = useState(375)
  const [screenHeight, setScreenHeight] = useState(667)
  const [activeTab, setActiveTab] = useState<'meanings' | 'phrases' | 'examples'>('meanings')
  const [showDictFeedback, setShowDictFeedback] = useState(false)
  const fetchVersionRef = useRef(0)

  const lookupText = mark?.lookupText || word
  const glossary = mark?.glossary
  const toneMeta = mark ? TONE_META[mark.visualTone] : null

  const effectivePhraseKind = isLearningGlossary(glossary) ? glossary.phraseType : undefined
  const effectiveLookupKind = 'lookupKind' in (mark ?? {}) ? mark!.lookupKind : undefined
  const professionalLabel = ((effectivePhraseKind || effectiveLookupKind) && PHRASE_KIND_LABELS[effectivePhraseKind || effectiveLookupKind || ''])
    ? PHRASE_KIND_LABELS[effectivePhraseKind || effectiveLookupKind || '']
    : (toneMeta?.label || 'AI 解析')

  const miniLabel = (effectivePhraseKind && MINI_LABEL_MAP[effectivePhraseKind])
    ? MINI_LABEL_MAP[effectivePhraseKind]
    : (mark ? MINI_LABEL_MAP[mark.visualTone] : 'AI')

  const entry = dictResult?.resultType === 'entry' ? dictResult.entry : null
  const notFoundMessage = dictResult?.resultType === 'not_found' ? '本地词库暂未收录' : undefined
  const miniMeaning = glossary?.zh || (isLearningGlossary(glossary) ? glossary.gloss : undefined) || notFoundMessage || lookupError?.miniMessage || getEntrySummary(entry) || getDisambiguationMiniMeaning(dictResult)
  const isLLMAnnotated = !!glossary

  const renderContextExcerpt = () => {
    if (!contextSentence || !lookupText) return null
    const escaped = lookupText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const parts = contextSentence.split(new RegExp(`(${escaped})`, 'gi'))
    return (
      <View className='source-context-excerpt'>
        {parts.map((part, i) =>
          part.toLowerCase() === lookupText.toLowerCase()
            ? <Text key={i} className='excerpt-highlight'>{part}</Text>
            : <Text key={i}>{part}</Text>
        )}
      </View>
    )
  }

  const saveState = getLookupSaveState(lookupText, isSaved, currentSentenceId, savedSourceRefs, savedMasteryStatus === 'mastered')
  const saveBtnCopy = getSaveActionCopy(saveState, savedSourceRefs?.length)
  const isSavedState = saveState !== 'not_saved'

  useEffect(() => {
    if (!visible) {
      setDictResult(null)
      setSoftDisambiguation(null)
      setLookupError(null)
      return
    }
    if (!lookupText) return
    fetchVersionRef.current += 1
    const version = fetchVersionRef.current
    void fetchDictionary(lookupText, version)
  }, [visible, lookupText, contextSentence, occurrence])

  useEffect(() => {
    const windowInfo = Taro.getWindowInfo()
    setScreenWidth(windowInfo.windowWidth || 375)
    setScreenHeight(windowInfo.windowHeight || 667)
  }, [])

  useEffect(() => {
    const isEntryResult = dictResult?.resultType === 'entry'
    if (isEntryResult && entry) {
      if (activeTab === 'phrases' && !entry.phrases?.length) setActiveTab('meanings')
      if (activeTab === 'examples' && !entry.examples?.length) setActiveTab('meanings')
    }
  }, [dictResult, activeTab, entry])

  useEffect(() => {
    if (!visible || mode !== 'full' || !isSoftDisambiguation(dictResult) || loading) return
    const firstCandidate = dictResult.candidates[0]
    if (!firstCandidate) return
    void fetchEntryDetail(firstCandidate.entryId)
  }, [visible, mode, dictResult, loading])

  const fetchDictionary = async (text: string, version: number) => {
    const type = text.trim().includes(' ') ? 'phrase' : 'word'
    const cached = getDictCache(text, type, contextSentence, occurrence)
    if (cached) {
      setDictResult(cached)
      setSoftDisambiguation(null)
      setLookupError(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setDictResult(null)
    setLookupError(null)
    try {
      const dto = await fetchDict(text, type, contextSentence, occurrence)
      if (version !== fetchVersionRef.current) return
      const vm = dictResponseDtoToVm(dto)
      setDictResult(vm)
      setSoftDisambiguation(isSoftDisambiguation(vm) ? vm : null)
      setLookupError(null)
      setDictCache(text, type, vm, contextSentence, occurrence)
    } catch (err) {
      if (version !== fetchVersionRef.current) return
      setDictResult(null)
      setSoftDisambiguation(null)
      setLookupError(classifyLookupError(err))
    } finally {
      if (version === fetchVersionRef.current) {
        setLoading(false)
      }
    }
  }

  const fetchEntryDetail = async (entryId: number, expand = false) => {
    const cached = getEntryCache(entryId)
    if (cached) {
      setDictResult(cached)
      setLookupError(null)
      if (expand) onExpand?.()
      return
    }
    setLoading(true)
    setLookupError(null)
    try {
      const dto = await fetchDictEntry(entryId)
      const vm = dictResponseDtoToVm(dto)
      setDictResult(vm)
      setLookupError(null)
      setEntryCache(entryId, vm)
      if (expand) onExpand?.()
    } catch (err) {
      setLookupError(classifyLookupError(err))
    } finally {
      setLoading(false)
    }
  }

  if (!visible) return null

  const isDisambiguationResult = isBlockingDisambiguation(dictResult)

  if (mode === 'mini') {
    return (
      <WordLookupSlip
        lookupText={lookupText}
        dictResult={dictResult}
        loading={loading}
        miniMeaning={miniMeaning}
        miniLabel={miniLabel}
        isLLMAnnotated={isLLMAnnotated}
        isDisambiguationResult={isDisambiguationResult}
        isSavedState={isSavedState}
        saveBtnCopy={saveBtnCopy}
        mark={mark}
        x={x}
        y={y}
        screenWidth={screenWidth}
        screenHeight={screenHeight}
        onClose={onClose}
        onExpand={onExpand}
        onAddVocab={onAddVocab}
        onSelectEntry={fetchEntryDetail}
      />
    )
  }

  return (
    <>
      <DictionaryNoteSheet
        lookupText={lookupText}
        dictResult={dictResult}
        softDisambiguation={softDisambiguation}
        lookupError={lookupError}
        loading={loading}
        glossary={glossary}
        mark={mark}
        professionalLabel={professionalLabel}
        contextSentence={contextSentence}
        readingGoal={readingGoal}
        readingVariant={readingVariant}
        activeTab={activeTab}
        isSavedState={isSavedState}
        saveBtnCopy={saveBtnCopy}
        setActiveTab={setActiveTab}
        onClose={onClose}
        onAddVocab={onAddVocab}
        onSelectEntry={fetchEntryDetail}
        setShowDictFeedback={setShowDictFeedback}
        renderContextExcerpt={renderContextExcerpt}
      />

      {showDictFeedback && (
        <View className='popup-feedback-overlay' onClick={() => setShowDictFeedback(false)}>
          <FeedbackSheet
            scope='dictionary'
            prefillSentiment='negative'
            payload={{
              targetId: entry?.id ? String(entry.id) : lookupText,
              analysisRecordId: cloudId,
              contextJson: {
                word: lookupText,
                phonetic: entry?.phonetic || '',
                current_meaning: getEntrySummary(entry) || '',
                dict_source: 'tecd3',
                dict_entry_id: entry?.id,
                context_sentence: contextSentence || '',
                reading_variant: readingVariant || '',
              }
            }}
            contextSummary={`${lookupText}${entry?.phonetic ? ` ${entry.phonetic}` : ''}`}
            onClose={() => setShowDictFeedback(false)}
          />
        </View>
      )}
    </>
  )
}
