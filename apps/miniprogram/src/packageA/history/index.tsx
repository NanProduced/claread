import { useEffect, useState, useCallback, useRef } from 'react'
import { View, Text, ScrollView, Image } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import type { StopPropagationEvent } from '../../types/taro-events'
import { getRecordIds, getRecord, deleteRecord, getVocabulary, updateVocabEntry } from '../../services/storage'
import { useAuthStore } from '../../stores/auth'
import { fetchCloudRecords } from '../../services/api/records.client'
import { fetchCloudFavorites } from '../../services/api/favorites.client'
import { CloudSyncService } from '../../services/cloudSync.service'
import type { AnalysisRecord } from '../../types/view/analysis-record.vm'
import { track } from '../../services/analytics'
import NavBar from '../../components/NavBar'
import TabBar from '../../components/TabBar'
import { useLayoutStore } from '../../stores/layout'
import { getSafeDisplayLabel, getCompactLabel } from '../../config/purpose'
import LucideIcon from '../../components/LucideIcon'
import emptyHistoryImg from '../../assets/illustrations/empty-history.jpg'
import './index.scss'
import { formatDate, getUTC8DayValue } from '../../utils/formatDate'

/** 读取显示用的前 50 字 */
function getDisplayTitle(record: AnalysisRecord): string {
  if (record.title && record.title.trim()) {
    return record.title.trim()
  }
  const sourceText = record.sourceText || ''
  const firstLine = sourceText.split('\n')[0]
  return firstLine.length > 50 ? `${firstLine.slice(0, 50)}...` : firstLine
}

type FilterTab = 'all' | 'favorites'

interface HistoryPageProps {
  isSubView?: boolean
}

export default function HistoryPage({ isSubView = false }: HistoryPageProps) {
  const [records, setRecords] = useState<AnalysisRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<FilterTab>('all')
  const [isEditMode, setIsEditMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const { navBarHeight } = useLayoutStore()

  // --------------------------------------------------------------------------
  // 数据与状态管理
  // --------------------------------------------------------------------------

  const filteredRecords = activeTab === 'favorites'
    ? records.filter((r) => r.isFavorited)
    : records

  const loadRecords = useCallback(async () => {
    setLoading(true)
    const { isLoggedIn } = useAuthStore.getState()
    if (isLoggedIn) {
      try {
        const [recordResult, favResult] = await Promise.all([
          fetchCloudRecords(),
          fetchCloudFavorites(),
        ])
        const favIds = new Set(favResult.items.map((f) => f.recordId))
        const allVocab = getVocabulary()
        const vocabCounts: Record<string, number> = {}
        allVocab.forEach(v => {
          v.sourceRefs?.forEach(r => {
            if (r.clientRecordId) vocabCounts[r.clientRecordId] = (vocabCounts[r.clientRecordId] || 0) + 1
          })
        })
        const cloudRecords = recordResult.items.map((r) => ({
          ...r,
          isFavorited: favIds.has(r.recordId),
          vocabCount: vocabCounts[r.recordId] || 0,
        }))
        const localIds = getRecordIds()
        const localOnlyRecords: AnalysisRecord[] = []
        const cloudRecordIds = new Set(cloudRecords.map(r => r.recordId))
        for (const id of localIds) {
          if (cloudRecordIds.has(id)) continue
          const record = getRecord(id)
          if (record && !record.tombstone) {
            localOnlyRecords.push({ ...record, vocabCount: vocabCounts[id] || 0 })
          }
        }
        setRecords([...cloudRecords, ...localOnlyRecords])
        setLoading(false)
        return
      } catch (e) { console.error("index.tsx:", e) }
    }
    const allVocab = getVocabulary()
    const vocabCounts: Record<string, number> = {}
    allVocab.forEach(v => {
      v.sourceRefs?.forEach(r => {
        if (r.clientRecordId) vocabCounts[r.clientRecordId] = (vocabCounts[r.clientRecordId] || 0) + 1
      })
    })
    const ids = getRecordIds()
    const loaded: AnalysisRecord[] = []
    for (const id of ids) {
      const record = getRecord(id)
      if (record && !record.tombstone) {
        loaded.push({ ...record, vocabCount: vocabCounts[id] || 0 })
      }
    }
    setRecords(loaded)
    setLoading(false)
  }, [])

  useEffect(() => { loadRecords() }, [loadRecords])
  useDidShow(loadRecords)

  // --------------------------------------------------------------------------
  // 交互逻辑
  // --------------------------------------------------------------------------

  const toggleEditMode = () => {
    setIsEditMode(!isEditMode)
    setSelectedIds(new Set())
  }

  const toggleSelect = (id: string, e: { stopPropagation: () => void }) => {
    e.stopPropagation()
    const next = new Set(selectedIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelectedIds(next)
  }

  const handleCardClick = (record: AnalysisRecord) => {
    if (isEditMode) {
      toggleSelect(record.recordId, { stopPropagation: () => {} })
      return
    }
    goToResult(record.recordId)
  }

  const handleLongPress = (id: string) => {
    if (isEditMode) return
    setIsEditMode(true)
    setSelectedIds(new Set([id]))
    if (Taro.vibrateShort) Taro.vibrateShort({ type: 'medium' })
  }

  const cleanupVocabRefs = (recordId: string) => {
    const allVocab = getVocabulary()
    for (const v of allVocab) {
      if (!v.sourceRefs || v.sourceRefs.length === 0) continue
      const hasRef = v.sourceRefs.some(r => r.clientRecordId === recordId)
      if (hasRef) {
        const filtered = v.sourceRefs.filter(r => r.clientRecordId !== recordId)
        updateVocabEntry(v.id, { sourceRefs: filtered })
      }
    }
  }

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return
    Taro.showModal({
      title: '批量删除',
      content: `确定要删除这 ${selectedIds.size} 条记录吗？`,
      confirmText: '全部删除',
      confirmColor: '#ef4444',
      success: (res) => {
        if (res.confirm) {
          selectedIds.forEach(id => {
            deleteRecord(id)
            cleanupVocabRefs(id)
            const record = records.find(r => r.recordId === id)
            if (record?.cloudId) {
              CloudSyncService.syncDeleteRecord(record.cloudId, id)
            }
          })
          setRecords(prev => prev.filter(r => !selectedIds.has(r.recordId)))
          setIsEditMode(false)
          setSelectedIds(new Set())
        }
      }
    })
  }

  // --------------------------------------------------------------------------
  // 渲染助手
  // --------------------------------------------------------------------------

  const groupedRecords = (() => {
    const todayDayValue = getUTC8DayValue(Date.now())
    const yesterdayDayValue = todayDayValue - 24 * 60 * 60 * 1000
    const sevenDaysAgoDayValue = todayDayValue - 7 * 24 * 60 * 60 * 1000

    const groups: { label: string; items: AnalysisRecord[] }[] = [
      { label: '今天', items: [] },
      { label: '昨天', items: [] },
      { label: '最近七天', items: [] },
      { label: '更早以前', items: [] },
    ]

    filteredRecords.forEach(r => {
      const dayValue = getUTC8DayValue(r.createdAt)
      if (dayValue >= todayDayValue) groups[0].items.push(r)
      else if (dayValue >= yesterdayDayValue) groups[1].items.push(r)
      else if (dayValue >= sevenDaysAgoDayValue) groups[2].items.push(r)
      else groups[3].items.push(r)
    })

    return groups.filter(g => g.items.length > 0)
  })()

  const handleDelete = (record: AnalysisRecord, e: StopPropagationEvent) => {
    e.stopPropagation()
    Taro.showModal({
      title: '删除记录',
      content: '确定要删除这条解读记录吗？',
      confirmText: '删除',
      confirmColor: '#ef4444',
      success: (res) => {
        if (res.confirm) {
          deleteRecord(record.recordId)
          cleanupVocabRefs(record.recordId)
          if (record.cloudId) {
            CloudSyncService.syncDeleteRecord(record.cloudId, record.recordId)
          }
          setRecords(prev => prev.filter(r => r.recordId !== record.recordId))
        }
      },
    })
  }

  const goToResult = (recordId: string) => {
    Taro.navigateTo({ url: `${ROUTES.RESULT}?recordId=${recordId}&mode=replay` })
  }

  const goToInput = () => {
    Taro.navigateTo({ url: ROUTES.INPUT })
  }

  return (
    <View className={`history-page ${isSubView ? 'sub-view' : ''} ${isEditMode ? 'is-edit-mode' : ''}`}>
      {!isSubView && <NavBar title='Claread透读' />}
      {!isSubView && <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />}

      <View className='filter-tabs'>
        <View className='tabs-main'>
          <View
            className={`filter-tab ${activeTab === 'all' ? 'active' : ''}`}
            onClick={() => setActiveTab('all')}
          >
            <Text className='filter-tab-label'>全部</Text>
          </View>
          <View
            className={`filter-tab ${activeTab === 'favorites' ? 'active' : ''}`}
            onClick={() => setActiveTab('favorites')}
          >
            <Text className='filter-tab-label'>已收藏</Text>
          </View>
        </View>

        {records.length > 0 && (
          <View className={`manage-btn ${isEditMode ? 'active' : ''}`} onClick={toggleEditMode}>
            <Text>{isEditMode ? '取消选择' : '批量管理'}</Text>
          </View>
        )}
      </View>

      <ScrollView
        scrollY
        className='list-area'
      >
        {loading && records.length === 0 ? null : groupedRecords.length === 0 ? (
            <View className='empty-state'>
              <Image className='empty-illustration' src={emptyHistoryImg} mode='aspectFit' />
              <Text className='empty-text'>
                {activeTab === 'favorites' ? '暂无收藏记录' : '暂无解读记录'}
              </Text>
              {activeTab === 'all' && (
                <View className='empty-action' onClick={goToInput}>
                  <Text className='empty-sub'>去粘贴一篇文章试试吧 →</Text>
                </View>
              )}
            </View>
          ) : (
            groupedRecords.map((group) => (
              <View key={group.label} className='history-section'>
                <View className='section-header'>
                  <View className='section-dot' />
                  <Text className='section-title'>{group.label}</Text>
                  <View className='section-line' />
                </View>

                {group.items.map((record, index) => (
                  <View
                    key={record.recordId}
                    className={`history-card ${selectedIds.has(record.recordId) ? 'is-selected' : ''} ${isEditMode ? 'is-edit-mode-card' : ''}`}
                    style={{
                      animation: `slideInUp 0.6s var(--ease-spring) both`,
                      animationDelay: `${index * 0.05}s`
                    }}
                    onClick={() => handleCardClick(record)}
                    onLongPress={() => handleLongPress(record.recordId)}
                  >
                    {isEditMode && (
                      <View className='card-checkbox' onClick={(e) => toggleSelect(record.recordId, e)}>
                        <View className={`checkbox-inner ${selectedIds.has(record.recordId) ? 'checked' : ''}`} />
                      </View>
                    )}

                    <View className='card-content'>
                      <View className='card-header'>
                        <Text className='item-title'>{getDisplayTitle(record)}</Text>
                        {!isEditMode && (
                          <View className='delete-btn' onClick={(e) => handleDelete(record, e)}>
                            <LucideIcon name='trash2' size={16} color='var(--text-muted)' />
                          </View>
                        )}
                      </View>
                      <View className='card-footer'>
                        <View className='tag-row'>
                          {record.isFavorited && (
                            <View className='fav-tag'>
                              <LucideIcon name='star' size={10} color='var(--color-warn)' />
                              <Text>已收藏</Text>
                            </View>
                          )}
                          {record.vocabCount && record.vocabCount > 0 ? (
                            <View className='vocab-tag-count'>
                              <LucideIcon name='book' size={10} color='var(--color-grammar)' />
                              <Text>{record.vocabCount} 生词</Text>
                            </View>
                          ) : null}
                          {record.pageState === 'loading' && (
                            <View className='processing-tag'>
                              <LucideIcon name='clock' size={10} color='var(--color-info)' />
                              <Text>处理中</Text>
                            </View>
                          )}
                          {(record.pageState === 'failed' || record.pageState === 'timeout' || record.pageState === 'network_fail') && (
                            <View className='failed-tag'>
                              <LucideIcon name='alertCircle' size={10} color='var(--color-exam)' />
                              <Text>解析失败</Text>
                            </View>
                          )}
                          <View className='config-tag'>
                            <Text>{getCompactLabel(record.requestPayload.reading_goal, record.requestPayload.reading_variant)}</Text>
                          </View>
                        </View>
                        <Text className='date-text'>{formatDate(record.createdAt)}</Text>
                      </View>
                    </View>
                  </View>
                ))}
              </View>
            ))
          )}
        <View className={`bottom-spacer ${isEditMode ? 'bottom-spacer--edit' : ''}`} />
      </ScrollView>

      {isEditMode && selectedIds.size > 0 && (
        <View className='batch-action-bar'>
          <View className='action-info'>
            <Text className='selected-count'>已选择 {selectedIds.size} 项</Text>
          </View>
          <View className='action-btns'>
            <View className={`action-btn delete ${selectedIds.size === 0 ? 'disabled' : ''}`} onClick={handleBatchDelete}>
              <LucideIcon name='trash2' size={18} color={selectedIds.size === 0 ? 'var(--text-muted)' : 'var(--color-exam)'} />
              <Text>批量删除</Text>
            </View>
          </View>
        </View>
      )}

      {!isSubView && <TabBar current='history' />}
    </View>
  )
}
