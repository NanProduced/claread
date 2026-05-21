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
import FeedbackSheet from '../../components/FeedbackSystem/FeedbackSheet'
import { useResultState } from './hooks/useResultState'
import { useResultEffects } from './hooks/useResultEffects'
import { useResultActions } from './hooks/useResultActions'
import { PAGE_MODE_OPTIONS, hasRenderableScene } from './utils'
import { getVocabEntryByLookupForm } from '../../services/storage'
import DegradedBanner from './components/DegradedBanner'
import SourceFallback from './components/SourceFallback'
import StateViews from './components/StateViews'
import appShare from '../../assets/images/share/app-share.jpg'
import './index.scss'

function buildTargetKey(
  recordId: string,
  anchorType: 'sentence' | 'paragraph' | 'text_range' | 'multi_text',
  opts: { sentenceId?: string; paragraphId?: string; startOffset?: number; endOffset?: number; textHash?: string; segments?: Array<{ paragraphId?: string; sentenceId: string; startOffset: number; endOffset: number; textHash: string }> }
): string {
  if (anchorType === 'sentence') return buildSentenceTargetKey(recordId, opts.sentenceId || '')
  if (anchorType === 'paragraph') return `record:${recordId}:paragraph:${opts.paragraphId}`
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
  const [activeReaderNoteId, setActiveReaderNoteId] = useState<string | null>(null)
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
    listUserAnnotations(cloudId).then(setUserAnnotations).catch(err => {
      console.warn('Failed to load user annotations', err)
    })
    listReaderNotes(cloudId).then(setReaderNotes).catch(err => {
      console.warn('Failed to load reader notes', err)
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
      setActiveReaderNoteId(matchedReaderNote.id)
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
      paragraphId: selectionContext.paragraphId,
      startOffset: selectionContext.startOffset,
      endOffset: selectionContext.endOffset,
      textHash: selectionContext.textHash,
    })
  }, [selectionContext, cloudId, recordId])
  const routeFocusSentenceIds = useMemo(() => new Set(routeFocus?.sentenceIds || []), [routeFocus])
  const routeFocusRangesBySentence = useMemo(() => routeFocus?.rangesBySentence || {}, [routeFocus])

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

  const handleOpenSentenceNotes = useCallback((sentenceId: string) => {
    setNoteSheetSentenceId(sentenceId)
    setActiveReaderNoteId(prev => {
      const next = readerNotes.find(note => note.anchor_sentence_id === sentenceId)
      return next?.id || prev
    })
    clearSelection()
  }, [clearSelection, readerNotes])

  const focusReaderNote = useCallback((note: ReaderNoteDto) => {
    const focus = buildRouteFocusFromReaderNote(note)
    routeSentenceIdRef.current = note.anchor_sentence_id
    scrolledSentenceIdRef.current = null
    setActiveReaderNoteId(note.id)
    setActiveSentenceId(note.anchor_sentence_id)
    setScrollIntoViewId(getSentenceAnchorId(note.anchor_sentence_id))
    setRouteFocus(focus)
  }, [setActiveSentenceId])

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
      />

      <ReaderNoteSheet
        visible={!!noteSheetSentenceId}
        sentenceText={noteSheetSentenceText}
        notes={noteSheetNotes}
        activeNoteId={activeReaderNoteId}
        onClose={() => setNoteSheetSentenceId(null)}
        onSelectNote={focusReaderNote}
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
