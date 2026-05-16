import { useMemo, memo, useState, useEffect, useCallback } from 'react'
import Taro from '@tarojs/taro'
import { View, Text } from '@tarojs/components'
import { AnyInlineMarkModel, AnySentenceEntryModel, VisualTone, AcademicVisualTone, SentenceModel, TranslationModel } from '../../types/view/render-scene.vm'
import { UserAnnotationDto } from '../../services/api/user-annotations.client'
import ClickableWord from '../ClickableWord'
import LucideIcon from '../LucideIcon'
import GrammarInlineSpan from '../GrammarInlineSpan'
import InlineMark from '../InlineMark'
import AnalysisCard, { type AnalysisCardProps } from '../AnalysisCard'
import AcademicNoteGroup from '../AcademicNoteGroup'  // 学术注释聚合组
import FeedbackSheet from '../FeedbackSystem/FeedbackSheet'
import { tokenizeText, parseSentenceAnalysis, findFuzzyMatch, tokenizeSentenceWithAnalysis, findTokenOffset } from './utils'
import type { SelectionContext } from '../ReadingSelectionToolbar'
import { getMappedEntryTitle } from '../../utils/entryTitleMapping'
import type { ClickEvent } from '../../types/taro-events'
import type { CommonEvent } from '@tarojs/components/types/common'
import './index.scss'

const TONE_PRIORITY: Record<VisualTone | AcademicVisualTone, number> = {
  vocab: 1,
  phrase: 2,
  context: 3,
  grammar: 4,
  term: 5,
  logic: 6,
}

export interface WordClickPayload {
  word: string
  mark: AnyInlineMarkModel | null
  event?: ClickEvent
  contextSentence?: string
  occurrence?: number
}

interface ParagraphBlockProps {
  order: number
  paragraphId?: string
  sentences: SentenceModel[]
  translations: TranslationModel[]
  inlineMarks: AnyInlineMarkModel[]
  activeMarkId?: string | null
  activeSentenceId?: string | null
  activeSelectionId?: string | null
  selectedWord?: string | null
  tailEntries: AnySentenceEntryModel[]
  pageMode: 'immersive' | 'intensive'
  isAcademicMode?: boolean
  vocabList?: string[]
  vocabSavedMap?: Record<string, string>
  userAnnotations?: UserAnnotationDto[]
  favoritedSentenceIds?: ReadonlySet<string>
  recordId?: string
  cloudId?: string
  selectionSentenceId?: string | null
  selectionRange?: { start: number; end: number } | null
  routeFocusSentenceIds?: ReadonlySet<string>
  routeFocusRangesBySentence?: Record<string, RouteFocusRange[]>
  onWordClick?: (payload: WordClickPayload) => void
  onSentenceClick?: (sentenceId: string) => void
  onSelectionContext?: (context: SelectionContext | null) => void
  onMarkActiveChange?: (markId: string | null) => void
}

interface RouteFocusRange {
  start: number
  end: number
}

function findTextAnchorPosition(text: string, anchorText: string, occurrence = 1): number {
  let count = 0
  let pos = 0
  const safeOccurrence = occurrence || 1
  while (count < safeOccurrence) {
    const idx = text.indexOf(anchorText, pos)
    if (idx === -1) return -1
    count++
    if (count === safeOccurrence) return idx
    pos = idx + 1
  }
  return -1
}

function renderPlainSegmentAsClickableWords(
  plainText: string,
  selectedWord?: string | null,
  vocabSet?: Set<string>,
  onWordClick?: (payload: WordClickPayload) => void,
  vocabSavedMap?: Record<string, string>,
  onTokenLongPress?: (tokenText: string, tokenStart: number, tokenEnd: number, event: CommonEvent) => void,
  selectionRange?: { start: number; end: number } | null,
  baseOffset?: number,
  userRanges?: UserHighlightRange[],
  routeFocusRanges?: RouteFocusRange[],
): React.ReactNode[] {
  if (!plainText) return []
  const tokens = tokenizeText(plainText)
  return tokens.map((token, idx) => {
    const absStart = (baseOffset ?? 0) + token.start
    const absEnd = (baseOffset ?? 0) + token.end
    const isInSelection = selectionRange
      ? absStart < selectionRange.end && absEnd > selectionRange.start
      : false
    const userRange = userRanges?.find(r => absStart < r.end && absEnd > r.start)
    const userHighlightClass = userRange ? `user-highlight-overlay user-highlight-overlay--${userRange.color}` : ''
    const routeFocusClass = routeFocusRanges?.some(r => absStart < r.end && absEnd > r.start)
      ? 'route-focus-overlay'
      : ''

    if (token.type === 'word') {
      const isSaved = vocabSet?.has(token.text.toLowerCase())
      const savedStatus = vocabSavedMap?.[token.text.toLowerCase()]
      return (
        <ClickableWord
          key={`cw-${idx}`}
          word={token.text}
          isSaved={isSaved}
          savedStatus={savedStatus}
          isInSelection={isInSelection}
          className={`${selectedWord === token.text ? 'active' : ''} ${userHighlightClass} ${routeFocusClass}`.trim()}
          onClick={(w, e) => onWordClick?.({ word: w, mark: null, event: e })}
          onLongPress={onTokenLongPress ? (w, e) => onTokenLongPress(w, absStart, absEnd, e) : undefined}
        />
      )
    }
    return <Text key={`p-${idx}`} className={`${isInSelection ? 'in-selection' : ''} ${userHighlightClass} ${routeFocusClass}`.trim()}>{token.text}</Text>
  })
}

function renderTextWithAnalysis(
  text: string,
  chunks: { label: string; text: string }[],
) {
  const atoms = tokenizeSentenceWithAnalysis(text, chunks)
  const seenChunkIds = new Set<string>()

  return (
    <Text className='english-flow sentence-text is-analyzing'>
      {atoms.map((atom, idx) => {
        const colorIndex = atom.chunkId ? parseInt(atom.chunkId.split('-')[1]) % 5 : 0
        let isFirst = false
        if (atom.chunkId && !seenChunkIds.has(atom.chunkId)) {
          seenChunkIds.add(atom.chunkId)
          isFirst = true
        }

        return (
          <Text
            key={idx}
            className={`analysis-atom ${atom.chunkId ? `is-chunk color-type-${colorIndex}` : 'is-gap'}`}
          >
            {isFirst && <Text className='chunk-inline-marker'>{colorIndex + 1}</Text>}
            {atom.text}
          </Text>
        )
      })}
    </Text>
  )
}

const normalizeId = (id: string | null | undefined) => id ? id.replace(/^[^_]+_/, '') : null;

export function getSentenceAnchorId(sentenceId: string): string {
  return `sentence-anchor-${sentenceId}`
}

function normalizeForMatch(value: string | null | undefined): string {
  return (value || '')
    .toLowerCase()
    .replace(/[^\u3400-\u9fff\w\s-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function compactForMatch(value: string): string {
  return value.replace(/\s+/g, '')
}

function includesMatch(haystack: string, needle: string): boolean {
  const compactNeedle = compactForMatch(needle)
  if (compactNeedle.length < 2) return false
  return haystack.includes(needle) || compactForMatch(haystack).includes(compactNeedle)
}

function getAnchorTexts(mark: AnyInlineMarkModel): string[] {
  if (mark.anchor.kind === 'text') return [mark.anchor.anchorText]
  return mark.anchor.parts.map(part => part.anchorText).filter(Boolean)
}

function getMarkTerms(mark: AnyInlineMarkModel): string[] {
  const glossary = mark.glossary as Record<string, unknown> | undefined
  const terms: string[] = [
    mark.lookupText,
    ...getAnchorTexts(mark),
    glossary?.zh,
    glossary?.gloss,
    glossary?.contextDefinition,
    glossary?.termCategory,
    glossary?.logicType,
  ].filter((term): term is string => typeof term === 'string' && term.trim().length > 0)

  if (Array.isArray(glossary?.hedgingWords)) {
    terms.push(...glossary.hedgingWords.filter((term): term is string => typeof term === 'string'))
  }

  return Array.from(new Set(terms.map(normalizeForMatch).filter(Boolean)))
}

function getExpectedAcademicType(entryType: string): 'term_note' | 'logic_note' | null {
  if (entryType === 'term_note') return 'term_note'
  if (entryType === 'logic_note') return 'logic_note'
  return null
}

function scoreMarkForEntry(entry: AnySentenceEntryModel, mark: AnyInlineMarkModel): number {
  const title = normalizeForMatch(entry.title || '')
  const label = normalizeForMatch(entry.label || '')
  const content = normalizeForMatch(entry.content || '')
  let score = 0

  getMarkTerms(mark).forEach(term => {
    if (includesMatch(title, term)) score += 55
    if (includesMatch(label, term)) score += 25
    if (includesMatch(content, term)) score += 18
    if (includesMatch(term, title)) score += 40
  })

  return score
}

function findMarkIdForEntry(entry: AnySentenceEntryModel, marks: AnyInlineMarkModel[]): string | null {
  const entryNormId = normalizeId(entry.id)
  const direct = marks.find(m => m.id === entry.id || normalizeId(m.id) === entryNormId)
  if (direct) return direct.id
  const byParent = marks.find(m => m.parentId === entry.id || normalizeId(m.parentId) === entryNormId)
  if (byParent) return byParent.id

  const expectedType = getExpectedAcademicType(entry.entryType)
  if (!expectedType) return null

  const candidates = marks.filter(mark => (
    mark.annotationType === expectedType ||
    (expectedType === 'term_note' && mark.visualTone === 'term') ||
    (expectedType === 'logic_note' && mark.visualTone === 'logic')
  ))

  if (candidates.length === 1) return candidates[0].id
  if (candidates.length === 0) return null

  const ranked = candidates
    .map(mark => ({ mark, score: scoreMarkForEntry(entry, mark) }))
    .sort((a, b) => b.score - a.score)

  const [best, second] = ranked
  if (!best || best.score < 25) return null
  if (second && best.score === second.score) return null
  return best.mark.id
}

function splitDropCapText(text: string): { letter: string; bodyText: string; letterIndex: number } | null {
  const match = text.match(/[a-zA-Z]/)
  if (!match || match.index === undefined) return null

  const letterIndex = match.index
  return {
    letter: match[0],
    bodyText: `${text.slice(0, letterIndex)}${text.slice(letterIndex + 1)}`,
    letterIndex,
  }
}

function adjustMarkForDropCap(mark: AnyInlineMarkModel, sourceText: string, letterIndex: number): AnyInlineMarkModel | null {
  if (mark.anchor.kind === 'text') {
    const pos = findTextAnchorPosition(sourceText, mark.anchor.anchorText, mark.anchor.occurrence || 1)
    if (pos !== letterIndex) return mark

    const anchorText = mark.anchor.anchorText.slice(1)
    if (!anchorText) return null

    return {
      ...mark,
      anchor: {
        ...mark.anchor,
        anchorText,
      },
    }
  }

  const parts = mark.anchor.parts
    .map(part => {
      const pos = findTextAnchorPosition(sourceText, part.anchorText, part.occurrence || 1)
      if (pos !== letterIndex) return part
      return {
        ...part,
        anchorText: part.anchorText.slice(1),
      }
    })
    .filter(part => part.anchorText.length > 0)

  if (parts.length === 0) return null

  return {
    ...mark,
    anchor: {
      ...mark.anchor,
      parts,
    },
  }
}

function adjustMarksForDropCap(marks: AnyInlineMarkModel[], sourceText: string, letterIndex: number): AnyInlineMarkModel[] {
  return marks
    .map(mark => adjustMarkForDropCap(mark, sourceText, letterIndex))
    .filter((mark): mark is AnyInlineMarkModel => Boolean(mark))
}

interface UserHighlightRange {
  start: number
  end: number
  color: string
  hasNote: boolean
  annotationId: string
}

function normalizeUserHighlightColor(color?: string): string {
  if (color === 'soft_blue') return 'soft_blue'
  if (color === 'soft_purple') return 'soft_purple'
  if (color === 'sage_green' || color === 'soft_green' || color === 'warm_yellow') return 'soft_green'
  return 'soft_green'
}

function rangeMatchesAnyFocus(
  start: number,
  end: number,
  routeFocusRanges?: RouteFocusRange[],
): boolean {
  return routeFocusRanges?.some(range => start < range.end && end > range.start) ?? false
}

function buildUserHighlightRanges(
  text: string,
  annotations: UserAnnotationDto[] | undefined,
  sentenceId: string,
): UserHighlightRange[] {
  if (!annotations?.length) return []
  const ranges: UserHighlightRange[] = []
  annotations.forEach(a => {
    if (a.anchor_type === 'multi_text' && Array.isArray(a.segments)) {
      a.segments.forEach(segment => {
        if (segment.sentence_id !== sentenceId) return
        const start = Math.max(0, segment.start_offset)
        const end = Math.min(text.length, segment.end_offset)
        if (end > start) {
          ranges.push({ start, end, color: normalizeUserHighlightColor(a.color), hasNote: !!a.note, annotationId: a.id })
        }
      })
      return
    }
    if (a.sentence_id !== sentenceId) return
    // text_range with valid offsets
    if (a.anchor_type === 'text_range' && typeof a.start_offset === 'number' && typeof a.end_offset === 'number') {
      const start = Math.max(0, a.start_offset)
      const end = Math.min(text.length, a.end_offset)
      if (end > start) {
        ranges.push({ start, end, color: normalizeUserHighlightColor(a.color), hasNote: !!a.note, annotationId: a.id })
      }
      return
    }
    // sentence-level fallback: no offset means whole sentence — handled by caller via CSS
  })
  // Sort and merge overlapping ranges of same color to avoid visual clutter
  ranges.sort((a, b) => a.start - b.start || b.end - a.end)
  const merged: UserHighlightRange[] = []
  for (const r of ranges) {
    const last = merged[merged.length - 1]
    if (last && r.start <= last.end && r.color === last.color) {
      last.end = Math.max(last.end, r.end)
      last.hasNote = last.hasNote || r.hasNote
    } else {
      merged.push({ ...r })
    }
  }
  return merged
}

function renderTextWithMarks(
  text: string,
  marks: AnyInlineMarkModel[],
  activeMarkId?: string | null,
  selectedWord?: string | null,
  vocabSet?: Set<string>,
  onWordClick?: (payload: WordClickPayload) => void,
  isImmersive?: boolean,
  isHighlighted?: boolean,
  vocabSavedMap?: Record<string, string>,
  isDropCap?: boolean,
  isAcademicMode?: boolean,
  groupActiveIds?: Set<string>,
  onTokenLongPress?: (tokenText: string, tokenStart: number, tokenEnd: number, event: CommonEvent) => void,
  selectionRange?: { start: number; end: number } | null,
  userAnnotations?: UserAnnotationDto[],
  sentenceId?: string,
  routeFocusRanges?: RouteFocusRange[],
) {
  // 用于追踪单词在整句中的出现次数
  const wordOccurrenceMap: Record<string, number> = {}
  let dropCapHandled = !isDropCap

  const handleDropCap = (textToProcess: string, renderCallback: (rest: string, offsetAdjust: number) => React.ReactNode) => {
    if (dropCapHandled) return renderCallback(textToProcess, 0)

    const match = textToProcess.match(/[a-zA-Z]/)
    if (!match || match.index === undefined) return renderCallback(textToProcess, 0)

    dropCapHandled = true
    const index = match.index
    const prefix = textToProcess.slice(0, index)
    const firstLetter = match[0]
    const rest = textToProcess.slice(index + 1)

    return (
      <Text>
        {prefix}
        <Text className='drop-cap'>{firstLetter}</Text>
        {renderCallback(rest, index + 1)}
      </Text>
    )
  }

  const handleWordClick = (payload: WordClickPayload) => {
    // 在整句中计算点击词的 occurrence
    // 由于前端分词和后端 spaCy 分词可能略有差异，这里我们采取一种简单的“第几次出现”策略
    const word = payload.word.toLowerCase()
    // 我们需要重新扫描一遍 text 来确定这个 payload.word 在整个句子中的位置
    // 但更简单的方法是在渲染时就给每个 ClickableWord 分配一个 occurrence
    onWordClick?.({ ...payload, contextSentence: text })
  }

  // 改进：为了精确计算 occurrence，我们需要在渲染过程中动态计数
  const getNextOccurrence = (word: string) => {
    const w = word.toLowerCase()
    wordOccurrenceMap[w] = (wordOccurrenceMap[w] || 0) + 1
    return wordOccurrenceMap[w]
  }

  // 沉浸模式下只保留词汇相关的标记（vocab, phrase, context）
  const visibleMarks = isImmersive
    ? marks.filter(m => ['vocab', 'phrase', 'context', 'term', 'logic'].includes(m.visualTone))
    : marks

  const userRanges = buildUserHighlightRanges(text, userAnnotations, sentenceId || '')

  // Build AI mark flat parts
  const flatParts: Array<{ mark: AnyInlineMarkModel; start: number; end: number; text: string; role?: string }> = []

  visibleMarks.forEach((m) => {
    if (m.anchor.kind === 'text') {
      const pos = findTextAnchorPosition(text, m.anchor.anchorText, m.anchor.occurrence || 1)
      if (pos >= 0) {
        flatParts.push({ mark: m, start: pos, end: pos + m.anchor.anchorText.length, text: m.anchor.anchorText })
      }
    } else {
      m.anchor.parts.forEach((part, idx) => {
        const pos = findTextAnchorPosition(text, part.anchorText, part.occurrence || 1)
        if (pos >= 0) {
          const partMark: AnyInlineMarkModel = {
            ...m,
            id: `${m.id}-part-${idx}`,
            parentId: m.id,
            anchor: {
              kind: 'text',
              sentenceId: m.anchor.sentenceId,
              anchorText: part.anchorText,
              occurrence: part.occurrence,
            },
          }
          flatParts.push({ mark: partMark, start: pos, end: pos + part.anchorText.length, text: part.anchorText, role: part.role })
        }
      })
    }
  })

  flatParts.sort((a, b) => {
    if (a.start !== b.start) return a.start - b.start
    if (a.end !== b.end) return b.end - a.end
    return TONE_PRIORITY[a.mark.visualTone] - TONE_PRIORITY[b.mark.visualTone]
  })

  const dedupedParts: typeof flatParts = []
  for (const part of flatParts) {
    const prev = dedupedParts[dedupedParts.length - 1]
    if (prev && part.start < prev.end) {
      if (part.end <= prev.end) continue
      dedupedParts.push({ ...part, start: prev.end, text: text.slice(prev.end, part.end) })
    } else {
      dedupedParts.push(part)
    }
  }

  type Segment =
    | { type: 'plain'; start: number; end: number }
    | { type: 'mark'; start: number; end: number; item: typeof dedupedParts[0] }

  const segments: Segment[] = []
  let aiIdx = 0

  for (const ai of dedupedParts) {
    const segStart = segments.length ? segments[segments.length - 1].end : 0
    if (ai.start > segStart) {
      segments.push({ type: 'plain', start: segStart, end: ai.start })
    }
    segments.push({ type: 'mark', start: ai.start, end: ai.end, item: ai })
  }

  const tailStart = segments.length ? segments[segments.length - 1].end : 0
  if (tailStart < text.length) {
    segments.push({ type: 'plain', start: tailStart, end: text.length })
  }

  const cleaned: Segment[] = []
  for (const seg of segments) {
    if (seg.end <= seg.start) continue
    const last = cleaned[cleaned.length - 1]
    if (last && last.type === 'plain' && seg.type === 'plain') {
      last.end = seg.end
    } else {
      cleaned.push({ ...seg })
    }
  }

  const resultElements: Array<React.ReactNode | string> = []

  for (const seg of cleaned) {
    if (seg.type === 'plain') {
      const plainSegment = text.slice(seg.start, seg.end)
      resultElements.push(
        handleDropCap(plainSegment, (restText, offsetAdjust) => (
          <Text key={`plain-${seg.start}`}>
            {renderPlainSegmentAsClickableWords(restText, selectedWord, vocabSet, (p) => {
              const occ = getNextOccurrence(p.word)
              onWordClick?.({ ...p, contextSentence: text, occurrence: occ })
            }, vocabSavedMap, onTokenLongPress, selectionRange, seg.start + offsetAdjust, userRanges, routeFocusRanges)}
          </Text>
        ))
      )
      continue
    }

    const item = seg.item
    const userRange = userRanges.find(r => item.start < r.end && item.end > r.start)
    const userHighlightClass = userRange ? `user-highlight-overlay user-highlight-overlay--${userRange.color}` : ''
    const routeFocusClass = rangeMatchesAnyFocus(item.start, item.end, routeFocusRanges)
      ? 'route-focus-overlay'
      : ''

    if (!item.mark.clickable) {
      const isActive = !!(
        normalizeId(activeMarkId) === normalizeId(item.mark.id)
        || (item.mark.parentId && normalizeId(activeMarkId) === normalizeId(item.mark.parentId))
        || (groupActiveIds && (groupActiveIds.has(item.mark.id) || (item.mark.parentId && groupActiveIds.has(item.mark.parentId))))
      )
      const role = item.role
      const markInSelection = selectionRange
        ? item.start < selectionRange.end && item.end > selectionRange.start
        : false

      resultElements.push(
        handleDropCap(item.text, (restText, offsetAdjust) => (
          <GrammarInlineSpan
            key={item.mark.id}
            mark={item.mark}
            text={restText}
            selectedWord={selectedWord}
            vocabSet={vocabSet}
            vocabSavedMap={vocabSavedMap}
            isActive={isActive}
            role={role}
            contextSentence={text}
            isInSelection={markInSelection}
            userHighlightClass={`${userHighlightClass} ${routeFocusClass}`.trim()}
            onWordClick={onWordClick}
            onTokenLongPress={onTokenLongPress ? (w, e) => onTokenLongPress(w, item.start + offsetAdjust, item.end + offsetAdjust, e) : undefined}
            getNextOccurrence={getNextOccurrence}
          />
        ))
      )
      continue
    }

    const isSurfaceMark = ['vocab', 'phrase'].includes(item.mark.visualTone)
    const isLineMark = ['context', 'grammar'].includes(item.mark.visualTone)
    const isAcademicMark = isAcademicMode && ['term', 'logic'].includes(item.mark.visualTone)
    const isActive = !!(
      normalizeId(activeMarkId) === normalizeId(item.mark.id)
      || (item.mark.parentId && normalizeId(activeMarkId) === normalizeId(item.mark.parentId))
      || (groupActiveIds && (groupActiveIds.has(item.mark.id) || (item.mark.parentId && groupActiveIds.has(item.mark.parentId))))
    )
    const isSaved = vocabSet?.has(item.text.toLowerCase())
    const savedStatus = vocabSavedMap?.[item.text.toLowerCase()]
    const markOcc = getNextOccurrence(item.text)
    const markInSelection = selectionRange
      ? item.start < selectionRange.end && item.end > selectionRange.start
      : false

    const effectiveMark = isAcademicMark
      ? { ...item.mark, renderType: 'underline' as const }
      : isSurfaceMark
        ? { ...item.mark, renderType: 'background' as const }
        : isLineMark
          ? { ...item.mark, renderType: 'underline' as const }
          : item.mark

    resultElements.push(
      handleDropCap(item.text, (restText, offsetAdjust) => (
        <InlineMark
          key={item.mark.id}
          mark={effectiveMark}
          text={restText}
          isActive={isActive}
          isSaved={isSaved}
          savedStatus={savedStatus}
          isAcademicMode={isAcademicMode}
          isInSelection={markInSelection}
          userHighlightClass={`${userHighlightClass} ${routeFocusClass}`.trim()}
          onWordClick={(p) => onWordClick?.({ ...p, contextSentence: text, occurrence: markOcc })}
          onLongPress={onTokenLongPress ? (t, e) => onTokenLongPress(t, item.start + offsetAdjust, item.end + offsetAdjust, e) : undefined}
        />
      ))
    )
  }

  return <Text className={`sentence-text ${isHighlighted ? 'is-highlighted' : ''}`}>{resultElements}</Text>
}

const ParagraphBlock = memo(function ParagraphBlock({
  order,
  paragraphId,
  sentences,
  translations,
  inlineMarks,
  activeMarkId,
  selectedWord,
  tailEntries,
  pageMode,
  isAcademicMode = false,
  vocabList,
  vocabSavedMap,
  userAnnotations,
  favoritedSentenceIds,
  recordId,
  cloudId,
  activeSentenceId,
  selectionSentenceId,
  selectionRange,
  routeFocusSentenceIds,
  routeFocusRangesBySentence,
  onWordClick,
  onSentenceClick,
  onSelectionContext,
  onMarkActiveChange,
}: ParagraphBlockProps) {
  const vocabSet = useMemo(() => new Set(vocabList ?? []), [vocabList])
  const [activeAnalysisId, setActiveAnalysisId] = useState<string | null>(null)
  const [groupActiveMarkIds, setGroupActiveMarkIds] = useState<Set<string>>(new Set())
  const isSentenceFavorited = useCallback((sentenceId: string) => {
    return favoritedSentenceIds?.has(sentenceId) ?? false
  }, [favoritedSentenceIds])
  const [feedbackTarget, setFeedbackTarget] = useState<{
    targetId: string
    annotationType: string
    prefillSentiment?: 'positive' | 'negative' | 'neutral'
    contextJson: Record<string, unknown>
  } | null>(null)

  const handleSentenceLongPress = useCallback((sentence: SentenceModel, event?: CommonEvent) => {
    event?.stopPropagation()
    const translation = translations.find(t => t.sentenceId === sentence.sentenceId)?.translationZh

    onSelectionContext?.({
      recordId: cloudId || recordId || undefined,
      paragraphId: paragraphId || sentence.paragraphId,
      sentenceId: sentence.sentenceId,
      selectedText: sentence.text,
      startOffset: 0,
      endOffset: sentence.text.length,
      translation,
      anchorType: 'sentence',
    })
  }, [translations, cloudId, recordId, paragraphId, onSelectionContext])

  const containerClass = `paragraph-block ${pageMode} ${isAcademicMode ? 'academic-mode' : ''} ${activeAnalysisId ? 'has-active-analysis' : ''}`

  // 监听分析卡片激活状态，自动定位锚点
  useEffect(() => {
    if (activeAnalysisId) {
      const scrollTimer = setTimeout(() => {
        const query = Taro.createSelectorQuery()
        query.select(`.sentence-text.is-analyzing`).boundingClientRect()
        query.selectViewport().scrollOffset()
        query.exec((res) => {
          if (res[0] && res[1]) {
            const top = res[0].top + res[1].scrollTop - 200
            Taro.pageScrollTo({
              scrollTop: top,
              duration: 300
            })
          }
        })
      }, 100)
      return () => clearTimeout(scrollTimer)
    }
  }, [activeAnalysisId])

  const handleAnalysisToggle = (entryId: string, expanded: boolean) => {
    setActiveAnalysisId(expanded ? entryId : null)
  }

  const handleCardFeedback = useCallback((entryId: string, entryType: string, title: string, content: string, prefillSentiment?: 'positive' | 'negative' | 'neutral') => {
    setFeedbackTarget({
      targetId: entryId,
      annotationType: entryType,
      prefillSentiment,
      contextJson: { title, content_preview: content.slice(0, 200) },
    })
  }, [])

  const marksBySentenceId = useMemo(() => {
    const map = new Map<string, AnyInlineMarkModel[]>()
    inlineMarks.forEach((m) => {
      const sid = m.anchor.sentenceId
      if (!map.has(sid)) map.set(sid, [])
      map.get(sid)!.push(m)
    })
    return map
  }, [inlineMarks])

  const annotationBySentenceId = useMemo(() => {
    const map = new Map<string, UserAnnotationDto>()
    userAnnotations?.forEach(a => {
      if (a.anchor_type !== 'sentence') return
      if (!map.has(a.sentence_id)) map.set(a.sentence_id, a)
    })
    return map
  }, [userAnnotations])

  const entriesBySentenceId = useMemo(() => {
    const map = new Map<string, AnySentenceEntryModel[]>()
    tailEntries.forEach((e) => {
      if (!map.has(e.sentenceId)) map.set(e.sentenceId, [])
      map.get(e.sentenceId)!.push(e)
    })
    return map
  }, [tailEntries])

  if (pageMode === 'immersive') {
    const shouldRenderDropCap = order === 1 && sentences.length > 0
    const firstDropCap = shouldRenderDropCap ? splitDropCapText(sentences[0].text) : null

    return (
      <View className={containerClass}>
        <View className='english-paragraph'>
          <View className={`english-flow ${firstDropCap ? 'drop-cap-flow' : ''}`}>
            {firstDropCap && (
              <Text className='drop-cap-initial'>{firstDropCap.letter}</Text>
            )}
            {sentences.map((sentence, idx) => {
              const rawSentenceMarks: AnyInlineMarkModel[] = marksBySentenceId.get(sentence.sentenceId) || []
              const isFirstDropCapSentence = Boolean(firstDropCap && idx === 0)
              const sentenceText = isFirstDropCapSentence ? firstDropCap!.bodyText : sentence.text
              const sentenceMarks = isFirstDropCapSentence
                ? adjustMarksForDropCap(rawSentenceMarks, sentence.text, firstDropCap!.letterIndex)
                : rawSentenceMarks
              const userAnno = annotationBySentenceId.get(sentence.sentenceId)
              const isWholeSentenceHighlight = userAnno
                ? userAnno.anchor_type !== 'text_range' || typeof userAnno.start_offset !== 'number' || typeof userAnno.end_offset !== 'number'
                : false
              const isFavorited = isSentenceFavorited(sentence.sentenceId)

              const isSentenceSelected = selectionSentenceId === sentence.sentenceId
              const currentSelectionRange = isSentenceSelected ? selectionRange : null
              const currentRouteFocusRanges = routeFocusRangesBySentence?.[sentence.sentenceId] || []
              const isSentenceRouteFocused = routeFocusSentenceIds?.has(sentence.sentenceId) ?? false

              return (
                <Text
                  id={getSentenceAnchorId(sentence.sentenceId)}
                  key={sentence.sentenceId}
                  className={`sentence-span sentence-${sentence.sentenceId} ${activeSentenceId === sentence.sentenceId ? 'is-highlighted-source' : ''} ${isWholeSentenceHighlight && userAnno ? `user-highlighted user-highlighted--${normalizeUserHighlightColor(userAnno.color)}` : ''} ${isFavorited ? 'is-favorited' : ''} ${isSentenceSelected ? 'user-selection-active' : ''} ${isSentenceRouteFocused ? 'route-focus-sentence' : ''}`}
                  onClick={() => {
                    if (selectionSentenceId) {
                      onSelectionContext?.(null)
                      return
                    }
                    onSentenceClick?.(sentence.sentenceId)
                  }}
                  onLongPress={(e: CommonEvent) => handleSentenceLongPress(sentence, e)}
                >
                  {renderTextWithMarks(sentenceText, sentenceMarks, activeMarkId, selectedWord, vocabSet, onWordClick, true, activeSentenceId === sentence.sentenceId, vocabSavedMap, false, isAcademicMode, groupActiveMarkIds, (_t, _s, _e, ev) => handleSentenceLongPress(sentence, ev), currentSelectionRange, userAnnotations, sentence.sentenceId, currentRouteFocusRanges)}
                  {isFavorited && <Text className='sentence-bookmark-mark'>⌑</Text>}
                  {idx < sentences.length - 1 ? <Text className='space-char'> </Text> : ''}
                </Text>
              )
            })}
          </View>
        </View>
      </View>
    )
  }

  // --- Intensive Mode Chunking Logic ---
  const sentenceDataList = sentences.map(sentence => {
    const sentenceMarks: AnyInlineMarkModel[] = marksBySentenceId.get(sentence.sentenceId) || []
    const sentenceEntries: AnySentenceEntryModel[] = entriesBySentenceId.get(sentence.sentenceId) || []
    const sentenceTranslation = translations.find(t => t.sentenceId === sentence.sentenceId)?.translationZh

    const analysisCards: (AnalysisCardProps & { id: string; markId?: string | null })[] = [
      ...sentenceEntries
        .filter(e => e.entryType === 'grammar_note')
        .map(e => {
          const mark = sentenceMarks.find(m => m.id === e.id)
          let snippet = ''
          if (mark) {
            if (mark.anchor.kind === 'text') snippet = mark.anchor.anchorText
            else if (mark.anchor.kind === 'multi_text') snippet = mark.anchor.parts.map(p => p.anchorText).join(' ... ')
          }
          return {
            id: e.id,
            markId: mark ? mark.id : null,
            type: 'grammar' as const,
            title: getMappedEntryTitle('grammar_note', e.title, e.label) || '语法要点',
            label: '语法要点',
            content: e.content,
            snippet: snippet,
            recordId: recordId || undefined,
            cloudId: cloudId || undefined,
            entryId: e.id,
            annotationType: 'grammar_note',
            onFeedback: recordId ? (sentiment?: 'positive' | 'negative' | 'neutral') => handleCardFeedback(e.id, 'grammar_note', e.title || e.label, e.content, sentiment) : undefined,
          }
        }),
        ...sentenceEntries
          .filter(e => e.entryType === 'sentence_analysis')
          .map(e => {
            const parsed = parseSentenceAnalysis(e.content)
            return {
              id: e.id,
              type: 'sentence' as const,
              title: e.label,
              label: '句式解析',
              content: e.content,
              structuredData: parsed,
              isExpanded: activeAnalysisId === e.id,
              onToggle: (expanded: boolean) => handleAnalysisToggle(e.id, expanded),
              recordId: recordId || undefined,
              cloudId: cloudId || undefined,
              entryId: e.id,
              annotationType: 'sentence_analysis',
              onFeedback: recordId ? () => handleCardFeedback(e.id, 'sentence_analysis', e.label, e.content) : undefined,
            }
          }),
        ...sentenceEntries
          .filter(e => e.entryType === 'term_note')
          .map(e => ({
            id: e.id,
            markId: findMarkIdForEntry(e, sentenceMarks),
            type: 'term' as const,
            title: getMappedEntryTitle('term_note', e.title, e.label) || '术语标注',
            label: '术语标注',
            content: e.content,
            onFeedback: recordId ? () => handleCardFeedback(e.id, 'term_note', e.title || e.label, e.content) : undefined,
          })),
        ...sentenceEntries
          .filter(e => e.entryType === 'logic_note')
          .map(e => ({
            id: e.id,
            markId: findMarkIdForEntry(e, sentenceMarks),
            type: 'logic' as const,
            title: getMappedEntryTitle('logic_note', e.title, e.label) || '逻辑关系',
            label: '逻辑关系',
            content: e.content,
            onFeedback: recordId ? () => handleCardFeedback(e.id, 'logic_note', e.title || e.label, e.content) : undefined,
          })),
        ...sentenceEntries
          .filter(e => e.entryType === 'interpretation_note')
          .map(e => ({
            id: e.id,
            markId: findMarkIdForEntry(e, sentenceMarks),
            type: 'interpretation' as const,
            title: getMappedEntryTitle('interpretation_note', e.title, e.label) || '解释说明',
            label: '解释说明',
            content: e.content,
            onFeedback: recordId ? () => handleCardFeedback(e.id, 'interpretation_note', e.title || e.label, e.content) : undefined,
          })),
        ...sentenceEntries
          .filter(e => e.entryType === 'content_summary')
          .map(e => ({
            id: e.id,
            type: 'summary' as const,
            title: getMappedEntryTitle('content_summary', e.title, e.label) || '内容概要',
            label: '内容概要',
            content: e.content,
          })),
    ]

    return { sentence, sentenceMarks, sentenceTranslation, analysisCards }
  })

  // 将没有解析卡片的连续句子合并为一个 Chunk
  const chunks: { id: string; hasCards: boolean; items: typeof sentenceDataList }[] = []
  let currentChunk: typeof chunks[0] | null = null

  sentenceDataList.forEach(item => {
    if (item.analysisCards.length > 0) {
      // 有卡片的句子必须独立成块
      chunks.push({ id: item.sentence.sentenceId, hasCards: true, items: [item] })
      currentChunk = null
    } else {
      // 没有卡片的句子合并到当前的无卡片块中
      if (!currentChunk || currentChunk.hasCards) {
        currentChunk = { id: `chunk-${item.sentence.sentenceId}`, hasCards: false, items: [] }
        chunks.push(currentChunk)
      }
      currentChunk.items.push(item)
    }
  })

  return (
    <View className={containerClass}>
      <View className='paragraph-header'>
        <Text className='paragraph-anchor'>{order < 10 ? `0${order}` : order} /</Text>
        <View className='paragraph-divider' />
      </View>
      {chunks.map((chunk, cIdx) => {
        if (chunk.hasCards) {
          const item = chunk.items[0]
          const isSentenceSelected = selectionSentenceId === item.sentence.sentenceId
          const currentSelectionRange = isSentenceSelected ? selectionRange : null
          const currentRouteFocusRanges = routeFocusRangesBySentence?.[item.sentence.sentenceId] || []
          const isSentenceRouteFocused = routeFocusSentenceIds?.has(item.sentence.sentenceId) ?? false
          const sentenceAnno = annotationBySentenceId.get(item.sentence.sentenceId)
          // whole-sentence fallback: no valid offset
          const isWholeSentenceHighlight = sentenceAnno
            ? sentenceAnno.anchor_type !== 'text_range' || typeof sentenceAnno.start_offset !== 'number' || typeof sentenceAnno.end_offset !== 'number'
            : false
          const hasNote = userAnnotations?.some(a => a.sentence_id === item.sentence.sentenceId && !!a.note) ?? false
          const isFavorited = isSentenceFavorited(item.sentence.sentenceId)
          return (
            <View key={`chunk-${chunk.id}-${cIdx}`} id={getSentenceAnchorId(item.sentence.sentenceId)} className='sentence-block'>
              <View
                className={`sentence-main sentence-${item.sentence.sentenceId} ${isWholeSentenceHighlight && sentenceAnno ? `user-highlighted user-highlighted--${normalizeUserHighlightColor(sentenceAnno.color)}` : ''} ${hasNote ? 'has-user-note' : ''} ${isFavorited ? 'is-favorited' : ''} ${isSentenceSelected ? 'user-selection-active' : ''} ${isSentenceRouteFocused ? 'route-focus-sentence' : ''}`}
                onClick={() => {
                  if (selectionSentenceId) {
                    onSelectionContext?.(null)
                  }
                }}
                onLongPress={(e: CommonEvent) => handleSentenceLongPress(item.sentence, e)}
              >
                {activeAnalysisId && item.analysisCards.some(c => c.id === activeAnalysisId && c.type === 'sentence') ? (
                  renderTextWithAnalysis(
                    item.sentence.text,
                    item.analysisCards.find(c => c.id === activeAnalysisId)?.structuredData?.chunks || []
                  )
                ) : (
                  <Text className='english-flow'>
                    {renderTextWithMarks(item.sentence.text, item.sentenceMarks, activeMarkId, selectedWord, vocabSet, onWordClick, false, activeSentenceId === item.sentence.sentenceId, vocabSavedMap, false, isAcademicMode, groupActiveMarkIds, (_t, _s, _e, ev) => handleSentenceLongPress(item.sentence, ev), currentSelectionRange, userAnnotations, item.sentence.sentenceId, currentRouteFocusRanges)}
                  </Text>
                )}
                {isFavorited && (
                  <View className='sentence-bookmark-corner'>
                    <LucideIcon name='bookmark' size={22} color='#47745f' strokeWidth={2} />
                  </View>
                )}

                {/* 渲染用户批注 */}
                {userAnnotations?.filter(a => a.sentence_id === item.sentence.sentenceId && !!a.note).map(anno => (
                  <View key={anno.id} className={`user-annotation-wrapper theme-${normalizeUserHighlightColor(anno.color)}`}>
                    <View className='user-annotation-note'>
                      <LucideIcon name='pen-line' size={14} color='var(--reader-muted)' />
                      <Text className='note-text'>{anno.note}</Text>
                    </View>
                  </View>
                ))}
              </View>

              {item.sentenceTranslation && (
                <View
                  className='sentence-translation'
                  onClick={() => onSentenceClick?.(item.sentence.sentenceId)}
                  onLongPress={(e: CommonEvent) => handleSentenceLongPress(item.sentence, e)}
                >                  <Text className={`translation-text segment ${activeSentenceId === item.sentence.sentenceId ? 'is-highlighted' : ''}`}>
                    {item.sentenceTranslation}
                  </Text>
                </View>
              )}

              {item.analysisCards.length > 0 && (
                <View className={`analysis-cards-list ${isAcademicMode ? 'academic-mode' : ''}`}>
                  {isAcademicMode ? (
                    /* Academic Mode: 聚合为单一注释组，不破坏阅读流 */
                    <AcademicNoteGroup
                      key={`group-${item.sentence.sentenceId}`}
                      items={item.analysisCards
                        .filter(card => ['term', 'logic', 'interpretation'].includes(card.type))
                        .map(card => ({
                        id: card.id,
                        markId: card.markId,
                        variant: (card.type === 'term' ? 'term' : card.type === 'logic' ? 'logic' : 'interpretation') as 'term' | 'logic' | 'interpretation',
                        title: card.title,
                        content: card.content,
                        sourceText: item.sentence.text,
                      }))}
                      initiallyExpanded={false}
                      onToggle={(isExpanded, activeIds) => {
                        onMarkActiveChange?.(isExpanded && activeIds.length > 0 ? activeIds[0] : null)
                        setGroupActiveMarkIds(new Set(isExpanded ? activeIds : []))
                      }}
                      onActiveChange={(activeIds) => {
                        setGroupActiveMarkIds(new Set(activeIds))
                        onMarkActiveChange?.(activeIds[0] || null)
                      }}
                    />
                  ) : (
                    /* Non-Academic Mode: 使用标准卡片组件 */
                    item.analysisCards.map((card, cardIdx) => (
                      <AnalysisCard
                        key={`${card.id}-${cardIdx}`}
                        type={card.type}
                        title={card.title}
                        label={card.label}
                        content={card.content}
                        snippet={card.snippet}
                        phonetic={card.phonetic}
                        tags={card.tags}
                        badgeIndex={card.badgeIndex}
                        structuredData={card.structuredData}
                        isExpanded={card.isExpanded}
                        onToggle={(isExpanded) => {
                          onMarkActiveChange?.(isExpanded ? card.id : null)
                          card.onToggle?.(isExpanded)
                        }}
                        onFeedback={card.onFeedback}
                        recordId={card.recordId}
                        cloudId={card.cloudId}
                        entryId={card.entryId}
                        annotationType={card.annotationType}
                      />
                    ))
                  )}
                </View>
              )}
            </View>
          )
        } else {
          return (
            <View key={`m-chunk-${chunk.id}-${cIdx}`} className='sentence-run'>
              {chunk.items.map(item => {
                const isSentenceSelected = selectionSentenceId === item.sentence.sentenceId
                const currentSelectionRange = isSentenceSelected ? selectionRange : null
                const currentRouteFocusRanges = routeFocusRangesBySentence?.[item.sentence.sentenceId] || []
                const isSentenceRouteFocused = routeFocusSentenceIds?.has(item.sentence.sentenceId) ?? false
                const sentenceAnno = annotationBySentenceId.get(item.sentence.sentenceId)
                const isWholeSentenceHighlight = sentenceAnno
                  ? sentenceAnno.anchor_type !== 'text_range' || typeof sentenceAnno.start_offset !== 'number' || typeof sentenceAnno.end_offset !== 'number'
                  : false
                const hasNote = userAnnotations?.some(a => a.sentence_id === item.sentence.sentenceId && !!a.note) ?? false
                const isFavorited = isSentenceFavorited(item.sentence.sentenceId)

                return (
                  <View key={`plain-${item.sentence.sentenceId}`} id={getSentenceAnchorId(item.sentence.sentenceId)} className='sentence-block sentence-block--plain'>
                    <View
                      className={`sentence-main sentence-${item.sentence.sentenceId} ${isWholeSentenceHighlight && sentenceAnno ? `user-highlighted user-highlighted--${normalizeUserHighlightColor(sentenceAnno.color)}` : ''} ${hasNote ? 'has-user-note' : ''} ${isFavorited ? 'is-favorited' : ''} ${isSentenceSelected ? 'user-selection-active' : ''} ${isSentenceRouteFocused ? 'route-focus-sentence' : ''}`}
                      onClick={() => {
                        if (selectionSentenceId) {
                          onSelectionContext?.(null)
                          return
                        }
                        onSentenceClick?.(item.sentence.sentenceId)
                      }}
                      onLongPress={(e: CommonEvent) => handleSentenceLongPress(item.sentence, e)}
                    >
                      <Text className='english-flow'>
                        {renderTextWithMarks(item.sentence.text, item.sentenceMarks, activeMarkId, selectedWord, vocabSet, onWordClick, false, activeSentenceId === item.sentence.sentenceId, vocabSavedMap, false, isAcademicMode, groupActiveMarkIds, (_t, _s, _e, ev) => handleSentenceLongPress(item.sentence, ev), currentSelectionRange, userAnnotations, item.sentence.sentenceId, currentRouteFocusRanges)}
                      </Text>
                      {isFavorited && (
                        <View className='sentence-bookmark-corner'>
                          <LucideIcon name='bookmark' size={22} color='#47745f' strokeWidth={2} />
                        </View>
                      )}
                      {userAnnotations?.filter(a => a.sentence_id === item.sentence.sentenceId && !!a.note).map(anno => (
                        <View key={anno.id} className={`user-annotation-wrapper theme-${normalizeUserHighlightColor(anno.color)}`}>
                          <View className='user-annotation-note'>
                            <LucideIcon name='pen-line' size={14} color='var(--reader-muted)' />
                            <Text className='note-text'>{anno.note}</Text>
                          </View>
                        </View>
                      ))}
                    </View>

                    {item.sentenceTranslation && (
                      <View
                        className='sentence-translation'
                        onClick={() => onSentenceClick?.(item.sentence.sentenceId)}
                        onLongPress={(e: CommonEvent) => handleSentenceLongPress(item.sentence, e)}
                      >
                        <Text className={`translation-text segment ${activeSentenceId === item.sentence.sentenceId ? 'is-highlighted' : ''}`}>
                          {item.sentenceTranslation}
                        </Text>
                      </View>
                    )}
                  </View>
                )
              })}
            </View>
          )
        }
      })}
      {feedbackTarget && recordId && (
        <View className='annotation-feedback-overlay' onClick={() => setFeedbackTarget(null)}>
          <FeedbackSheet
            scope="annotation"
            prefillSentiment={feedbackTarget.prefillSentiment}
            payload={{
              targetId: feedbackTarget.targetId,
              analysisRecordId: cloudId || recordId,
              annotationType: feedbackTarget.annotationType,
              contextJson: feedbackTarget.contextJson,
            }}
            contextSummary={(feedbackTarget.contextJson.title as string) || (feedbackTarget.contextJson.content_preview as string)}
            onClose={() => setFeedbackTarget(null)}
          />
        </View>
      )}
    </View>
  )
})

export default ParagraphBlock
