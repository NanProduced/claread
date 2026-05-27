import { useMemo, useState, useCallback, useEffect, useRef } from 'react'
import { View, Text, ScrollView } from '@tarojs/components'
import Taro, { useShareAppMessage } from '@tarojs/taro'
import { buildMultiTextTargetKey, buildSentenceTargetKey, buildTextRangeTargetKey } from '@claread/contracts'
import { ROUTES } from '../../config/routes'
import { AnyRenderSceneVm, AcademicRenderSceneVm } from '../../types/view/render-scene.vm'
import { useAuthStore } from '../../stores/auth'
import NavBar from '../../components/NavBar'
import ParagraphBlock, { getSentenceAnchorId } from '../../components/ParagraphBlock'
import WordPopup from '../../components/WordPopup'
import ContentSummaryCard from '../../components/ContentSummaryCard'
import LucideIcon from '../../components/LucideIcon'
import AnnotationGlyph from '../../components/AnnotationGlyph'
import BottomSheetSelect from '../../components/BottomSheetSelect'
import FeedbackWidget from '../../components/FeedbackWidget'
import ReaderNoteSheet from '../../components/ReaderNoteSheet'
import ReaderContextBar from '../../components/ReaderContextBar'
import ReadingSettingsSheet from '../../components/ReadingSettingsSheet'
import ReadingSelectionToolbar, { SelectionContext } from '../../components/ReadingSelectionToolbar'
import { useReadingPreferencesStore } from '../../stores/reading-preferences'
import { UserAnnotationDto, listUserAnnotations } from '../../services/api/user-annotations.client'
import { ReaderNoteDto, listReaderNotes } from '../../services/api/reader-notes.client'
import { CloudSyncService } from '../../services/cloudSync.service'
import FeedbackSheet from '../../components/FeedbackSystem/FeedbackSheet'
import { useResultState } from './hooks/useResultState'
import { useResultEffects } from './hooks/useResultEffects'
import { useResultActions } from './hooks/useResultActions'
import { PAGE_MODE_OPTIONS, hasRenderableScene } from './utils'
import { getVocabEntryByLookupForm } from '../../services/storage'
import { getLocalReaderNotes, getLocalUserAnnotations, getSyncQueue } from '../../services/storage'
import DegradedBanner from './components/DegradedBanner'
import SourceFallback from './components/SourceFallback'
import StateViews from './components/StateViews'
import appShare from '../../assets/images/share/app-share.jpg'
import './index.scss'

function buildPendingEntityIdSet(action: 'UPSERT_NOTE' | 'UPSERT_ANNOTATION'): Set<string> {
  return new Set(
    getSyncQueue()
      .filter(item => (item.status === 'pending' || item.status === 'running') && item.action === action)
      .map(item => item.entityId)
      .filter(Boolean)
  )
}

function mergeReaderNotesCloudWithLocal(
  cloudNotes: ReaderNoteDto[],
  localNotes: ReaderNoteDto[],
): ReaderNoteDto[] {
  const pendingNoteIds = buildPendingEntityIdSet('UPSERT_NOTE')
  const result: ReaderNoteDto[] = []
  const cloudTargetKeys = new Set(cloudNotes.map(n => n.target_key))
  const cloudIds = new Set(cloudNotes.map(n => n.id))
  const localById = new Map(localNotes.map(note => [note.id, note]))

  for (const cloud of cloudNotes) {
    const local = localById.get(cloud.id)
    if (
      local
      && pendingNoteIds.has(local.id)
      && typeof local.updated_at === 'string'
      && typeof cloud.updated_at === 'string'
      && new Date(local.updated_at).getTime() > new Date(cloud.updated_at).getTime()
    ) {
      result.push(local)
      continue
    }
    result.push(cloud)
  }

  for (const local of localNotes) {
    if (cloudIds.has(local.id)) continue
    if (pendingNoteIds.has(local.id) && !cloudTargetKeys.has(local.target_key)) {
      result.push(local)
    }
  }
  return result
}

function mergeAnnotationsCloudWithLocal(
  cloudAnnotations: UserAnnotationDto[],
  localAnnotations: UserAnnotationDto[],
): UserAnnotationDto[] {
  const pendingAnnotationIds = buildPendingEntityIdSet('UPSERT_ANNOTATION')
  const result: UserAnnotationDto[] = []
  const cloudTargetKeys = new Set(cloudAnnotations.map(a => a.target_key))
  const cloudIds = new Set(cloudAnnotations.map(a => a.id))
  const localById = new Map(localAnnotations.map(annotation => [annotation.id, annotation]))

  for (const cloud of cloudAnnotations) {
    const local = localById.get(cloud.id)
    if (
      local
      && pendingAnnotationIds.has(local.id)
      && typeof local.updated_at === 'string'
      && typeof cloud.updated_at === 'string'
      && new Date(local.updated_at).getTime() > new Date(cloud.updated_at).getTime()
    ) {
      result.push(local)
      continue
    }
    result.push(cloud)
  }

  for (const local of localAnnotations) {
    if (cloudIds.has(local.id)) continue
    if (pendingAnnotationIds.has(local.id) && !cloudTargetKeys.has(local.target_key)) {
      result.push(local)
    }
  }
  return result
}

function buildTargetKey(
  recordId: string,
  anchorType: 'sentence' | 'text_range' | 'multi_text',
  opts: { sentenceId?: string; startOffset?: number; endOffset?: number; textHash?: string; segments?: Array<{ paragraphId?: string; sentenceId: string; startOffset: number; endOffset: number; textHash: string }> }
): string {
  if (anchorType === 'sentence') return buildSentenceTargetKey(recordId, opts.sentenceId || '')
  if (anchorType === 'multi_text') return buildMultiTextTargetKey(recordId, opts.segments || [])
  return buildTextRangeTargetKey(recordId, opts.sentenceId || '', opts.startOffset || 0, opts.endOffset || 0, opts.textHash || '')
}

interface RouteFocusRange {
  start: number
  end: number
}

interface RouteFocusState {
  targetKey: string
  anchorType: 'sentence' | 'text_range' | 'multi_text'
  sentenceIds: string[]
  rangesBySentence: Record<string, RouteFocusRange[]>
}

function buildRouteFocusState(
  targetKey: string,
  anchorType: RouteFocusState['anchorType'],
  sentenceIds: string[],
  rangesBySentence: Record<string, RouteFocusRange[]>
): RouteFocusState | null {
  const normalizedSentenceIds = Array.from(new Set(sentenceIds.filter(Boolean)))
  const normalizedRanges = Object.fromEntries(
    Object.entries(rangesBySentence)
      .map(([sentenceId, ranges]) => {
        const accepted = ranges
          .filter(range => Number.isFinite(range.start) && Number.isFinite(range.end) && range.end > range.start)
          .sort((a, b) => a.start - b.start || b.end - a.end)
          .reduce<RouteFocusRange[]>((list, current) => {
            const previous = list[list.length - 1]
            if (previous && current.start < previous.end) return list
            list.push(current)
            return list
          }, [])
        return accepted.length > 0 ? [sentenceId, accepted] : null
      })
      .filter((item): item is [string, RouteFocusRange[]] => Boolean(item))
  )

  if (normalizedSentenceIds.length === 0 && Object.keys(normalizedRanges).length === 0) {
    return null
  }

  return {
    targetKey,
    anchorType,
    sentenceIds: normalizedSentenceIds,
    rangesBySentence: normalizedRanges,
  }
}

function buildRouteFocusFromAnnotation(annotation: UserAnnotationDto): RouteFocusState | null {
  const sentenceIds: string[] = []
  const rangesBySentence: Record<string, RouteFocusRange[]> = {}

  const appendRange = (sentenceId: string, range: RouteFocusRange) => {
    sentenceIds.push(sentenceId)
    rangesBySentence[sentenceId] = [...(rangesBySentence[sentenceId] || []), range]
  }

  if (annotation.anchor_type === 'multi_text') {
    ;(annotation.segments || []).forEach(segment => {
      appendRange(segment.sentence_id, { start: segment.start_offset, end: segment.end_offset })
    })
    return buildRouteFocusState(annotation.target_key, 'multi_text', sentenceIds, rangesBySentence)
  }

  if (
    annotation.anchor_type === 'text_range'
    && annotation.sentence_id
    && typeof annotation.start_offset === 'number'
    && typeof annotation.end_offset === 'number'
    && annotation.end_offset > annotation.start_offset
  ) {
    appendRange(annotation.sentence_id, { start: annotation.start_offset, end: annotation.end_offset })
    return buildRouteFocusState(annotation.target_key, 'text_range', sentenceIds, rangesBySentence)
  }

  return annotation.sentence_id
    ? buildRouteFocusState(annotation.target_key, 'sentence', [annotation.sentence_id], {})
    : null
}

function buildRouteFocusFromReaderNote(note: ReaderNoteDto): RouteFocusState | null {
  const sentenceIds: string[] = []
  const rangesBySentence: Record<string, RouteFocusRange[]> = {}

  const appendRange = (sentenceId: string, range: RouteFocusRange) => {
    sentenceIds.push(sentenceId)
    rangesBySentence[sentenceId] = [...(rangesBySentence[sentenceId] || []), range]
  }

  if (note.quote_mode === 'multi_text') {
    note.segments.forEach(segment => appendRange(segment.sentence_id, { start: segment.start_offset, end: segment.end_offset }))
    return buildRouteFocusState(note.target_key, 'multi_text', sentenceIds, rangesBySentence)
  }

  if (
    note.quote_mode === 'text_range'
    && note.sentence_id
    && typeof note.start_offset === 'number'
    && typeof note.end_offset === 'number'
    && note.end_offset > note.start_offset
  ) {
    appendRange(note.sentence_id, { start: note.start_offset, end: note.end_offset })
    return buildRouteFocusState(note.target_key, 'text_range', sentenceIds, rangesBySentence)
  }

  return note.anchor_sentence_id
    ? buildRouteFocusState(note.target_key, 'sentence', [note.anchor_sentence_id], {})
    : null
}

export default function Result() {
  const state = useResultState()
  const {
    navBarHeight, pageMode, setPageMode,
    vocabList, vocabSavedMap, wordPopup, setWordPopup,
    activeMarkId, selectedWord, activeSentenceId, setActiveSentenceId,
    animTrigger, favorited, vocabHighlights,
    showModeSheet, setShowModeSheet, tempConfig,
    pageState, sceneData, requestParams, errorCode, errorMsg,
    recordId, cloudId, isReplayMode,
  } = state

  const [showSettingsSheet, setShowSettingsSheet] = useState(false)
  const [selectionContext, setSelectionContext] = useState<SelectionContext | null>(null)
  const [selectionSentenceId, setSelectionSentenceId] = useState<string | null>(null)
  const [selectionRange, setSelectionRange] = useState<{ start: number; end: number } | null>(null)
  const [routeFocus, setRouteFocus] = useState<RouteFocusState | null>(null)
  const [userAnnotations, setUserAnnotations] = useState<UserAnnotationDto[]>([])
  const [readerNotes, setReaderNotes] = useState<ReaderNoteDto[]>([])
  const [noteSheetSentenceId, setNoteSheetSentenceId] = useState<string | null>(null)
  const [noteSheetMode, setNoteSheetMode] = useState<'preview' | 'compose'>('preview')
  const [activeReaderNoteId, setActiveReaderNoteId] = useState<string | null>(null)
  const [editingReaderNoteId, setEditingReaderNoteId] = useState<string | null>(null)
  const [noteDraftText, setNoteDraftText] = useState('')
  const [showFeedbackSheet, setShowFeedbackSheet] = useState(false)
  const [scrollIntoViewId, setScrollIntoViewId] = useState('')
  const [articleScrollTop, setArticleScrollTop] = useState(0)
  const routeSentenceIdRef = useRef<string | null>(null)
  const routeTargetKeyRef = useRef<string | null>(null)
  const routeRangeRef = useRef<{ start: number; end: number; textHash?: string } | null>(null)
  const scrolledSentenceIdRef = useRef<string | null>(null)
  const articleScrollTopRef = useRef(0)
  const { preferences } = useReadingPreferencesStore()
  const isLoggedIn = useAuthStore(state => state.isLoggedIn)

  if (routeSentenceIdRef.current === null) {
    const params = Taro.getCurrentInstance().router?.params || {}
    routeSentenceIdRef.current = typeof params.sentenceId === 'string' ? params.sentenceId : ''
    routeTargetKeyRef.current = typeof params.targetKey === 'string' ? params.targetKey : ''
    const anchorType = typeof params.anchorType === 'string' ? params.anchorType : ''
    const startOffset = typeof params.startOffset === 'string' ? Number(params.startOffset) : NaN
    const endOffset = typeof params.endOffset === 'string' ? Number(params.endOffset) : NaN
    routeRangeRef.current = anchorType === 'text_range' && Number.isFinite(startOffset) && Number.isFinite(endOffset) && startOffset >= 0 && endOffset > startOffset
      ? {
          start: startOffset,
          end: endOffset,
          textHash: typeof params.textHash === 'string' ? params.textHash : undefined,
        }
      : null
  }

  const readerStyles = useMemo(() => {
    const fsRatios = { small: 0.85, standard: 1, large: 1.15, xlarge: 1.3 }
    const lhRatios = { compact: 1.34, standard: 1.6, loose: 1.82 }
    const sentenceSpacing = { compact: '24rpx', standard: '36rpx', loose: '48rpx' }
    const plainSentenceSpacing = { compact: '20rpx', standard: '30rpx', loose: '40rpx' }
    const translationTopSpacing = { compact: '8rpx', standard: '14rpx', loose: '18rpx' }
    const translationBottomSpacing = { compact: '10rpx', standard: '16rpx', loose: '22rpx' }
    const trOpacity = { hidden: 0, muted: 0.6, standard: 1 }

    let bg = '#F9F5EC'
    if (preferences.paper_theme === 'white') bg = '#FFFFFF'
    else if (preferences.paper_theme === 'sage') bg = '#F0F4F0'

    return {
      '--reader-font-size-ratio': fsRatios[preferences.font_size],
      '--reader-line-height': lhRatios[preferences.line_height],
      '--reader-sentence-spacing': sentenceSpacing[preferences.line_height],
      '--reader-plain-sentence-spacing': plainSentenceSpacing[preferences.line_height],
      '--reader-translation-top-spacing': translationTopSpacing[preferences.line_height],
      '--reader-translation-bottom-spacing': translationBottomSpacing[preferences.line_height],
      '--reader-bg-theme': bg,
      '--reader-translation-opacity': trOpacity[preferences.translation_display],
    } as React.CSSProperties
  }, [preferences])

  useEffect(() => {
    if (pageState !== 'normal' || !isLoggedIn || !cloudId) {
      setUserAnnotations([])
      setReaderNotes([])
      return
    }

    const localReaderNotes = getLocalReaderNotes()
      .filter(note => note.analysis_record_id === cloudId)
    if (localReaderNotes.length > 0) {
      setReaderNotes(localReaderNotes)
    }
    const localAnnotations = getLocalUserAnnotations()
      .filter(a => a.analysis_record_id === cloudId)
    if (localAnnotations.length > 0) {
      setUserAnnotations(localAnnotations)
    }

    listUserAnnotations(cloudId).then(cloud => {
      setUserAnnotations(mergeAnnotationsCloudWithLocal(cloud, localAnnotations))
    }).catch(err => {
      console.warn('Failed to load user annotations', err)
      if (localAnnotations.length > 0) {
        setUserAnnotations(localAnnotations)
      }
    })
    listReaderNotes(cloudId).then(cloud => {
      setReaderNotes(mergeReaderNotesCloudWithLocal(cloud, localReaderNotes))
    }).catch(err => {
      console.warn('Failed to load reader notes', err)
      if (localReaderNotes.length > 0) {
        setReaderNotes(localReaderNotes)
      }
    })
  }, [cloudId, pageState, isLoggedIn])

  useEffect(() => {
    const targetKey = routeTargetKeyRef.current
    if (!targetKey) return

    const matchedAnnotation = userAnnotations.find(item => item.target_key === targetKey)
    const matchedReaderNote = readerNotes.find(item => item.target_key === targetKey)
    const matchedSegments = matchedAnnotation?.segments?.map(segment => ({
      sentenceId: segment.sentence_id,
      start: segment.start_offset,
      end: segment.end_offset,
    })) || matchedReaderNote?.segments?.map(segment => ({
      sentenceId: segment.sentence_id,
      start: segment.start_offset,
      end: segment.end_offset,
    })) || []

    if (!routeSentenceIdRef.current && matchedSegments[0]?.sentenceId) {
      routeSentenceIdRef.current = matchedSegments[0].sentenceId
    }
    if (!routeRangeRef.current && matchedSegments[0]) {
      routeRangeRef.current = { start: matchedSegments[0].start, end: matchedSegments[0].end }
    }

    setRouteFocus(
      matchedAnnotation
        ? buildRouteFocusFromAnnotation(matchedAnnotation)
        : matchedReaderNote
          ? buildRouteFocusFromReaderNote(matchedReaderNote)
          : null,
    )
    if (matchedReaderNote) {
      setNoteSheetSentenceId(matchedReaderNote.anchor_sentence_id)
      setNoteSheetMode('preview')
      setActiveReaderNoteId(matchedReaderNote.id)
      setEditingReaderNoteId(null)
      setNoteDraftText('')
    }
  }, [readerNotes, userAnnotations])

  useEffect(() => {
    const targetSentenceId = routeSentenceIdRef.current
    if (!targetSentenceId || pageState !== 'normal' || !sceneData?.article?.sentences?.length) return
    if (scrolledSentenceIdRef.current === targetSentenceId) return
    const exists = sceneData.article.sentences.some(sentence => sentence.sentenceId === targetSentenceId)
    if (!exists) return

    scrolledSentenceIdRef.current = targetSentenceId
    setActiveSentenceId(targetSentenceId)
    setScrollIntoViewId('')
    const timer = setTimeout(() => {
      const anchorId = getSentenceAnchorId(targetSentenceId)
      setScrollIntoViewId(anchorId)

      const query = Taro.createSelectorQuery()
      query.select('.article-scroll').boundingClientRect()
      query.select(`#${anchorId}`).boundingClientRect()
      query.selectViewport().scrollOffset()
      query.exec((res) => {
        const scrollRect = res?.[0]
        const targetRect = res?.[1]
        const viewportOffset = res?.[2]
        if (!targetRect) return

        if (scrollRect) {
          const nextTop = Math.max(0, articleScrollTopRef.current + targetRect.top - scrollRect.top - 48)
          articleScrollTopRef.current = nextTop
          setArticleScrollTop(prev => (Math.abs(prev - nextTop) < 1 ? nextTop + 0.5 : nextTop))
        }

        if (viewportOffset) {
          const pageTop = Math.max(0, viewportOffset.scrollTop + targetRect.top - navBarHeight - 56)
          Taro.pageScrollTo({ scrollTop: pageTop, duration: 280 })
        }
      })
    }, 260)
    return () => clearTimeout(timer)
  }, [pageState, sceneData, setActiveSentenceId, navBarHeight])

  useEffect(() => {
    if (!routeFocus) return
    const targetKey = routeFocus.targetKey
    const timer = setTimeout(() => {
      setRouteFocus(current => current?.targetKey === targetKey ? null : current)
    }, 4200)
    return () => clearTimeout(timer)
  }, [routeFocus])

  const clearSelection = useCallback(() => {
    setSelectionContext(null)
    setSelectionSentenceId(null)
    setSelectionRange(null)
  }, [])

  const handleSelectionContext = useCallback((context: SelectionContext | null) => {
    if (context) {
      setSelectionSentenceId(context.sentenceId)
      setSelectionRange(null)
      setSelectionContext(context)
    } else {
      clearSelection()
    }
  }, [clearSelection])

  const activeSelectionTargetKey = useMemo(() => {
    if (!selectionContext) return null
    const activeRecordId = cloudId || recordId || ''
    return buildTargetKey(activeRecordId, selectionContext.anchorType, {
      sentenceId: selectionContext.sentenceId,
      startOffset: selectionContext.startOffset,
      endOffset: selectionContext.endOffset,
      textHash: selectionContext.textHash,
    })
  }, [selectionContext, cloudId, recordId])
  const routeFocusSentenceIds = useMemo(() => new Set(routeFocus?.sentenceIds || []), [routeFocus])
  const routeFocusRangesBySentence = useMemo(() => routeFocus?.rangesBySentence || {}, [routeFocus])
  const sentenceById = useMemo(
    () => new Map((sceneData?.article?.sentences || []).map(sentence => [sentence.sentenceId, sentence])),
    [sceneData],
  )

  const noteSheetNotes = useMemo(() => {
    if (!noteSheetSentenceId) return []
    return readerNotes
      .filter(note => note.anchor_sentence_id === noteSheetSentenceId)
      .sort((a, b) => {
        const aStart = a.quote_mode === 'sentence' ? 0 : (a.start_offset ?? a.segments[0]?.start_offset ?? 0)
        const bStart = b.quote_mode === 'sentence' ? 0 : (b.start_offset ?? b.segments[0]?.start_offset ?? 0)
        if (aStart !== bStart) return aStart - bStart
        const aLen = a.selected_text.length
        const bLen = b.selected_text.length
        if (aLen !== bLen) return aLen - bLen
        return a.created_at.localeCompare(b.created_at)
      })
  }, [noteSheetSentenceId, readerNotes])

  const noteSheetSentenceText = useMemo(() => {
    if (!noteSheetSentenceId || !sceneData?.article?.sentences) return ''
    return sceneData.article.sentences.find(sentence => sentence.sentenceId === noteSheetSentenceId)?.text || ''
  }, [noteSheetSentenceId, sceneData])

  const editingReaderNote = useMemo(() => {
    if (!editingReaderNoteId) return null
    return readerNotes.find(note => note.id === editingReaderNoteId) || null
  }, [editingReaderNoteId, readerNotes])

  const activeReaderNote = useMemo(() => {
    if (!activeReaderNoteId) return null
    return readerNotes.find(note => note.id === activeReaderNoteId) || null
  }, [activeReaderNoteId, readerNotes])

  const openNoteSheetForSentence = useCallback((sentenceId: string, preferSentenceComposer = false) => {
    const sentenceTargetKey = cloudId ? buildSentenceTargetKey(cloudId, sentenceId) : null
    const sentenceNote = sentenceTargetKey
      ? readerNotes.find(note => note.target_key === sentenceTargetKey) || null
      : null
    const fallbackNote = readerNotes.find(note => note.anchor_sentence_id === sentenceId) || null
    const nextActiveNote = sentenceNote || fallbackNote
    const nextEditingNote = preferSentenceComposer ? sentenceNote : null

    setNoteSheetSentenceId(sentenceId)
    setNoteSheetMode(preferSentenceComposer || !nextActiveNote ? 'compose' : 'preview')
    setActiveReaderNoteId(nextActiveNote?.id || null)
    setEditingReaderNoteId(nextEditingNote?.id || null)
    setNoteDraftText(nextEditingNote?.note_text || '')
    clearSelection()
  }, [clearSelection, cloudId, readerNotes])

  const closeNoteSheet = useCallback(() => {
    setNoteSheetSentenceId(null)
    setNoteSheetMode('preview')
    setActiveReaderNoteId(null)
    setEditingReaderNoteId(null)
    setNoteDraftText('')
  }, [])

  const handleOpenSentenceNotes = useCallback((sentenceId: string) => {
    openNoteSheetForSentence(sentenceId)
  }, [openNoteSheetForSentence])

  const focusReaderNote = useCallback((note: ReaderNoteDto) => {
    const focus = buildRouteFocusFromReaderNote(note)
    routeSentenceIdRef.current = note.anchor_sentence_id
    scrolledSentenceIdRef.current = null
    setNoteSheetSentenceId(note.anchor_sentence_id)
    setNoteSheetMode('preview')
    setActiveReaderNoteId(note.id)
    setEditingReaderNoteId(null)
    setNoteDraftText('')
    setActiveSentenceId(note.anchor_sentence_id)
    setScrollIntoViewId(getSentenceAnchorId(note.anchor_sentence_id))
    setRouteFocus(focus)
  }, [setActiveSentenceId])

  const handleOpenActiveNoteActions = useCallback(() => {
    if (!activeReaderNote) return

    Taro.showActionSheet({
      itemList: ['编辑笔记', '删除笔记'],
      success: (result) => {
        if (result.tapIndex === 0) {
          setNoteSheetMode('compose')
          setEditingReaderNoteId(activeReaderNote.id)
          setNoteDraftText(activeReaderNote.note_text)
          return
        }

        if (result.tapIndex === 1) {
          Taro.showModal({
            title: '删除笔记',
            content: '删除后将无法恢复，是否继续？',
            confirmColor: '#C2410C',
            success: (modal) => {
              if (!modal.confirm) return

              const nextNotes = readerNotes.filter(note => note.id !== activeReaderNote.id)
              const nextActive = nextNotes.find(note => note.anchor_sentence_id === activeReaderNote.anchor_sentence_id) || null

              setReaderNotes(nextNotes)
              setActiveReaderNoteId(nextActive?.id || null)
              setEditingReaderNoteId(null)
              setNoteDraftText('')
              setNoteSheetMode(nextActive ? 'preview' : 'compose')
              CloudSyncService.syncDeleteNote(activeReaderNote.id)
              Taro.showToast({ title: '已删除笔记', icon: 'success' })
            },
          })
        }
      },
    })
  }, [activeReaderNote, readerNotes])

  const handleCopy = (mode: 'original' | 'translation' | 'bilingual') => {
    if (!selectionContext) return
    let text = ''
    if (mode === 'original') {
      text = selectionContext.selectedText
    } else if (mode === 'translation') {
      text = selectionContext.translation || selectionContext.selectedText
    } else {
      text = selectionContext.translation
        ? `${selectionContext.selectedText}\n${selectionContext.translation}`
        : selectionContext.selectedText
    }
    Taro.setClipboardData({
      data: text,
      success: () => {
        Taro.showToast({ title: '已复制', icon: 'success' })
        clearSelection()
      }
    })
  }

  const handleHighlight = async (color: 'soft_green' | 'soft_blue' | 'soft_purple' | 'warm_yellow' | 'sage_green', selectedText: string) => {
    if (!selectionContext || !cloudId) return
    try {
      const tempId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
      const now = new Date().toISOString()
      const newAnnotation: UserAnnotationDto = {
        id: tempId,
        analysis_record_id: cloudId,
        anchor_type: 'sentence',
        target_key: buildSentenceTargetKey(cloudId, selectionContext.sentenceId),
        sentence_id: selectionContext.sentenceId,
        selected_text: selectedText,
        text_hash: selectionContext.textHash,
        color: color,
        payload_json: {},
        created_at: now,
        updated_at: now,
      }
      setUserAnnotations(prev => [...prev, newAnnotation])
      CloudSyncService.syncAnnotation(newAnnotation, true)
      Taro.showToast({ title: '已添加高亮', icon: 'success' })
      clearSelection()
    } catch (err) {
      console.warn('Failed to create highlight', err)
      Taro.showToast({ title: '添加高亮失败', icon: 'none' })
    }
  }

  const handleNote = async (selectedText: string) => {
    if (!selectionContext || !cloudId) return
    try {
      const tempId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
      const now = new Date().toISOString()
      const newNote: ReaderNoteDto = {
        id: tempId,
        analysis_record_id: cloudId,
        anchor_sentence_id: selectionContext.sentenceId,
        quote_mode: 'sentence',
        target_key: buildSentenceTargetKey(cloudId, selectionContext.sentenceId),
        sentence_id: selectionContext.sentenceId,
        selected_text: selectedText,
        start_offset: selectionContext.startOffset,
        end_offset: selectionContext.endOffset,
        text_hash: selectionContext.textHash,
        segments: [{
          sentence_id: selectionContext.sentenceId,
          selected_text: selectedText,
          start_offset: selectionContext.startOffset,
          end_offset: selectionContext.endOffset,
          text_hash: selectionContext.textHash || '',
        }],
        note_text: '',
        payload_json: {},
        created_at: now,
        updated_at: now,
      }
      setReaderNotes(prev => [...prev, newNote])
      setNoteSheetSentenceId(selectionContext.sentenceId)
      setActiveReaderNoteId(newNote.id)
      CloudSyncService.syncNote(newNote, true)
      clearSelection()
    } catch (err) {
      console.warn('Failed to create note', err)
      Taro.showToast({ title: '添加笔记失败', icon: 'none' })
    }
  }

  const handleSentenceHighlight = useCallback(async (
    color: 'soft_green' | 'soft_blue' | 'soft_purple' | 'warm_yellow' | 'sage_green',
    selectedText: string,
  ) => {
    if (!selectionContext || !cloudId) return
    try {
      const now = new Date().toISOString()
      const targetKey = buildSentenceTargetKey(cloudId, selectionContext.sentenceId)
      const existingAnnotation = userAnnotations.find(item => item.target_key === targetKey) || null

      if (existingAnnotation) {
        const updatedAnnotation: UserAnnotationDto = {
          ...existingAnnotation,
          sentence_id: selectionContext.sentenceId,
          selected_text: selectedText,
          text_hash: selectionContext.textHash,
          color,
          updated_at: now,
        }
        setUserAnnotations(prev => prev.map(item => item.id === updatedAnnotation.id ? updatedAnnotation : item))
        CloudSyncService.syncAnnotation(updatedAnnotation, false)
        Taro.showToast({ title: '已更新高亮', icon: 'success' })
        clearSelection()
        return
      }

      const tempId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
      const newAnnotation: UserAnnotationDto = {
        id: tempId,
        analysis_record_id: cloudId,
        anchor_type: 'sentence',
        target_key: targetKey,
        sentence_id: selectionContext.sentenceId,
        selected_text: selectedText,
        text_hash: selectionContext.textHash,
        color,
        payload_json: {},
        created_at: now,
        updated_at: now,
      }
      setUserAnnotations(prev => [...prev, newAnnotation])
      CloudSyncService.syncAnnotation(newAnnotation, true)
      Taro.showToast({ title: '已添加高亮', icon: 'success' })
      clearSelection()
    } catch (err) {
      console.warn('Failed to create highlight', err)
      Taro.showToast({ title: '添加高亮失败', icon: 'none' })
    }
  }, [clearSelection, cloudId, selectionContext, userAnnotations])

  const handleSentenceNoteAction = useCallback(() => {
    if (!selectionContext) return
    openNoteSheetForSentence(selectionContext.sentenceId, true)
  }, [openNoteSheetForSentence, selectionContext])

  const handleSubmitNoteDraft = useCallback(async () => {
    if (!noteSheetSentenceId || !cloudId) return
    const nextText = noteDraftText.trim()
    if (!nextText) {
      Taro.showToast({ title: '笔记内容不能为空', icon: 'none' })
      return
    }

    try {
      const now = new Date().toISOString()

      if (editingReaderNote) {
        const updatedNote: ReaderNoteDto = {
          ...editingReaderNote,
          note_text: nextText,
          updated_at: now,
        }
        setReaderNotes(prev => prev.map(note => note.id === updatedNote.id ? updatedNote : note))
        setActiveReaderNoteId(updatedNote.id)
        setEditingReaderNoteId(updatedNote.id)
        setNoteDraftText(updatedNote.note_text)
        setNoteSheetMode('preview')
        CloudSyncService.syncNote(updatedNote, false)
        Taro.showToast({ title: '已保存笔记', icon: 'success' })
        return
      }

      const sentence = sentenceById.get(noteSheetSentenceId)
      const selectedText = sentence?.text || noteSheetSentenceText
      if (!selectedText) {
        Taro.showToast({ title: '未找到句子内容', icon: 'none' })
        return
      }

      const tempId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
      const newNote: ReaderNoteDto = {
        id: tempId,
        analysis_record_id: cloudId,
        anchor_sentence_id: noteSheetSentenceId,
        quote_mode: 'sentence',
        target_key: buildSentenceTargetKey(cloudId, noteSheetSentenceId),
        paragraph_id: sentence?.paragraphId,
        sentence_id: noteSheetSentenceId,
        selected_text: selectedText,
        start_offset: 0,
        end_offset: selectedText.length,
        segments: [],
        note_text: nextText,
        payload_json: {},
        created_at: now,
        updated_at: now,
      }
      setReaderNotes(prev => [...prev, newNote])
      setActiveReaderNoteId(newNote.id)
      setEditingReaderNoteId(newNote.id)
      setNoteDraftText(newNote.note_text)
      setNoteSheetMode('preview')
      CloudSyncService.syncNote(newNote, true)
      Taro.showToast({ title: '已创建笔记', icon: 'success' })
    } catch (err) {
      console.warn('Failed to save note', err)
      Taro.showToast({ title: '保存笔记失败', icon: 'none' })
    }
  }, [cloudId, editingReaderNote, noteDraftText, noteSheetSentenceId, noteSheetSentenceText, sentenceById])

  useResultEffects({
    recordId, cloudId, sceneData, pageState,
    setFavorited: state.setFavorited,
    setVocabList: state.setVocabList,
    setVocabSavedMap: state.setVocabSavedMap,
    setVocabHighlights: state.setVocabHighlights,
    loadRecord: state.loadRecord,
    recoverActiveTask: state.recoverActiveTask,
    setWordPopup,
  })

  const actions = useResultActions({
    recordId, cloudId, requestParams, isReplayMode, pageState,
    favorited, wordPopup, activeSentenceId,
    setFavorited: state.setFavorited,
    setAnimTrigger: state.setAnimTrigger,
    setActiveMarkId: state.setActiveMarkId,
    setSelectedWord: state.setSelectedWord,
    setActiveSentenceId: state.setActiveSentenceId,
    setWordPopup,
    setVocabList: state.setVocabList,
    setShowModeSheet, setTempConfig: state.setTempConfig,
    analyze: state.analyze,
    reset: state.reset,
  })

  useShareAppMessage(() => {
    const academicVm = sceneData?.schemaVersion === '3.0.0-academic' ? sceneData as AcademicRenderSceneVm : null
    const academicTitle = academicVm?.title
    const firstSentence = sceneData?.article.sentences[0]?.text
    const title = academicTitle
      || (firstSentence ? firstSentence.split('\n')[0].slice(0, 30) + '...' : null)
      || 'Claread透读 - AI 英语深度解析'
    const path = recordId
      ? `${ROUTES.RESULT}?recordId=${recordId}&mode=replay`
      : ROUTES.RESULT
    return { title, path, imageUrl: appShare }
  })

  const isAcademicMode = sceneData?.schemaVersion === '3.0.0-academic'
  const academicVm = isAcademicMode ? (sceneData as AcademicRenderSceneVm) : null
  const academicContentSummary = academicVm?.contentSummary ?? null
  const academicTitle = academicVm?.title ?? null

  const articleHeader = useMemo(() => {
    if (!sceneData?.request) return null
    const { request } = sceneData
    return (
      <View className='article-header'>
        {isAcademicMode && academicTitle && (
          <Text className='article-title'>{academicTitle}</Text>
        )}
        <ReaderContextBar
          sourceType={request.sourceType}
          readingGoal={request.readingGoal}
          readingVariant={request.readingVariant}
          pageMode={pageMode}
          isAcademicMode={isAcademicMode}
          onModeToggle={() => setPageMode(pageMode === 'immersive' ? 'intensive' : 'immersive')}
          onEdit={() => setShowModeSheet(true)}
          onSettingsClick={() => setShowSettingsSheet(true)}
        />
      </View>
    )
  }, [sceneData, isAcademicMode, academicTitle, pageMode])

  const paragraphBlocks = useMemo(() => {
    if (!sceneData?.article?.paragraphs?.length) return null
    const sentenceMap = new Map(sceneData.article.sentences.map(s => [s.sentenceId, s]))
    return sceneData.article.paragraphs.map((paragraph, idx) => {
      const sentences = paragraph.sentenceIds
        .map((id) => sentenceMap.get(id))
        .filter((s): s is NonNullable<typeof s> => !!s)

      return (
        <ParagraphBlock
          key={`${paragraph.paragraphId}-${idx}`}
          order={idx + 1}
          paragraphId={paragraph.paragraphId}
          sentences={sentences}
          translations={sceneData.translations}
          inlineMarks={sceneData.inlineMarks}
          activeMarkId={activeMarkId}
          selectedWord={selectedWord}
          vocabList={vocabList}
          vocabSavedMap={vocabSavedMap}
          tailEntries={sceneData.sentenceEntries}
          pageMode={pageMode}
          isAcademicMode={isAcademicMode}
          recordId={recordId || undefined}
          cloudId={cloudId || undefined}
          activeSentenceId={activeSentenceId}
          selectionSentenceId={selectionSentenceId}
          selectionRange={selectionRange}
          routeFocusSentenceIds={routeFocusSentenceIds}
          routeFocusRangesBySentence={routeFocusRangesBySentence}
          userAnnotations={userAnnotations}
          readerNotes={readerNotes}
          onWordClick={actions.handleWordClick}
          onSentenceClick={actions.handleSentenceClick}
          onSentenceNotePress={handleOpenSentenceNotes}
          onSelectionContext={handleSelectionContext}
          onMarkActiveChange={state.setActiveMarkId}
        />
      )
    })
  }, [sceneData, activeMarkId, selectedWord, vocabList, vocabSavedMap, pageMode, isAcademicMode, recordId, cloudId, activeSentenceId, selectionSentenceId, selectionRange, handleSelectionContext, handleOpenSentenceNotes, userAnnotations, readerNotes, routeFocusSentenceIds, routeFocusRangesBySentence])

  if (!sceneData) {
    if (pageState === 'loading') {
      return <StateViews pageState='loading' errorCode={null} errorMsg={null} navBarHeight={navBarHeight} onRetry={actions.handleRetry} />
    }
    if (pageState === 'empty') {
      return <StateViews pageState='empty' errorCode={errorCode} errorMsg={errorMsg} navBarHeight={navBarHeight} onRetry={actions.handleRetry} />
    }
    if (pageState === 'failed' || pageState === 'timeout' || pageState === 'network_fail') {
      return <StateViews pageState={pageState} errorCode={errorCode} errorMsg={errorMsg} navBarHeight={navBarHeight} onRetry={actions.handleRetry} />
    }
    return <StateViews pageState='loading' errorCode={null} errorMsg={null} navBarHeight={navBarHeight} onRetry={actions.handleRetry} />
  }

  if (!hasRenderableScene(sceneData)) {
    return (
      <SourceFallback
        pageState={pageState} sceneData={sceneData}
        requestText={requestParams?.text} isReplayMode={isReplayMode}
        navBarHeight={navBarHeight} onRetry={actions.handleRetry}
      />
    )
  }

  return (
    <View className={`result-page ${isAcademicMode ? 'academic-mode' : ''}`}>
      <NavBar title='Claread透读' showBack showHome />
      <View className='result-nav-spacer' style={{ height: navBarHeight + 'px' }} />

      <View className={`result-content-root ${isAcademicMode ? 'academic-mode' : ''} translation-${preferences.translation_display}`} style={{ ...readerStyles, backgroundColor: 'var(--reader-bg-theme)' }}>
        <DegradedBanner pageState={pageState} sceneData={sceneData} onRetry={actions.handleRetry} />

        {isAcademicMode && sceneData?.warnings?.some(w => w.level === 'info' || w.code === 'NON_ACADEMIC_TEXT_DETECTED' || w.code === 'FRAGMENT_INPUT_DETECTED') && (
          <View className='academic-info-banner'>
            <LucideIcon name='info' size={14} color='var(--term-accent)' />
            <Text className='academic-info-text'>
              {sceneData.warnings.find(w => w.code === 'NON_ACADEMIC_TEXT_DETECTED')
                ? '检测到输入文本可能不是学术文献，已自动调整解析策略。如需英语学习模式，可切换至日常阅读。'
                : '检测到片段输入，内容概要可能不完整。'}
            </Text>
          </View>
        )}

        <ScrollView
          className='article-scroll'
          scrollY
          enhanced
          showScrollbar={false}
          scrollIntoView={scrollIntoViewId}
          scrollTop={articleScrollTop}
          scrollWithAnimation
          onScroll={(event) => {
            articleScrollTopRef.current = Number((event as any)?.detail?.scrollTop || articleScrollTopRef.current || 0)
            actions.handleScroll()
            if (selectionContext) clearSelection()
          }}
        >
          <View className='article-container'>
            {articleHeader}
            {academicContentSummary && (
              <ContentSummaryCard summary={academicContentSummary} />
            )}
            {paragraphBlocks}

            <View className='article-end-actions'>
              <View
                key={`fav-btn-${animTrigger}`}
                className={`end-btn-secondary ${favorited ? 'favorited' : ''} ${animTrigger > 0 ? 'animate-spring' : ''}`}
                onClick={actions.handleToggleFavorite}
                role='button'
                aria-label={favorited ? '取消收藏' : '加入收藏'}
              >
                <AnnotationGlyph type='saved_vocab' size={32} state={favorited ? 'active' : 'default'} />
                <Text className={favorited ? 'favorited-text' : ''}>{favorited ? '已收藏' : '收藏'}</Text>
              </View>
              <View
                className='end-btn-primary'
                onClick={actions.handleRetry}
                role='button'
                aria-label='分析新文章'
              >
                <LucideIcon name='plus' size={18} color='var(--color-white)' />
                <Text>再分析一篇</Text>
              </View>
            </View>

            {sceneData && (pageState === 'normal' || pageState === 'degraded_light') && (
              <FeedbackWidget
                recordId={recordId || ''}
                cloudId={cloudId || undefined}
                readingGoal={sceneData.request?.readingGoal}
                readingVariant={sceneData.request?.readingVariant}
                userFacingState={(sceneData as AnyRenderSceneVm).userFacingState}
              />
            )}
            <View className='bottom-spacer' />
          </View>
        </ScrollView>
      </View>

      <WordPopup
        visible={wordPopup.visible}
        mode={wordPopup.mode}
        mark={wordPopup.mark}
        word={wordPopup.word}
        contextSentence={wordPopup.contextSentence}
        occurrence={wordPopup.occurrence}
        x={wordPopup.x}
        y={wordPopup.y}
        readingVariant={sceneData?.request?.readingVariant}
        readingGoal={sceneData?.request?.readingGoal}
        cloudId={cloudId || undefined}
        isSaved={!!vocabSavedMap[wordPopup.word?.toLowerCase()]}
        savedMasteryStatus={vocabSavedMap[wordPopup.word?.toLowerCase()]}
        savedSourceRefs={getVocabEntryByLookupForm(wordPopup.word || '')?.sourceRefs}
        currentSentenceId={wordPopup.mark?.anchor?.sentenceId || activeSentenceId || undefined}
        onClose={actions.handleClosePopup}
        onExpand={() => setWordPopup({ ...wordPopup, mode: 'full' })}
        onAddVocab={actions.handleAddVocab}
      />

      <BottomSheetSelect
        visible={showModeSheet}
        currentGoal={tempConfig.purpose}
        currentLevel={tempConfig.level}
        onClose={() => setShowModeSheet(false)}
        onSelect={actions.handleModeSelect}
      />

      <ReadingSettingsSheet
        visible={showSettingsSheet}
        onClose={() => setShowSettingsSheet(false)}
      />

      <ReadingSelectionToolbar
        visible={!!selectionContext && !showFeedbackSheet}
        context={selectionContext}
        onClose={clearSelection}
        onCopy={handleCopy}
        onFeedback={() => { setShowFeedbackSheet(true) }}
        onHighlight={handleSentenceHighlight}
        onNote={handleSentenceNoteAction}
      />

      <ReaderNoteSheet
        visible={!!noteSheetSentenceId}
        notes={noteSheetNotes}
        mode={noteSheetMode}
        activeNote={activeReaderNote}
        activeNoteId={activeReaderNoteId}
        draftText={noteDraftText}
        submitLabel={editingReaderNote ? '保存笔记' : '创建笔记'}
        onClose={closeNoteSheet}
        onSelectNote={focusReaderNote}
        onDraftChange={setNoteDraftText}
        onSubmitDraft={handleSubmitNoteDraft}
        onOpenActions={handleOpenActiveNoteActions}
      />

      {showFeedbackSheet && selectionContext && (
        <View className='sentence-feedback-overlay' onClick={() => { setShowFeedbackSheet(false); clearSelection() }}>
          <FeedbackSheet
            scope='sentence'
            contextSummary={selectionContext.selectedText}
            payload={{
              targetId: activeSelectionTargetKey || selectionContext.sentenceId,
              analysisRecordId: cloudId || undefined,
              annotationType: 'sentence_action',
              contextJson: {
                sentenceId: selectionContext.sentenceId,
                paragraphId: selectionContext.paragraphId,
                text: selectionContext.selectedText,
                translation: selectionContext.translation,
              },
            }}
            onClose={() => { setShowFeedbackSheet(false); clearSelection() }}
          />
        </View>
      )}
    </View>
  )
}
