import Taro from '@tarojs/taro'
import { useArticleStore } from '../../../stores/article'
import { isFavorited, saveFavorite, removeFavorite, updateRecord, saveVocabEntry, getVocabulary } from '../../../services/storage'
import { CloudSyncService } from '../../../services/cloudSync.service'
import { track } from '../../../services/analytics'
import { getApiParams, SERVER_GOAL_TO_UI_GOAL, ReadingGoal } from '../../../config/purpose'
import type { AnalyzeRequest } from '../../../services/api/client'
import { ROUTES } from '../../../config/routes'
import type { FavoriteRecord } from '../../../types/view/favorites.vm'
import type { VocabEntry, SaveVocabResult } from '../../../types/view/vocabulary.vm'
import type { DictionaryResult } from '../../../types/view/render-scene.vm'
import type { WordClickPayload } from '../../../components/ParagraphBlock'
import type { WordPopupState } from './useResultState'

interface ActionDeps {
  recordId: string | null
  cloudId: string | null
  requestParams: { text?: string; source_type?: AnalyzeRequest['source_type']; reading_goal?: string; reading_variant?: string | null; extended?: boolean } | null
  isReplayMode: boolean
  pageState: import('../../../types/view/render-scene.vm').ResultPageState
  favorited: boolean
  wordPopup: WordPopupState
  activeSentenceId: string | null
  setFavorited: (v: boolean) => void
  setAnimTrigger: (v: number | ((prev: number) => number)) => void
  setActiveMarkId: (v: string | null) => void
  setSelectedWord: (v: string | null) => void
  setActiveSentenceId: (v: string | null) => void
  setWordPopup: (v: WordPopupState | ((prev: WordPopupState) => WordPopupState)) => void
  setVocabList: (v: string[]) => void
  setShowModeSheet: (v: boolean) => void
  setTempConfig: (v: { purpose: ReadingGoal; level: string | null }) => void
  analyze: ReturnType<typeof useArticleStore.getState>['analyze']
  reset: ReturnType<typeof useArticleStore.getState>['reset']
}

export function useResultActions(deps: ActionDeps) {
  const {
    recordId, cloudId, requestParams, isReplayMode, pageState,
    favorited, wordPopup, activeSentenceId,
    setFavorited, setAnimTrigger, setActiveMarkId, setSelectedWord,
    setActiveSentenceId, setWordPopup, setVocabList,
    setShowModeSheet, setTempConfig, analyze, reset,
  } = deps

  const handleWordClick = ({ word, mark, event, contextSentence, occurrence }: WordClickPayload) => {
    setActiveMarkId(mark?.id ?? null)
    setSelectedWord(word)

    const windowInfo = Taro.getWindowInfo()
    const windowWidth = windowInfo.windowWidth || 375
    let clientX = windowWidth / 2
    let clientY = 300

    if (event) {
      const touch = event.changedTouches?.[0] || (event.touches ? event.touches[0] : null)
      if (touch) {
        clientX = touch.clientX ?? touch.pageX
        clientY = touch.clientY ?? touch.pageY
      } else if (event.detail && (event.detail.x !== undefined || event.detail.clientX !== undefined)) {
        clientX = event.detail.x ?? event.detail.clientX
        clientY = event.detail.y ?? event.detail.clientY
      }
    }

    setWordPopup({
      visible: true, mode: 'mini', mark: mark ?? null,
      word, contextSentence, occurrence, x: clientX, y: clientY,
    })
  }

  const handleSentenceClick = (sentenceId: string) => {
    setActiveSentenceId(activeSentenceId === sentenceId ? null : sentenceId)
  }

  const handleClosePopup = () => {
    setWordPopup((prev) => ({ ...prev, visible: false }))
    setActiveMarkId(null)
    setSelectedWord(null)
    setActiveSentenceId(null)
  }

  const handleScroll = () => {
    setWordPopup((prev) => {
      if (!prev.visible || prev.mode !== 'mini') return prev
      setActiveMarkId(null)
      setSelectedWord(null)
      setActiveSentenceId(null)
      return { ...prev, visible: false }
    })
  }

  const handleToggleFavorite = async () => {
    if (!recordId) return
    const isAdding = !favorited
    setAnimTrigger(prev => prev + 1)

    if (isAdding) {
      saveFavorite({ recordId, cloudId: cloudId || undefined, createdAt: Date.now() } as FavoriteRecord)
      updateRecord(recordId, { isFavorited: true })
      setFavorited(true)
      track('favorite', { isFavorited: true })
      Taro.showToast({ title: '已收藏', icon: 'success', duration: 1500 })
      CloudSyncService.syncFavorite(cloudId || undefined, recordId, 'add')
    } else {
      removeFavorite(recordId)
      updateRecord(recordId, { isFavorited: false })
      setFavorited(false)
      track('favorite', { isFavorited: false })
      Taro.showToast({ title: '已取消收藏', icon: 'none', duration: 1500 })
      CloudSyncService.syncFavorite(cloudId || undefined, recordId, 'remove')
    }
  }

  const handleModeSelect = (goal: ReadingGoal, level: string | null) => {
    setShowModeSheet(false)
    const text = requestParams?.text
    const source_type = requestParams?.source_type || 'user_input'

    if (!text) {
      Taro.showToast({ title: '无法获取原文', icon: 'none' })
      return
    }

    const apiParams = getApiParams(goal, level)
    analyze({
      text,
      reading_goal: apiParams.reading_goal,
      reading_variant: apiParams.reading_variant,
      source_type: source_type,
      extended: requestParams?.extended ?? false,
    } as AnalyzeRequest)

    Taro.redirectTo({ url: ROUTES.RESULT })
  }

  const handleRetry = () => {
    const isErrorState = ['failed', 'timeout', 'network_fail', 'empty', 'degraded_heavy'].includes(pageState)

    if (isReplayMode || isErrorState) {
      if (requestParams) {
        setTempConfig({
          purpose: SERVER_GOAL_TO_UI_GOAL[requestParams.reading_goal || ''] || 'daily',
          level: requestParams.reading_variant ?? null,
        })
      }
      setShowModeSheet(true)
    } else {
      reset()
      Taro.redirectTo({ url: ROUTES.INPUT })
    }
  }

  const handleAddVocab = async (w: string, dictResult: DictionaryResult | null) => {
    if (!recordId || !dictResult || dictResult.resultType !== 'entry') return
    const detailEntry = dictResult.entry
    const detailMeanings = detailEntry.meanings
    const derivedMeaning = detailMeanings[0]?.definitions
      ?.map((d: { meaning: string }) => d.meaning)
      .filter(Boolean)
      .join('；') || ''
    const lemma = detailEntry.baseWord ?? detailEntry.word
    const sentenceId = wordPopup.mark?.anchor?.sentenceId || activeSentenceId || undefined
    const vocabEntry: VocabEntry = {
      id: `${recordId}_${lemma.toLowerCase()}_${Date.now()}`,
      lemma,
      word: w,
      partOfSpeech: detailMeanings[0]?.partOfSpeech || '',
      meaning: derivedMeaning.slice(0, 200),
      addedAt: Date.now(),
      mastered: false,
      dictEntryId: detailEntry.id,
      phonetic: detailEntry.phonetic,
      provider: dictResult.provider || 'tecd3',
      sentence: wordPopup.contextSentence,
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
        clientRecordId: recordId,
        cloudRecordId: cloudId || undefined,
        sourceSentence: wordPopup.contextSentence || undefined,
        sourceSentenceId: sentenceId,
        sourceAnchorText: w,
        sourceOccurrence: wordPopup.occurrence,
        collectedAt: new Date().toISOString(),
      }],
    }
    const result: SaveVocabResult = saveVocabEntry(vocabEntry)
    if (result.merged) {
      Taro.showToast({
        title: `${w} 已添加到 ${lemma}（第 ${result.totalSourceCount} 个语境）`,
        icon: 'none',
        duration: 2000,
      })
    } else {
      Taro.showToast({ title: `${w} 已记入生词本`, icon: 'success' })
    }
    const allVocabAfter = getVocabulary()
    const wordsAfter = allVocabAfter.flatMap((v) => {
      const forms = [v.word.toLowerCase()]
      if (v.lemma) forms.push(v.lemma.toLowerCase())
      if (v.collectedForms) forms.push(...v.collectedForms.map(f => f.toLowerCase()))
      return forms
    })
    setVocabList([...new Set(wordsAfter)])
    track('add_vocab', { word: w, merged: result.merged })
    CloudSyncService.syncVocab(result.entry)
  }

  return {
    handleWordClick, handleSentenceClick, handleClosePopup,
    handleScroll, handleToggleFavorite, handleModeSelect,
    handleRetry, handleAddVocab,
  }
}
