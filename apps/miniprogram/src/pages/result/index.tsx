import { useMemo, useState, useCallback, useEffect, useRef } from 'react'
import { View, Text, ScrollView } from '@tarojs/components'
import Taro, { useShareAppMessage } from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import { PageMode, AnyRenderSceneVm, AcademicRenderSceneVm, AnySentenceEntryModel } from '../../types/view/render-scene.vm'
import { getSafeDisplayLabel } from '../../config/purpose'
import { submitFeedback } from '../../services/api/feedback.client'
import { useAuthStore } from '../../stores/auth'
import { ensureLoggedIn } from '../../services/auth'
import NavBar from '../../components/NavBar'
import ParagraphBlock, { getSentenceAnchorId } from '../../components/ParagraphBlock'
import WordPopup from '../../components/WordPopup'
import ContentSummaryCard from '../../components/ContentSummaryCard'
import LucideIcon from '../../components/LucideIcon'
import AnnotationGlyph from '../../components/AnnotationGlyph'
import BottomSheetSelect from '../../components/BottomSheetSelect'
import FeedbackWidget from '../../components/FeedbackWidget'
import ReaderContextBar from '../../components/ReaderContextBar'
import ReadingSettingsSheet from '../../components/ReadingSettingsSheet'
import ReadingSelectionToolbar, { SelectionContext } from '../../components/ReadingSelectionToolbar'
import UserNoteSheet from '../../components/UserNoteSheet'
import { useReadingPreferencesStore } from '../../stores/reading-preferences'
import { UserAnnotationDto, listUserAnnotations, createUserAnnotation, updateUserAnnotation, deleteUserAnnotation } from '../../services/api/user-annotations.client'
import { addFavoriteToCloud, removeFavoriteFromCloud, fetchCloudFavoriteItems, FavoriteItemDto } from '../../services/api/favorites.client'
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
  anchorType: 'sentence' | 'paragraph' | 'text_range',
  opts: { sentenceId?: string; paragraphId?: string; startOffset?: number; endOffset?: number; textHash?: string }
): string {
  if (anchorType === 'sentence') return `record:${recordId}:sentence:${opts.sentenceId}`
  if (anchorType === 'paragraph') return `record:${recordId}:paragraph:${opts.paragraphId}`
  return `record:${recordId}:range:${opts.sentenceId}:${opts.startOffset}:${opts.endOffset}:${opts.textHash}`
}

function trimReviewText(text: string | undefined, max = 54): string | undefined {
  if (!text) return undefined
  const normalized = text.replace(/[#>*_`-]/g, '').replace(/\s+/g, ' ').trim()
  if (!normalized) return undefined
  return normalized.length > max ? `${normalized.slice(0, max)}...` : normalized
}

function getReviewAssetLabel(entryType: string): string | null {
  switch (entryType) {
    case 'grammar_note':
      return '语法'
    case 'sentence_analysis':
      return '句析'
    case 'term_note':
      return '术语'
    case 'logic_note':
      return '逻辑'
    case 'interpretation_note':
      return '解读'
    case 'content_summary':
      return '概要'
    default:
      return null
  }
}

function buildReviewAssets(sceneData: AnyRenderSceneVm | null, sentenceId?: string) {
  if (!sceneData || !sentenceId) return []
  const seen = new Set<string>()
  return ((sceneData.sentenceEntries || []) as AnySentenceEntryModel[])
    .filter(entry => entry.sentenceId === sentenceId)
    .map(entry => {
      const label = getReviewAssetLabel(entry.entryType)
      if (!label) return null
      const title = trimReviewText(entry.title || entry.label || label, 30) || label
      const key = `${entry.entryType}:${title}`
      if (seen.has(key)) return null
      seen.add(key)
      return {
        id: entry.id,
        type: entry.entryType,
        label,
        title,
        summary: trimReviewText(entry.content, 260),
      }
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item))
    .slice(0, 4)
}

async function requireAuth(): Promise<boolean> {
  if (useAuthStore.getState().isLoggedIn) return true
  const result = await ensureLoggedIn()
  return result.success
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
  const [userAnnotations, setUserAnnotations] = useState<UserAnnotationDto[]>([])
  const [favoriteItems, setFavoriteItems] = useState<FavoriteItemDto[]>([])
  const [showNoteSheet, setShowNoteSheet] = useState(false)
  const [showFeedbackSheet, setShowFeedbackSheet] = useState(false)
  const [scrollIntoViewId, setScrollIntoViewId] = useState('')
  const [articleScrollTop, setArticleScrollTop] = useState(0)
  const routeSentenceIdRef = useRef<string | null>(null)
  const scrolledSentenceIdRef = useRef<string | null>(null)
  const articleScrollTopRef = useRef(0)
  const { preferences } = useReadingPreferencesStore()
  const isLoggedIn = useAuthStore(state => state.isLoggedIn)

  if (routeSentenceIdRef.current === null) {
    const params = Taro.getCurrentInstance().router?.params || {}
    routeSentenceIdRef.current = typeof params.sentenceId === 'string' ? params.sentenceId : ''
  }

  const articleTitleForAssets = useMemo(() => {
    if (!sceneData) return undefined
    const academic = sceneData.schemaVersion === '3.0.0-academic' ? sceneData as AcademicRenderSceneVm : null
    const explicitTitle = academic?.title?.trim()
    if (explicitTitle) return explicitTitle
    const firstSentence = sceneData.article.sentences[0]?.text?.trim()
    if (firstSentence) return firstSentence.length > 42 ? `${firstSentence.slice(0, 42)}...` : firstSentence
    const requestText = requestParams?.text?.trim()
    return requestText ? (requestText.length > 42 ? `${requestText.slice(0, 42)}...` : requestText) : undefined
  }, [sceneData, requestParams])

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
      return
    }
    listUserAnnotations(cloudId).then(setUserAnnotations).catch(err => {
      console.warn('Failed to load user annotations', err)
    })
  }, [cloudId, pageState, isLoggedIn])

  useEffect(() => {
    if (pageState !== 'normal' || !isLoggedIn) return
    fetchCloudFavoriteItems().then(res => setFavoriteItems(res.items)).catch(err => {
      console.warn('Failed to load favorites', err)
    })
  }, [pageState, isLoggedIn])

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
    })
  }, [selectionContext, cloudId, recordId])

  const annotationsByTargetKey = useMemo(() => {
    const map = new Map<string, UserAnnotationDto>()
    userAnnotations.forEach(annotation => {
      map.set(annotation.target_key, annotation)
    })
    return map
  }, [userAnnotations])

  const favoriteTargetKeys = useMemo(() => new Set(
    favoriteItems
      .filter(item => item.target_type === 'sentence' || item.target_type === 'paragraph')
      .map(item => item.target_key)
  ), [favoriteItems])

  const currentSelectionAnnotation = activeSelectionTargetKey
    ? annotationsByTargetKey.get(activeSelectionTargetKey)
    : undefined
  const isSelectionFavorited = activeSelectionTargetKey
    ? favoriteTargetKeys.has(activeSelectionTargetKey)
    : false

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

  const handleFavoriteSelection = async () => {
    if (!selectionContext || !activeSelectionTargetKey) return
    const authed = await requireAuth()
    if (!authed) return

    try {
      const reviewAssets = buildReviewAssets(sceneData, selectionContext.sentenceId)
      if (isSelectionFavorited) {
        await removeFavoriteFromCloud(activeSelectionTargetKey, selectionContext.anchorType)
        setFavoriteItems(prev => prev.filter(item => item.target_key !== activeSelectionTargetKey))
        Taro.showToast({ title: '已取消收藏', icon: 'success' })
        clearSelection()
        return
      }
      await addFavoriteToCloud(
        cloudId || null,
        activeSelectionTargetKey,
        selectionContext.anchorType,
        {
          paragraph_id: selectionContext.paragraphId,
          sentence_id: selectionContext.sentenceId,
          client_record_id: recordId,
          text: selectionContext.selectedText,
          translation: selectionContext.translation,
          article_title: articleTitleForAssets,
          start_offset: selectionContext.startOffset,
          end_offset: selectionContext.endOffset,
          anchor_type: selectionContext.anchorType,
          review_assets: reviewAssets,
        }
      )
      setFavoriteItems(prev => [
        {
          id: activeSelectionTargetKey,
          target_type: selectionContext.anchorType,
          target_key: activeSelectionTargetKey,
          analysis_record_id: cloudId || null,
          payload_json: {
            paragraph_id: selectionContext.paragraphId,
            sentence_id: selectionContext.sentenceId,
            client_record_id: recordId,
            text: selectionContext.selectedText,
            translation: selectionContext.translation,
            article_title: articleTitleForAssets,
            anchor_type: selectionContext.anchorType,
            review_assets: reviewAssets,
          },
          note: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
        ...prev.filter(item => item.target_key !== activeSelectionTargetKey),
      ])
      Taro.showToast({ title: '已收藏', icon: 'success' })
      clearSelection()
    } catch (err: any) {
      Taro.showToast({ title: err.message || '收藏失败', icon: 'none' })
    }
  }

  const handleOpenNote = async () => {
    if (!selectionContext) return
    const authed = await requireAuth()
    if (!authed) return
    setShowNoteSheet(true)
  }

  const handleNoteSave = async (color: string, note: string) => {
    if (!selectionContext || !activeSelectionTargetKey) return
    const authed = await requireAuth()
    if (!authed) return

    try {
      const reviewAssets = buildReviewAssets(sceneData, selectionContext.sentenceId)
      Taro.showLoading({ title: '保存中...' })
      const res = currentSelectionAnnotation
        ? await updateUserAnnotation(currentSelectionAnnotation.id, { color, note })
        : await createUserAnnotation({
          analysis_record_id: cloudId || undefined,
          annotation_type: note ? 'note' : 'highlight',
          anchor_type: selectionContext.anchorType,
          target_key: activeSelectionTargetKey,
          paragraph_id: selectionContext.paragraphId,
          sentence_id: selectionContext.sentenceId,
          selected_text: selectionContext.selectedText,
          color,
          note,
          payload_json: {
            source: 'result_page',
            client_record_id: recordId,
            translation: selectionContext.translation,
            article_title: articleTitleForAssets,
            review_assets: reviewAssets,
          },
        })
      setUserAnnotations(prev => {
        const filtered = prev.filter(a => a.target_key !== res.target_key)
        return [res, ...filtered]
      })
      Taro.hideLoading()
      Taro.showToast({ title: note ? '笔记已保存' : '高亮已添加', icon: 'success' })
      clearSelection()
      setShowNoteSheet(false)
    } catch (e: any) {
      Taro.hideLoading()
      Taro.showToast({ title: '保存失败', icon: 'none' })
    }
  }

  const handleHighlightOnly = async (color: string) => {
    if (!selectionContext || !activeSelectionTargetKey) return
    const authed = await requireAuth()
    if (!authed) return

    try {
      const reviewAssets = buildReviewAssets(sceneData, selectionContext.sentenceId)
      Taro.showLoading({ title: '保存中...' })
      const res = currentSelectionAnnotation
        ? await updateUserAnnotation(currentSelectionAnnotation.id, { color, note: '' })
        : await createUserAnnotation({
          analysis_record_id: cloudId || undefined,
          annotation_type: 'highlight',
          anchor_type: selectionContext.anchorType,
          target_key: activeSelectionTargetKey,
          paragraph_id: selectionContext.paragraphId,
          sentence_id: selectionContext.sentenceId,
          selected_text: selectionContext.selectedText,
          color,
          payload_json: {
            source: 'result_page',
            client_record_id: recordId,
            translation: selectionContext.translation,
            article_title: articleTitleForAssets,
            review_assets: reviewAssets,
          },
        })
      setUserAnnotations(prev => {
        const filtered = prev.filter(a => a.target_key !== res.target_key)
        return [res, ...filtered]
      })
      Taro.hideLoading()
      Taro.showToast({ title: '高亮已添加', icon: 'success' })
      clearSelection()
      setShowNoteSheet(false)
    } catch (e: any) {
      Taro.hideLoading()
      Taro.showToast({ title: '保存失败', icon: 'none' })
    }
  }

  const handleDeleteAnnotation = async () => {
    if (!currentSelectionAnnotation) return
    const authed = await requireAuth()
    if (!authed) return

    try {
      Taro.showLoading({ title: '删除中...' })
      await deleteUserAnnotation(currentSelectionAnnotation.id)
      setUserAnnotations(prev => prev.filter(item => item.id !== currentSelectionAnnotation.id))
      Taro.hideLoading()
      Taro.showToast({ title: '批注已删除', icon: 'success' })
      setShowNoteSheet(false)
      clearSelection()
    } catch {
      Taro.hideLoading()
      Taro.showToast({ title: '删除失败', icon: 'none' })
    }
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
          userAnnotations={userAnnotations}
          favoriteTargetKeys={favoriteTargetKeys}
          onWordClick={actions.handleWordClick}
          onSentenceClick={actions.handleSentenceClick}
          onSelectionContext={handleSelectionContext}
          onMarkActiveChange={state.setActiveMarkId}
        />
      )
    })
  }, [sceneData, activeMarkId, selectedWord, vocabList, vocabSavedMap, pageMode, isAcademicMode, recordId, cloudId, activeSentenceId, selectionSentenceId, selectionRange, handleSelectionContext, userAnnotations, favoriteTargetKeys])

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
        visible={!!selectionContext && !showNoteSheet && !showFeedbackSheet}
        context={selectionContext}
        isFavorited={isSelectionFavorited}
        hasAnnotation={!!currentSelectionAnnotation}
        hasNote={!!currentSelectionAnnotation?.note}
        onClose={clearSelection}
        onCopy={handleCopy}
        onFavorite={handleFavoriteSelection}
        onNote={handleOpenNote}
        onFeedback={() => { setShowFeedbackSheet(true) }}
      />

      <UserNoteSheet
        visible={showNoteSheet}
        selectionText={selectionContext?.selectedText}
        initialColor={currentSelectionAnnotation?.color || 'soft_green'}
        initialNote={currentSelectionAnnotation?.note || ''}
        hasExistingAnnotation={!!currentSelectionAnnotation}
        onClose={() => setShowNoteSheet(false)}
        onSave={handleNoteSave}
        onHighlightOnly={handleHighlightOnly}
        onDelete={currentSelectionAnnotation ? handleDeleteAnnotation : undefined}
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
