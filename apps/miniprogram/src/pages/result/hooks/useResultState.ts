import { useState } from 'react'
import { useArticleStore } from '../../../stores/article'
import { useLayoutStore } from '../../../stores/layout'
import type { AnyInlineMarkModel, PageMode } from '../../../types/view/render-scene.vm'
import type { ReadingGoal } from '../../../config/purpose'

interface WordPopupState {
  visible: boolean
  mode: 'mini' | 'full'
  mark: AnyInlineMarkModel | null
  word: string
  contextSentence?: string
  occurrence?: number
  x: number
  y: number
}

const INITIAL_WORD_POPUP: WordPopupState = {
  visible: false, mode: 'mini', mark: null, word: '', x: 0, y: 0,
}

export function useResultState() {
  const { navBarHeight } = useLayoutStore()

  const [pageMode, setPageMode] = useState<PageMode>('intensive')
  const [vocabList, setVocabList] = useState<string[]>([])
  const [vocabSavedMap, setVocabSavedMap] = useState<Record<string, string>>({})
  const [wordPopup, setWordPopup] = useState<WordPopupState>(INITIAL_WORD_POPUP)
  const [activeMarkId, setActiveMarkId] = useState<string | null>(null)
  const [animTrigger, setAnimTrigger] = useState(0)
  const [activeSentenceId, setActiveSentenceId] = useState<string | null>(null)
  const [selectedWord, setSelectedWord] = useState<string | null>(null)
  const [showModeSheet, setShowModeSheet] = useState(false)
  const [tempConfig, setTempConfig] = useState<{
    purpose: ReadingGoal
    level: string | null
  }>({
    purpose: 'daily',
    level: 'intermediate_reading',
  })
  const [favorited, setFavorited] = useState(false)
  const [vocabHighlights, setVocabHighlights] = useState<
    import('../../../types/view/vocabulary.vm').VocabHighlightMatch[]
  >([])

  const pageState = useArticleStore((s) => s.pageState)
  const sceneData = useArticleStore((s) => s.sceneData)
  const requestParams = useArticleStore((s) => s.requestParams)
  const errorCode = useArticleStore((s) => s.errorCode)
  const errorMsg = useArticleStore((s) => s.error)
  const analyze = useArticleStore((s) => s.analyze)
  const loadRecord = useArticleStore((s) => s.loadRecord)
  const recoverActiveTask = useArticleStore((s) => s.recoverActiveTask)
  const recordId = useArticleStore((s) => s.recordId)
  const cloudId = useArticleStore((s) => s.cloudId)
  const isReplayMode = useArticleStore((s) => s.isReplayMode)
  const reset = useArticleStore((s) => s.reset)

  return {
    navBarHeight,
    pageMode, setPageMode,
    vocabList, setVocabList,
    vocabSavedMap, setVocabSavedMap,
    wordPopup, setWordPopup,
    activeMarkId, setActiveMarkId,
    animTrigger, setAnimTrigger,
    activeSentenceId, setActiveSentenceId,
    selectedWord, setSelectedWord,
    showModeSheet, setShowModeSheet,
    tempConfig, setTempConfig,
    favorited, setFavorited,
    vocabHighlights, setVocabHighlights,
    pageState, sceneData, requestParams, errorCode, errorMsg,
    analyze, loadRecord, recoverActiveTask, recordId, cloudId, isReplayMode, reset,
  }
}

export type { WordPopupState }
