// @ts-nocheck
// CUTOVER-MINI-LONG: deregistered in Logical phase; scheduled for Physical deletion.
import Taro from '@tarojs/taro'
import { useArticleStore } from '../../../stores/article'
import { updateRecord } from '../../../services/storage'
import { CloudSyncService } from '../../../services/cloudSync.service'
import { track } from '../../../services/analytics'
import { getApiParams, SERVER_GOAL_TO_UI_GOAL, ReadingGoal } from '../../../config/purpose'
import type { AnalyzeRequest } from '../../../services/api/client'
import { ROUTES } from '../../../config/routes'
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
    updateRecord(recordId, { isFavorited: isAdding })
    setFavorited(isAdding)
    track('favorite', { isFavorited: isAdding })
    Taro.showToast({ title: isAdding ? '已收藏' : '已取消收藏', icon: 'none' })
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
    Taro.showToast({ title: '生词本功能暂停使用', icon: 'none' })
  }

  return {
    handleWordClick, handleSentenceClick, handleClosePopup,
    handleScroll, handleToggleFavorite, handleModeSelect,
    handleRetry, handleAddVocab,
  }
}
