import { useEffect, useRef } from 'react'
import Taro from '@tarojs/taro'
import { useArticleStore } from '../../../stores/article'
import { useAuthStore } from '../../../stores/auth'
import { isFavorited, getVocabulary } from '../../../services/storage'
import { fetchVocabHighlights } from '../../../services/api/vocabulary.client'
import { getSimpleLemmaCandidates, hasRenderableScene } from '../utils'
import type { VocabHighlightMatch } from '../../../types/view/vocabulary.vm'
import type { WordPopupState } from './useResultState'

interface EffectDeps {
  recordId: string | null
  cloudId: string | null
  sceneData: import('../../../types/view/render-scene.vm').AnyRenderSceneVm | null
  pageState: import('../../../types/view/render-scene.vm').ResultPageState
  setFavorited: (v: boolean) => void
  setVocabList: (v: string[] | ((prev: string[]) => string[])) => void
  setVocabSavedMap: (v: Record<string, string> | ((prev: Record<string, string>) => Record<string, string>)) => void
  setVocabHighlights: (v: VocabHighlightMatch[]) => void
  loadRecord: (id: string) => void
  recoverActiveTask: () => void
  setWordPopup: (v: WordPopupState | ((prev: WordPopupState) => WordPopupState)) => void
}

export function useResultEffects(deps: EffectDeps) {
  const {
    recordId, cloudId, sceneData, pageState,
    setFavorited, setVocabList, setVocabSavedMap, setVocabHighlights,
    loadRecord, recoverActiveTask, setWordPopup,
  } = deps

  const isLoggedIn = useAuthStore((s) => s.isLoggedIn)
  const highlightsTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (recordId) {
      setFavorited(isFavorited(recordId))
    }
  }, [recordId])

  useEffect(() => {
    const instance = Taro.getCurrentInstance()
    const params = instance.router?.params || {}
    const { recordId: urlRecordId, mode } = params

    if (mode === 'replay' && urlRecordId) {
      useArticleStore.getState().reset()
      loadRecord(urlRecordId)
    }
  }, [loadRecord])

  useEffect(() => {
    if (!recordId) return
    const all = getVocabulary()
    const words = all.flatMap((v) => {
      const forms = [v.word.toLowerCase()]
      if (v.lemma) forms.push(v.lemma.toLowerCase())
      if (v.collectedForms) forms.push(...v.collectedForms.map(f => f.toLowerCase()))
      return forms
    })
    setVocabList([...new Set(words)])

    const savedMap: Record<string, string> = {}
    all.forEach((v) => {
      const status = v.mastered ? 'mastered' : 'new'
      const key = (v.lemma || v.word).toLowerCase()
      savedMap[key] = status
      savedMap[v.word.toLowerCase()] = status
      if (v.lemma) savedMap[v.lemma.toLowerCase()] = status
      v.collectedForms?.forEach(f => { savedMap[f.toLowerCase()] = status })
    })
    setVocabSavedMap(savedMap)
  }, [recordId])

  useEffect(() => {
    if (!sceneData || !hasRenderableScene(sceneData)) return

    const sentences = sceneData.article.sentences.map(s => ({
      sentenceId: s.sentenceId,
      tokens: s.text.split(/\s+/).filter(Boolean),
    }))

    if (highlightsTimerRef.current) clearTimeout(highlightsTimerRef.current)

    highlightsTimerRef.current = setTimeout(async () => {
      if (isLoggedIn) {
        try {
          const matches = await fetchVocabHighlights(sentences)
          setVocabHighlights(matches)
          const highlightWords = matches.map(m => m.anchorText.toLowerCase())
          setVocabList(prev => [...new Set([...prev, ...highlightWords])])
          setVocabSavedMap((prev: Record<string, string>) => {
            const next = { ...prev }
            matches.forEach(m => {
              next[m.anchorText.toLowerCase()] = m.masteryStatus
            })
            return next
          })
        } catch (e) {
          console.error('result: vocab highlights API failed', e)
        }
      } else {
        const all = getVocabulary()
        const localMatches: VocabHighlightMatch[] = []
        const lemmaSet = new Map<string, { id: string; lemma: string; masteryStatus: string; collectedForms: string[] }>()
        const formsReverseIndex = new Map<string, { id: string; lemma: string; masteryStatus: string; collectedForms: string[] }>()
        all.forEach(v => {
          const key = (v.lemma || v.word).toLowerCase()
          const entry = {
            id: v.id,
            lemma: v.lemma || v.word,
            masteryStatus: v.mastered ? 'mastered' : 'new',
            collectedForms: (v.collectedForms || []).map(f => f.toLowerCase()),
          }
          lemmaSet.set(key, entry)
          if (entry.collectedForms.length > 0) {
            formsReverseIndex.set(key, entry)
            entry.collectedForms.forEach(f => {
              if (!formsReverseIndex.has(f)) {
                formsReverseIndex.set(f, entry)
              }
            })
          }
        })

        for (const sent of sentences) {
          const occMap: Record<string, number> = {}
          for (const token of sent.tokens) {
            let cleaned = token.replace(/[.,;:!?'"(){}[\]]/g, '').toLowerCase()
            if (!cleaned) continue

            if (cleaned.endsWith("'s") || cleaned.endsWith("\u2019s")) {
              cleaned = cleaned.slice(0, -2)
            } else if (cleaned.endsWith("'") && cleaned.length > 1) {
              cleaned = cleaned.slice(0, -1)
            }

            let match = lemmaSet.get(cleaned) || formsReverseIndex.get(cleaned)

            if (!match) {
              const candidates = getSimpleLemmaCandidates(cleaned)
              for (const cand of candidates) {
                const found = lemmaSet.get(cand) || formsReverseIndex.get(cand)
                if (found) { match = found; break }
              }
            }

            if (!match) continue
            occMap[match.lemma] = (occMap[match.lemma] || 0) + 1
            localMatches.push({
              vocabId: match.id,
              lemma: match.lemma,
              sentenceId: sent.sentenceId,
              anchorText: token,
              occurrence: occMap[match.lemma],
              masteryStatus: match.masteryStatus,
            })
          }
        }
        setVocabHighlights(localMatches)
        setVocabSavedMap((prev: Record<string, string>) => {
          const next = { ...prev }
          localMatches.forEach(m => {
            next[m.anchorText.toLowerCase()] = m.masteryStatus
          })
          return next
        })
      }
    }, 500)

    return () => {
      if (highlightsTimerRef.current) clearTimeout(highlightsTimerRef.current)
    }
  }, [sceneData, recordId, isLoggedIn])

  Taro.useDidShow(() => {
    if ((pageState === 'loading' || pageState === 'failed') && !sceneData) {
      recoverActiveTask()
    }
  })
}
