/**
 * 生词本页面
 *
 * 展示用户收藏的单词列表，支持搜索、筛选和云端同步。
 * 点击单词进入生词笔记视图。
 */

import { View, Text, ScrollView, Input, Image } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import type { InputEvent, StopPropagationEvent } from '../../types/taro-events'
import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { useAuthStore } from '../../stores/auth'
import { getVocabulary, removeVocabEntry, saveVocabInspectEntry } from '../../services/storage'
import { CloudSyncService } from '../../services/cloudSync.service'
import { fetchCloudVocabulary } from '../../services/api/vocabulary.client'
import type { VocabEntry } from '../../types/view/vocabulary.vm'
import { track } from '../../services/analytics'
import NavBar from '../../components/NavBar'
import TabBar from '../../components/TabBar'
import LucideIcon from '../../components/LucideIcon'
import { useLayoutStore } from '../../stores/layout'
import emptyVocabImg from '../../assets/illustrations/empty-vocab.jpg'
import './index.scss'

interface VocabPageProps {
  isSubView?: boolean
}

type SortMode = 'time' | 'alpha'
type FilterStatus = 'all' | 'due'

const FILTER_OPTIONS: { value: FilterStatus; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'due', label: '待复习' },
]

import { formatDate } from '../../utils/formatDate'

function mergeVocabCloudWithLocal(cloudItems: VocabEntry[], localItems: VocabEntry[]): VocabEntry[] {
  const localByLemma = new Map<string, VocabEntry>()
  for (const item of localItems) {
    const key = (item.lemma || item.word).toLowerCase()
    localByLemma.set(key, item)
  }

  const result: VocabEntry[] = []
  const seenLemmas = new Set<string>()

  for (const cloud of cloudItems) {
    const key = (cloud.lemma || cloud.word).toLowerCase()
    seenLemmas.add(key)
    const local = localByLemma.get(key)

    if (!local) {
      result.push(cloud)
      continue
    }

    if (local.tombstone) continue
    if (local.pendingOp === 'delete') continue
    if (local.pendingOp === 'create' || local.pendingOp === 'update') {
      result.push(local)
      continue
    }
    if (local.syncState === 'local_only') {
      result.push(local)
      continue
    }

    const localRefsCount = local.sourceRefs?.length || 0
    const cloudRefsCount = cloud.sourceRefs?.length || 0
    const mergedSourceRefs = localRefsCount >= cloudRefsCount ? local.sourceRefs : cloud.sourceRefs
    const mergedCollectedForms = (local.collectedForms?.length || 0) >= (cloud.collectedForms?.length || 0) ? local.collectedForms : cloud.collectedForms
    result.push({
      ...cloud,
      mastered: cloud.mastered,
      masteryStatus: cloud.masteryStatus,
      reviewStage: cloud.reviewStage,
      nextReviewAt: cloud.nextReviewAt,
      reviewCount: cloud.reviewCount,
      lastReviewedAt: cloud.lastReviewedAt,
      sourceRefs: mergedSourceRefs,
      collectedForms: mergedCollectedForms,
      sentence: local.sentence || cloud.sentence,
      context: local.context || cloud.context,
    })
  }

  for (const local of localItems) {
    if (local.tombstone) continue
    const key = (local.lemma || local.word).toLowerCase()
    if (seenLemmas.has(key)) continue
    if (local.pendingOp === 'delete') continue
    result.push(local)
  }

  return result
}

function isDue(entry: VocabEntry): boolean {
  if (entry.mastered || entry.masteryStatus === 'mastered') return false
  if (!entry.nextReviewAt) return true
  return new Date(entry.nextReviewAt).getTime() <= Date.now()
}

function getReviewStatusText(entry: VocabEntry): string {
  if (entry.mastered || entry.masteryStatus === 'mastered') return '已掌握'
  if (!entry.nextReviewAt) return '待复习'
  const next = new Date(entry.nextReviewAt).getTime()
  const now = Date.now()
  if (next <= now) return '今天'
  const diffDays = Math.ceil((next - now) / (24 * 60 * 60 * 1000))
  return `${diffDays}天后`
}

function getSourceArticleCount(entry: VocabEntry): number {
  const refs = entry.sourceRefs || []
  const sourceIds = new Set(
    refs
      .map(ref => ref.cloudRecordId || ref.clientRecordId)
      .filter(Boolean)
  )
  return sourceIds.size
}

export default function VocabPage({ isSubView = false }: VocabPageProps) {
  const [vocabList, setVocabList] = useState<VocabEntry[]>([])
  const [loading, setLoading] = useState(true)
  const { navBarHeight } = useLayoutStore()
  const loadVocabRef = useRef<() => Promise<void>>()

  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [filterStatus, setFilterStatus] = useState<FilterStatus>('all')
  const [sortMode, setSortMode] = useState<SortMode>('time')
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const dueCount = useMemo(() => vocabList.filter(isDue).length, [vocabList])

  const loadVocab = useCallback(async () => {
    setLoading(true)
    const { isLoggedIn } = useAuthStore.getState()

    if (isLoggedIn) {
      try {
        let allCloudItems: VocabEntry[] = []
        let page = 1
        const pageSize = 100
        let hasMore = true
        while (hasMore) {
          const result = await fetchCloudVocabulary(page, pageSize)
          allCloudItems = allCloudItems.concat(result.items)
          hasMore = allCloudItems.length < result.total
          if (result.items.length === 0) break
          page++
        }
        const localItems = getVocabulary()
        const merged = mergeVocabCloudWithLocal(allCloudItems, localItems)
        setVocabList(merged)
        track('view_vocab', { count: merged.length, source: 'cloud_merged' })
        setLoading(false)
        return
      } catch {
        // fallback to local
      }
    }

    const local = getVocabulary()
    setVocabList(local)
    track('view_vocab', { count: local.length, source: 'local' })
    setLoading(false)
  }, [])

  useEffect(() => {
    loadVocabRef.current = loadVocab
  }, [loadVocab])

  useEffect(() => {
    loadVocab()
  }, [loadVocab])

  useDidShow(loadVocab)

  Taro.usePullDownRefresh(() => {
    if (isSubView) return
    loadVocabRef.current?.()
    Taro.stopPullDownRefresh()
  })

  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    searchTimerRef.current = setTimeout(() => {
      setDebouncedQuery(searchQuery)
    }, 300)
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    }
  }, [searchQuery])

  const filteredList = useMemo(() => {
    let list = vocabList

    if (debouncedQuery) {
      const q = debouncedQuery.toLowerCase()
      list = list.filter((v) => {
        if (v.lemma?.toLowerCase().startsWith(q)) return true
        if (v.word.toLowerCase().startsWith(q)) return true
        if (v.collectedForms?.some(f => f.toLowerCase().startsWith(q))) return true
        if (v.meaning?.toLowerCase().includes(q)) return true
        return false
      })
    }

    if (filterStatus === 'due') {
      list = list.filter((v) => isDue(v))
    }

    if (sortMode === 'alpha') {
      list = [...list].sort((a, b) => (a.lemma || a.word).localeCompare(b.lemma || b.word))
    }

    return list
  }, [vocabList, debouncedQuery, filterStatus, sortMode])

  const goToResult = (recordId: string, sentenceId?: string, e?: StopPropagationEvent) => {
    if (e) e.stopPropagation()
    if (!recordId) return
    let url = `${ROUTES.RESULT}?recordId=${recordId}&mode=replay`
    if (sentenceId) url += `&sentenceId=${sentenceId}`
    Taro.navigateTo({ url })
  }

  const handleDelete = (entry: VocabEntry, e: StopPropagationEvent) => {
    e.stopPropagation()
    Taro.showModal({
      title: '删除生词',
      content: `确定要删除「${entry.word}」吗？`,
      confirmText: '删除',
      confirmColor: '#ef4444',
      cancelText: '取消',
      success: (res) => {
        if (res.confirm) {
          removeVocabEntry(entry.id)
          CloudSyncService.syncDeleteVocab(entry.id, entry.lemma || entry.word)
          setVocabList((prev) => prev.filter((v) => v.id !== entry.id))
        }
      },
    })
  }

  const goToInput = () => {
    Taro.navigateTo({ url: ROUTES.INPUT })
  }

  const openVocabInspect = (entry: VocabEntry) => {
    saveVocabInspectEntry(entry)
    Taro.navigateTo({
      url: `${ROUTES.VOCAB_REVIEW}?mode=inspect&vocabId=${encodeURIComponent(entry.id)}`,
    })
  }

  const handleSearchInput = (e: InputEvent) => {
    setSearchQuery(e.detail.value || '')
  }

  const clearSearch = () => {
    setSearchQuery('')
    setDebouncedQuery('')
  }

  return (
    <View className={`vocab-page ${isSubView ? 'sub-view' : ''}`}>
      {!isSubView && <NavBar title='生词本' />}
      {!isSubView && <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />}

      <View className='search-bar'>
        <View className='search-input-wrap'>
          <LucideIcon name='search' size={16} color='var(--text-muted)' />
          <Input
            className='search-input'
            type='text'
            placeholder='搜索单词或释义'
            placeholderClass='search-placeholder'
            value={searchQuery}
            onInput={handleSearchInput}
            confirmType='search'
          />
          {searchQuery && (
            <View className='search-clear' onClick={clearSearch}>
              <LucideIcon name='x' size={14} color='var(--text-muted)' />
            </View>
          )}
        </View>
      </View>

      <View className='vocab-control-strip'>
        <View className='status-segment'>
          {FILTER_OPTIONS.map(opt => (
            <View
              key={opt.value}
              className={`status-option ${filterStatus === opt.value ? 'active' : ''}`}
              onClick={() => setFilterStatus(opt.value)}
            >
              <Text>{opt.label}</Text>
              {opt.value === 'due' && dueCount > 0 && <Text className='option-count'>{dueCount}</Text>}
            </View>
          ))}
        </View>
        <View className='sort-pill' onClick={() => setSortMode(sortMode === 'time' ? 'alpha' : 'time')}>
          <Text>{sortMode === 'time' ? '最近收藏' : '按字母'}</Text>
          <LucideIcon name='chevronDown' size={14} color='var(--text-muted)' />
        </View>
      </View>

      {dueCount > 0 && !debouncedQuery && filterStatus === 'all' && (
        <View className='due-review-banner'>
          <View className='banner-copy'>
            <View className='banner-title-row'>
              <LucideIcon name='calendar' size={16} color='var(--text-main)' />
              <Text className='banner-title'>今日待复习</Text>
            </View>
            <Text className='banner-desc'>{dueCount} 个词，预计 {Math.max(1, Math.ceil(dueCount * 0.5))} 分钟</Text>
          </View>
          <View className='start-btn' onClick={() => Taro.navigateTo({ url: ROUTES.VOCAB_REVIEW })}>
            <Text>开始复习</Text>
          </View>
        </View>
      )}

      {debouncedQuery && (
        <View className='search-result-hint'>
          <Text className='search-result-text'>
            {filteredList.length > 0
              ? `找到 ${filteredList.length} 个结果`
              : '未找到匹配的生词'}
          </Text>
        </View>
      )}

      <ScrollView scrollY className='list-area'>
        {loading && vocabList.length === 0 ? (
          <View className='loading-state'>
            <Text className='loading-text'>加载中...</Text>
          </View>
        ) : filteredList.length === 0 ? (
          <View className='empty-state'>
            <Image className='empty-illustration' src={emptyVocabImg} mode='aspectFit' />
            <Text className='empty-text'>{debouncedQuery ? '未找到匹配的生词' : (filterStatus === 'due' ? '今日无需复习' : '暂无生词')}</Text>
            {!debouncedQuery && filterStatus === 'all' && (
              <View className='empty-action' onClick={goToInput}>
                <Text className='empty-sub'>去读一篇文章，记下不认识的词吧 →</Text>
              </View>
            )}
          </View>
        ) : (
          filteredList.map((entry, index) => {
            const contextCount = entry.sourceRefs?.length || 0
            const sourceArticleCount = getSourceArticleCount(entry)
            const primaryRef = entry.sourceRefs?.[0]

            return (
              <View
                key={entry.id}
                className='vocab-card'
                style={{
                  animation: `vocabFadeIn 180ms ease-out both`,
                  animationDelay: `${Math.min(index, 6) * 0.02}s`
                }}
                onClick={() => openVocabInspect(entry)}
              >
                <View className='card-header'>
                  <View className='word-group'>
                    <Text className='word-text'>{entry.word}</Text>
                    {entry.phonetic && (
                      <Text className='phonetic-text'>/{entry.phonetic}/</Text>
                    )}
                  </View>
                  <View className='card-header-right'>
                    {sourceArticleCount > 1 && (
                      <Text className='source-count-badge'>{sourceArticleCount} 篇</Text>
                    )}
                    <Text className={`review-status-tag ${entry.mastered || entry.masteryStatus === 'mastered' ? 'mastered' : 'pending'}`}>
                      {getReviewStatusText(entry)}
                    </Text>
                    <View className='delete-btn' onClick={(e) => handleDelete(entry, e)}>
                      <LucideIcon name='trash2' size={16} color='var(--text-muted)' />
                    </View>
                  </View>
                </View>
                <View className='card-body'>
                  <View className='meaning-row'>
                    {entry.partOfSpeech && (
                      <Text className='pos-tag'>{entry.partOfSpeech}</Text>
                    )}
                    <Text className='meaning-text'>{entry.meaning}</Text>
                  </View>
                  {(primaryRef?.sourceSentence || entry.sentence) && (
                    <View className='context-box'>
                      <Text className='context-text'>"{primaryRef?.sourceSentence || entry.sentence}"</Text>
                      {contextCount > 1 && (
                        <Text className='more-context'>还有 {contextCount - 1} 个语境</Text>
                      )}
                    </View>
                  )}
                </View>
                <View className='card-footer'>
                  <Text className='date-text'>收藏于 {formatDate(entry.addedAt)}</Text>
                  {primaryRef?.clientRecordId && (
                    <View className='source-link' onClick={(e) => goToResult(primaryRef.clientRecordId, primaryRef.sourceSentenceId, e)}>
                      <Text>查看原文</Text>
                      <LucideIcon name='chevronRight' size={14} color='currentColor' />
                    </View>
                  )}
                </View>
              </View>
            )
          })
        )}
        <View className='bottom-spacer' />
      </ScrollView>

      {!isSubView && <TabBar current='profile' />}
    </View>
  )
}
