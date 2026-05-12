import { View, Text } from '@tarojs/components'
import { useCallback, useEffect, useState, useRef } from 'react'
import Taro, { usePageScroll, useShareAppMessage } from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import { useDailyReaderStore } from '../../stores/daily-reader'
import NavBar from '../../components/NavBar'
import DailyReaderHeader from '../../components/DailyReaderHeader'
import DailyReaderBody from '../../components/DailyReaderBody'
import DailyReaderFooterAnalysis from '../../components/DailyReaderFooterAnalysis'
import DailyReaderProgress from '../../components/DailyReaderProgress'
import WordPopup from '../../components/WordPopup'
import { highlightToInlineMark } from '../../services/api/adapters/daily-reader-highlight.adapter'
import type { DailyReaderHighlight } from '../../types/view/daily-reader.vm'
import type { InlineMarkModel, DictionaryResult } from '../../types/view/render-scene.vm'
import { isFavorited, saveFavorite, removeFavorite, saveVocabEntry } from '../../services/storage'
import { CloudSyncService } from '../../services/cloudSync.service'
import { track } from '../../services/analytics'
import type { VocabEntry } from '../../types/view/vocabulary.vm'
import type { FavoriteRecord } from '../../types/view/favorites.vm'
import { getDailyReaderSourceDisplay } from '../../utils/daily-reader-source'
import LucideIcon from '../../components/LucideIcon'
import share01 from '../../assets/images/share/daily-reader-01.jpg'
import share02 from '../../assets/images/share/daily-reader-02.jpg'
import share03 from '../../assets/images/share/daily-reader-03.jpg'
import share04 from '../../assets/images/share/daily-reader-04.jpg'
import share05 from '../../assets/images/share/daily-reader-05.jpg'
import share06 from '../../assets/images/share/daily-reader-06.jpg'
import share07 from '../../assets/images/share/daily-reader-07.jpg'

const SHARE_IMAGES = [share01, share02, share03, share04, share05, share06, share07]

function pickShareImage(id: string): string {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash + id.charCodeAt(i)) | 0
  }
  return SHARE_IMAGES[Math.abs(hash) % SHARE_IMAGES.length]
}
import './index.scss'

const SOLID_NAV_SHOW_THRESHOLD = 260
const SOLID_NAV_HIDE_THRESHOLD = 180

export default function DailyReaderPage() {
  const {
    currentArticle: article,
    loading,
    error,
    fetchArticle,
  } = useDailyReaderStore()

  const [popupVisible, setPopupVisible] = useState(false)
  const [popupMode, setPopupMode] = useState<'mini' | 'full'>('mini')
  const [activeMark, setActiveMark] = useState<InlineMarkModel | null>(null)
  const [activeWord, setActiveWord] = useState('')
  const [contextSentence, setContextSentence] = useState<string | undefined>()
  const [tapPosition, setTapPosition] = useState({ x: 0, y: 0 })
  const [favorited, setFavorited] = useState(false)
  const [showSolidNav, setShowSolidNav] = useState(false)
  const [showHighlightHint, setShowHighlightHint] = useState(false)
  const solidNavShown = useRef(false)

  useEffect(() => {
    if (article) {
      setFavorited(isFavorited(article.id))
      const onboardingDone = Taro.getStorageSync('dr_onboarding_done')
      if (!onboardingDone && article.highlights.length > 0) {
        const timer = setTimeout(() => setShowHighlightHint(true), 1800)
        return () => clearTimeout(timer)
      }
    }
  }, [article])

  const dismissHint = useCallback(() => {
    setShowHighlightHint(false)
    Taro.setStorageSync('dr_onboarding_done', '1')
  }, [])

  useEffect(() => {
    const params = Taro.getCurrentInstance().router?.params
    const id = params?.id
    if (id) {
      fetchArticle(id)
    }
  }, [fetchArticle])

  usePageScroll((res) => {
    Taro.eventCenter.trigger('dailyReaderPageScroll', res)
    if (res.scrollTop > SOLID_NAV_SHOW_THRESHOLD && !solidNavShown.current) {
      solidNavShown.current = true
      setShowSolidNav(true)
    } else if (res.scrollTop <= SOLID_NAV_HIDE_THRESHOLD && solidNavShown.current) {
      solidNavShown.current = false
      setShowSolidNav(false)
    }
  })

  useShareAppMessage(() => {
    if (!article) return { title: 'Claread透读', path: ROUTES.HOME, imageUrl: SHARE_IMAGES[0] }
    return {
      title: `${article.title} — Claread 每日精读`,
      path: `${ROUTES.DAILY_READER}?id=${article.id}`,
      imageUrl: pickShareImage(article.id),
    }
  })

  const handleHighlightClick = useCallback((highlight: DailyReaderHighlight, tapPosition?: { x: number; y: number }, sourceContext?: string) => {
    dismissHint()
    const mark = highlightToInlineMark(highlight)
    setActiveMark(mark)
    setActiveWord(highlight.text)
    setContextSentence(sourceContext)
    if (tapPosition) {
      setTapPosition(tapPosition)
    }
    setPopupMode('mini')
    setPopupVisible(true)
  }, [dismissHint])

  const handleWordClick = useCallback((word: string, tapPosition?: { x: number; y: number }) => {
    dismissHint()
    setActiveMark(null)
    setActiveWord(word)
    setContextSentence(undefined)
    if (tapPosition) {
      setTapPosition(tapPosition)
    }
    setPopupMode('mini')
    setPopupVisible(true)
  }, [dismissHint])

  const handleExpandPopup = useCallback(() => {
    setPopupMode('full')
  }, [])

  const handleClosePopup = useCallback(() => {
    setPopupVisible(false)
    setActiveMark(null)
    setActiveWord('')
    setContextSentence(undefined)
  }, [])

  const handleAddVocab = useCallback((word: string, dictResult: DictionaryResult | null) => {
    if (!article || !dictResult || dictResult.resultType !== 'entry') return
    const detailEntry = dictResult.entry
    const detailMeanings = detailEntry.meanings
    const derivedMeaning = detailMeanings[0]?.definitions
      ?.map((d: { meaning: string }) => d.meaning)
      .filter(Boolean)
      .join('；') || ''
    const lemma = detailEntry.baseWord ?? detailEntry.word
    const vocabEntry: VocabEntry = {
      id: `${article.id}_${lemma.toLowerCase()}_${Date.now()}`,
      lemma,
      word: word,
      partOfSpeech: detailMeanings[0]?.partOfSpeech || '',
      meaning: derivedMeaning.slice(0, 200),
      addedAt: Date.now(),
      mastered: false,
      dictEntryId: detailEntry.id,
      phonetic: detailEntry.phonetic,
      provider: dictResult.provider || 'tecd3',
      sentence: contextSentence,
      detailMeanings: detailMeanings.map((m: { partOfSpeech?: string; definitions: Array<{ meaning: string; example?: string; exampleTranslation?: string }> }) => ({
        partOfSpeech: m.partOfSpeech || '',
        definitions: m.definitions.map((d: { meaning: string; example?: string; exampleTranslation?: string }) => {
          const def: { meaning: string; example?: string; exampleTranslation?: string } = { meaning: d.meaning }
          if (d.example) def.example = d.example
          if (d.exampleTranslation) def.exampleTranslation = d.exampleTranslation
          return def
        }).filter((d: { meaning: string }) => d.meaning),
      })).filter((m: { definitions: Array<{ meaning: string }> }) => m.definitions.length > 0),
      detailPhrases: detailEntry.phrases?.length > 0 ? detailEntry.phrases : undefined,
      detailExamples: detailEntry.examples?.length > 0 ? detailEntry.examples : undefined,
      exchange: detailEntry.exchange || [],
      tags: detailEntry.tags || [],
      sourceRefs: [{
        clientRecordId: article.id,
        cloudRecordId: undefined,
        sourceSentence: contextSentence || undefined,
        sourceAnchorText: word,
        sourceOccurrence: activeMark?.anchor.kind === 'text' ? activeMark.anchor.occurrence : undefined,
        collectedAt: new Date().toISOString(),
      }],
    }

    const result = saveVocabEntry(vocabEntry)
    if (result.merged) {
      Taro.showToast({
        title: `${word} 已添加到 ${lemma}`,
        icon: 'none',
        duration: 2000,
      })
    } else {
      Taro.showToast({ title: `${word} 已记入生词本`, icon: 'success' })
    }

    track('add_vocab', { word, merged: result.merged })
    CloudSyncService.syncVocab(result.entry, 'daily_reader_article')
  }, [article, contextSentence, activeMark])

  const handleFavorite = useCallback(() => {
    if (!article) return
    const isAdding = !favorited

    if (isAdding) {
      saveFavorite({ recordId: article.id, cloudId: undefined, createdAt: Date.now() } as FavoriteRecord)
      setFavorited(true)
      track('favorite', { isFavorited: true })
      Taro.showToast({ title: '已收藏全文', icon: 'success', duration: 1500 })
      CloudSyncService.syncFavorite(undefined, article.id, 'add', 'daily_reader_article')
    } else {
      removeFavorite(article.id)
      setFavorited(false)
      track('favorite', { isFavorited: false })
      Taro.showToast({ title: '已取消收藏', icon: 'none', duration: 1500 })
      CloudSyncService.syncFavorite(undefined, article.id, 'remove', 'daily_reader_article')
    }
  }, [article, favorited])

  if (loading && !article) {
    return (
      <View className='daily-page daily-page--loading'>
        <NavBar title='精读' showBack background='transparent' color='#FFFFFF' />
        <View className='daily-page__loading-container'>
          <View className='daily-page__loading-icon'>
            <View className='daily-page__loading-dot' />
            <View className='daily-page__loading-dot' />
            <View className='daily-page__loading-dot' />
          </View>
          <Text className='daily-page__loading-text'>正在加载文章...</Text>
        </View>
      </View>
    )
  }

  if (error || !article) {
    return (
      <View className='daily-page daily-page--error'>
        <NavBar title='精读' showBack showHome background='var(--dr-bg)' color='var(--dr-text-heading)' />
        <View className='daily-page__error-container'>
          <Text className='daily-page__error-icon'>📄</Text>
          <Text className='daily-page__error-text'>{error || '文章未找到'}</Text>
          <View className='daily-page__error-actions'>
            <View
              className='daily-page__error-btn'
              onClick={() => {
                const params = Taro.getCurrentInstance().router?.params
                const id = params?.id
                if (id) fetchArticle(id)
              }}
            >
              <Text className='daily-page__error-btn-text'>重新加载</Text>
            </View>
            <View
              className='daily-page__error-btn daily-page__error-btn--secondary'
              onClick={() => Taro.navigateBack()}
            >
              <Text className='daily-page__error-btn-text'>返回上页</Text>
            </View>
          </View>
        </View>
      </View>
    )
  }

  const sourceDisplay = getDailyReaderSourceDisplay(article.source)

  return (
    <View className='daily-page'>
      <NavBar
        title={sourceDisplay.shortName}
        showBack
        showHome
        background={showSolidNav ? 'var(--dr-bg)' : 'transparent'}
        color={showSolidNav ? 'var(--dr-text-heading)' : '#FFFFFF'}
      />
      <DailyReaderProgress />
      <DailyReaderHeader article={article} />
      <View className='daily-page__body-divider' />
      <DailyReaderBody
        body={article.body}
        highlights={article.highlights}
        onHighlightClick={handleHighlightClick}
        onWordClick={handleWordClick}
        showHighlightHint={showHighlightHint}
      />
      <DailyReaderFooterAnalysis
        footerAnalysis={article.footerAnalysis}
        sourceUrl={article.sourceUrl}
        source={article.source}
      />
      <View className='daily-page__end-actions'>
        <View
          className={`daily-page__action-btn daily-page__action-btn--primary ${favorited ? 'daily-page__action-btn--favorited' : ''}`}
          onClick={handleFavorite}
        >
          <LucideIcon name='star' size={20} color={favorited ? 'var(--color-warning)' : 'var(--dr-text-sub)'} />
          <Text className='daily-page__action-btn-text'>{favorited ? '已收藏' : '收藏本文'}</Text>
        </View>
        <View
          className='daily-page__action-btn daily-page__action-btn--secondary'
          onClick={() => Taro.navigateTo({ url: ROUTES.DAILY_READER_ARCHIVE })}
        >
          <LucideIcon name='clock' size={20} color='var(--dr-text-sub)' />
          <Text className='daily-page__action-btn-text'>往期文章</Text>
        </View>
      </View>
      {showHighlightHint && (
        <View className='daily-page__hint-overlay' onClick={dismissHint}>
          <View className='daily-page__hint-bubble' onClick={(e) => e.stopPropagation()}>
            <Text className='daily-page__hint-text'>点击高亮词可查释义</Text>
            <LucideIcon name='ArrowUp' size={14} color='var(--dr-accent)' />
          </View>
        </View>
      )}
      <WordPopup
        visible={popupVisible}
        mode={popupMode}
        mark={activeMark}
        word={activeWord}
        contextSentence={contextSentence}
        x={tapPosition.x}
        y={tapPosition.y}
        onClose={handleClosePopup}
        onExpand={handleExpandPopup}
        onAddVocab={handleAddVocab}
      />
    </View>
  )
}
