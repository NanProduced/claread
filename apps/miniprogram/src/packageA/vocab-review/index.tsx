import { View, Text, ScrollView } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useCallback, useEffect, useState } from 'react'
import { fetchCloudVocabulary, fetchDueVocabulary, submitVocabReview } from '../../services/api/vocabulary.client'
import type { VocabEntry, SourceRef } from '../../types/view/vocabulary.vm'
import { getRecord, getVocabInspectEntry, getVocabulary, updateVocabEntry } from '../../services/storage'
import { ROUTES } from '../../config/routes'
import NavBar from '../../components/NavBar'
import LucideIcon from '../../components/LucideIcon'
import VocabStudyCard from '../../components/VocabStudyCard'
import { useLayoutStore } from '../../stores/layout'
import './index.scss'

const REVIEW_INTERVALS_DAYS = [1, 3, 7, 14, 30]
const DAY_MS = 24 * 60 * 60 * 1000

function isCloudVocabId(id: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
}

function isReviewDue(entry: VocabEntry): boolean {
  if (entry.mastered || entry.masteryStatus === 'mastered') return false
  if (!entry.nextReviewAt) return true
  return new Date(entry.nextReviewAt).getTime() <= Date.now()
}

function buildLocalReviewPatch(entry: VocabEntry, result: 'known' | 'unfamiliar'): Partial<VocabEntry> {
  const reviewCount = (entry.reviewCount || 0) + 1
  if (result === 'unfamiliar') {
    return {
      mastered: false,
      masteryStatus: 'learning',
      reviewStage: 0,
      reviewCount,
      lastReviewedAt: new Date().toISOString(),
      nextReviewAt: new Date(Date.now() + REVIEW_INTERVALS_DAYS[0] * DAY_MS).toISOString(),
    }
  }

  const nextStage = (entry.reviewStage || 0) + 1
  const mastered = nextStage >= REVIEW_INTERVALS_DAYS.length
  return {
    mastered,
    masteryStatus: mastered ? 'mastered' : 'learning',
    reviewStage: nextStage,
    reviewCount,
    lastReviewedAt: new Date().toISOString(),
    nextReviewAt: mastered ? undefined : new Date(Date.now() + REVIEW_INTERVALS_DAYS[nextStage] * DAY_MS).toISOString(),
  }
}

export default function VocabReviewPage() {
  const params = Taro.getCurrentInstance().router?.params || {}
  const mode: 'inspect' | 'review' = params.mode === 'inspect' ? 'inspect' : 'review'
  const inspectVocabId = params.vocabId ? decodeURIComponent(String(params.vocabId)) : ''

  const [vocabList, setVocabList] = useState<VocabEntry[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [isFinished, setIsFinished] = useState(false)
  const [stats, setStats] = useState({ remembered: 0, unfamiliar: 0 })
  const [reinforceList, setReinforceList] = useState<VocabEntry[]>([])

  const { navBarHeight } = useLayoutStore()
  const currentVocab = vocabList[currentIndex]
  const nextVocab = vocabList[currentIndex + 1]

  const loadInspectEntry = useCallback(async (): Promise<VocabEntry | null> => {
    const cached = getVocabInspectEntry()
    if (cached && (!inspectVocabId || cached.id === inspectVocabId)) return cached

    const local = getVocabulary().find(item => item.id === inspectVocabId)
    if (local) return local

    let page = 1
    const pageSize = 100
    while (page <= 10) {
      const res = await fetchCloudVocabulary(page, pageSize)
      const found = res.items.find(item => item.id === inspectVocabId)
      if (found) return found
      if (res.items.length === 0 || page * pageSize >= res.total) break
      page++
    }
    return null
  }, [inspectVocabId])

  const loadEntries = useCallback(async () => {
    setLoading(true)
    setIsFinished(false)
    try {
      if (mode === 'inspect') {
        const entry = await loadInspectEntry()
        setVocabList(entry ? [entry] : [])
        return
      }

      const res = await fetchDueVocabulary(50)
      const localDue = getVocabulary().filter(isReviewDue)
      const seen = new Set(res.items.map(item => (item.lemma || item.word).toLowerCase()))
      const localOnlyDue = localDue.filter(item => {
        const key = (item.lemma || item.word).toLowerCase()
        return !seen.has(key) && (!isCloudVocabId(item.id) || item.syncState === 'local_only' || !!item.pendingOp)
      })
      const dueItems = [...res.items, ...localOnlyDue]
      setVocabList(dueItems)
      setIsFinished(dueItems.length === 0)
    } catch {
      if (mode === 'inspect') {
        const cached = getVocabInspectEntry()
        const local = getVocabulary().find(item => item.id === inspectVocabId)
        const fallback = cached && (!inspectVocabId || cached.id === inspectVocabId) ? cached : local
        setVocabList(fallback ? [fallback] : [])
        return
      }

      const localDue = getVocabulary().filter(isReviewDue)
      setVocabList(localDue)
      setIsFinished(localDue.length === 0)
      if (localDue.length > 0) {
        Taro.showToast({ title: '已使用本地生词', icon: 'none' })
      }
    } finally {
      setLoading(false)
    }
  }, [inspectVocabId, loadInspectEntry, mode])

  useEffect(() => {
    loadEntries()
  }, [loadEntries])

  const goBack = () => {
    Taro.navigateBack()
  }

  const goToHome = () => {
    Taro.switchTab({ url: ROUTES.HOME })
  }

  const goToOriginal = (ref: SourceRef) => {
    if (!ref.clientRecordId) return
    const record = getRecord(ref.clientRecordId)
    if (!record || record.tombstone) {
      Taro.showToast({ title: '原文记录已删除或不可用', icon: 'none' })
      return
    }
    let url = `${ROUTES.RESULT}?recordId=${ref.clientRecordId}&mode=replay`
    if (ref.sourceSentenceId) url += `&sentenceId=${ref.sourceSentenceId}`
    Taro.navigateTo({ url })
  }

  const handleNext = async (result: 'known' | 'unfamiliar') => {
    if (!currentVocab || mode !== 'review') return

    const localPatch = buildLocalReviewPatch(currentVocab, result)
    updateVocabEntry(currentVocab.id, localPatch)

    setStats(prev => ({
      ...prev,
      [result === 'known' ? 'remembered' : 'unfamiliar']: prev[result === 'known' ? 'remembered' : 'unfamiliar'] + 1,
    }))

    if (result === 'unfamiliar') {
      setReinforceList(prev => [...prev, { ...currentVocab, ...localPatch }])
    }

    if (isCloudVocabId(currentVocab.id)) {
      submitVocabReview(currentVocab.id, result)
        .then((updated) => {
          updateVocabEntry(currentVocab.id, {
            mastered: updated.masteryStatus === 'mastered',
            masteryStatus: updated.masteryStatus,
            reviewStage: updated.stage,
            reviewCount: updated.reviewCount,
            nextReviewAt: updated.nextReviewAt,
            lastReviewedAt: new Date().toISOString(),
          })
        })
        .catch(() => {
          Taro.showToast({ title: '复习结果稍后同步', icon: 'none' })
        })
    }

    if (currentIndex < vocabList.length - 1) {
      setCurrentIndex(prev => prev + 1)
    } else {
      setIsFinished(true)
    }
  }

  if (loading) {
    return (
      <View className='vocab-review-page'>
        <NavBar title={mode === 'inspect' ? '生词笔记' : '今日复习'} />
        <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />
        <View className='loading-state'><Text>加载中...</Text></View>
      </View>
    )
  }

  if (mode === 'review' && isFinished) {
    return (
      <View className='vocab-review-page finished-state'>
        <NavBar title='复习完成' />
        <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />
        <ScrollView scrollY className='finished-scroll'>
          <View className='finished-card'>
            <Text className='finished-subtitle'>今天复习完成</Text>
            <View className='finished-count-row'>
              <Text className='finished-number'>{vocabList.length}</Text>
              <Text className='finished-unit'>个</Text>
              <View className='finished-icon'>
                <LucideIcon name='feather' size={48} color='var(--color-ink)' />
              </View>
            </View>
            <View className='finished-divider' />
            <Text className='finished-time'>用时 {Math.max(1, Math.ceil(vocabList.length * 0.5))} 分钟</Text>

            <View className='stats-grid'>
              <View className='stat-item'>
                <Text className='stat-label'>记得清楚</Text>
                <Text className='stat-val success'>{stats.remembered}<Text className='unit'>个</Text></Text>
              </View>
              <View className='stat-item'>
                <Text className='stat-label'>还需巩固</Text>
                <Text className='stat-val warning'>{stats.unfamiliar}<Text className='unit'>个</Text></Text>
              </View>
              <View className='stat-item'>
                <Text className='stat-label'>明天提醒</Text>
                <Text className='stat-val'>{stats.unfamiliar}<Text className='unit'>个</Text></Text>
              </View>
            </View>
          </View>

          {reinforceList.length > 0 && (
            <View className='next-review-section'>
              <View className='section-header'>
                <Text className='section-title'>下次复习</Text>
                <View className='section-badge'>
                  <Text>明天提醒 {stats.unfamiliar} 个</Text>
                </View>
              </View>
              <View className='next-list'>
                {reinforceList.slice(0, 3).map((v, idx) => (
                  <View key={idx} className='next-item'>
                    <Text className='next-word'>{v.word}</Text>
                    <View className='next-status'>
                      <Text>明天</Text>
                      <LucideIcon name='chevronRight' size={14} color='var(--text-muted)' />
                    </View>
                  </View>
                ))}
              </View>
            </View>
          )}

          <View className='curve-note'>
            <LucideIcon name='info' size={12} color='var(--text-muted)' />
            <Text>已根据你的选择更新记忆曲线</Text>
          </View>
          <View className='actions-stack'>
            <View className='action-btn dark-btn' onClick={goBack}>
              <Text>返回生词本</Text>
            </View>
            <View className='action-btn light-btn' onClick={goToHome}>
              <Text>继续阅读</Text>
            </View>
          </View>
          <View className='bottom-spacer' />
        </ScrollView>
      </View>
    )
  }

  if (!currentVocab) {
    return (
      <View className='vocab-review-page'>
        <NavBar title={mode === 'inspect' ? '生词笔记' : '今日复习'} />
        <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />
        <View className='loading-state'><Text>{mode === 'inspect' ? '未找到这个生词' : '今日无需复习'}</Text></View>
      </View>
    )
  }

  return (
    <View className={`vocab-review-page ${mode === 'inspect' ? 'inspect-mode' : ''}`}>
      <NavBar title={mode === 'inspect' ? '生词笔记' : '今日复习'} />
      <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />

      <View className='review-stage'>
        {mode === 'review' && (
          <View className='progress-header'>
            <Text className='progress-text'>{currentIndex + 1} / {vocabList.length}</Text>
            <View className='progress-bar-bg'>
              <View className='progress-fill' style={{ transform: `scaleX(${(currentIndex + 1) / vocabList.length})` }} />
            </View>
          </View>
        )}

        <VocabStudyCard entry={currentVocab} mode={mode} onGoToOriginal={goToOriginal} />

        {mode === 'review' && nextVocab && (
          <View className='next-preview compact'>
            <Text className='next-label'>接下来</Text>
            <View className='next-card'>
              <View className='next-word-group'>
                <Text className='next-word'>{nextVocab.word}</Text>
                {nextVocab.phonetic && <Text className='next-phonetic'>/{nextVocab.phonetic}/</Text>}
              </View>
              <LucideIcon name='chevronRight' size={18} color='var(--text-muted)' />
            </View>
          </View>
        )}
      </View>

      {mode === 'review' && (
        <View className='action-footer safe-area-bottom'>
          <View className='action-btn light-btn' onClick={() => handleNext('unfamiliar')}>
            <Text className='btn-title'>再巩固</Text>
          </View>
          <View className='action-btn dark-btn' onClick={() => handleNext('known')}>
            <Text className='btn-title'>记得清楚</Text>
          </View>
        </View>
      )}
    </View>
  )
}
