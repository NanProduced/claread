import { useCallback, useMemo, useState } from 'react'
import { View, Text, ScrollView } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'

import { ROUTES } from '../../config/routes'
import { ensureLoggedIn } from '../../services/auth'
import { useAuthStore } from '../../stores/auth'
import {
  type ExcerptAnchorType,
  type ExcerptAssetGroupDto,
  type ExcerptInsightDto,
  listExcerptAssets,
} from '../../services/api/excerpt-assets.client'
import NavBar from '../../components/NavBar'
import LucideIcon from '../../components/LucideIcon'
import { useLayoutStore } from '../../stores/layout'
import './index.scss'

type ExcerptFilter = 'all' | 'favorite' | 'highlight' | 'note' | 'insight'

interface SentenceAsset {
  key: string
  recordId?: string | null
  cloudRecordId?: string | null
  sentenceId?: string
  anchorType?: ExcerptAnchorType
  startOffset?: number
  endOffset?: number
  textHash?: string
  segments?: Array<{
    paragraphId?: string
    sentenceId: string
    selectedText: string
    startOffset: number
    endOffset: number
    textHash: string
  }>
  sourceTitle: string
  sourceSubtitle?: string
  text: string
  translation?: string
  note?: string
  color?: string
  isFavorited: boolean
  isHighlighted: boolean
  isNoted: boolean
  insights: ExcerptInsightDto[]
  updatedAt: string
}

interface ArticleGroup {
  key: string
  title: string
  subtitle?: string
  recordId?: string | null
  cloudRecordId?: string | null
  updatedAt: string
  items: SentenceAsset[]
}

const FILTERS: Array<{ key: ExcerptFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'favorite', label: '收藏' },
  { key: 'highlight', label: '高亮' },
  { key: 'note', label: '笔记' },
  { key: 'insight', label: '解析' },
]

function trimText(text: string | undefined, max = 54): string | undefined {
  if (!text) return undefined
  const normalized = text.replace(/\s+/g, ' ').trim()
  if (!normalized) return undefined
  return normalized.length > max ? `${normalized.slice(0, max)}...` : normalized
}

function trimInsightPreview(text: string | undefined, max = 34): string | undefined {
  return trimText(text, max)
}

function getSentenceOrder(sentenceId?: string): number {
  if (!sentenceId) return Number.MAX_SAFE_INTEGER
  const match = sentenceId.match(/\d+/)
  return match ? Number(match[0]) : Number.MAX_SAFE_INTEGER
}

function compareSentenceOrder(a: SentenceAsset, b: SentenceAsset): number {
  const orderDiff = getSentenceOrder(a.sentenceId) - getSentenceOrder(b.sentenceId)
  if (orderDiff !== 0) return orderDiff
  return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
}

function adaptGroup(group: ExcerptAssetGroupDto): ArticleGroup {
  const recordId = group.client_record_id || group.record_id
  return {
    key: group.client_record_id || group.record_id,
    title: group.title,
    subtitle: group.subtitle || undefined,
    recordId,
    cloudRecordId: group.record_id,
    updatedAt: group.updated_at,
    items: group.items
      .map(item => ({
        key: item.target_key,
        recordId,
        cloudRecordId: group.record_id,
        sentenceId: item.sentence_id || item.segments?.[0]?.sentence_id,
        anchorType: item.anchor_type,
        startOffset: item.start_offset ?? undefined,
        endOffset: item.end_offset ?? undefined,
        textHash: item.text_hash ?? undefined,
        segments: item.segments?.map(segment => ({
          paragraphId: segment.paragraph_id || undefined,
          sentenceId: segment.sentence_id,
          selectedText: segment.selected_text,
          startOffset: segment.start_offset,
          endOffset: segment.end_offset,
          textHash: segment.text_hash,
        })),
        sourceTitle: group.title,
        sourceSubtitle: group.subtitle || undefined,
        text: item.selected_text,
        translation: item.translation || undefined,
        note: item.note || undefined,
        color: item.annotation_color || undefined,
        isFavorited: item.is_favorited,
        isHighlighted: item.is_highlighted,
        isNoted: item.is_noted,
        insights: item.insights || [],
        updatedAt: item.updated_at,
      }))
      .filter(item => item.text)
      .sort(compareSentenceOrder),
  }
}

function matchesFilter(item: SentenceAsset, filter: ExcerptFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'favorite') return item.isFavorited
  if (filter === 'highlight') return item.isHighlighted
  if (filter === 'note') return item.isNoted
  return item.insights.length > 0
}

export default function ExcerptsPage() {
  const { navBarHeight } = useLayoutStore()
  const isLoggedIn = useAuthStore(state => state.isLoggedIn)
  const [groups, setGroups] = useState<ArticleGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [activeFilter, setActiveFilter] = useState<ExcerptFilter>('all')

  const loadExcerpts = useCallback(async () => {
    setLoading(true)
    const result = isLoggedIn ? { success: true } : await ensureLoggedIn()
    if (!result.success) {
      setLoading(false)
      return
    }
    try {
      const response = await listExcerptAssets({ page: 1, limit: 100 })
      setGroups(response.groups.map(adaptGroup))
    } catch (err) {
      console.warn('Failed to load excerpts', err)
      Taro.showToast({ title: '摘录加载失败', icon: 'none' })
    } finally {
      setLoading(false)
    }
  }, [isLoggedIn])

  useDidShow(loadExcerpts)

  const allAssets = useMemo(() => groups.flatMap(group => group.items), [groups])
  const filteredGroups = useMemo(() => (
    groups
      .map(group => ({
        ...group,
        items: group.items.filter(item => matchesFilter(item, activeFilter)),
      }))
      .filter(group => group.items.length > 0)
  ), [groups, activeFilter])
  const isInsightMode = activeFilter === 'insight'

  const goToRecord = (item: SentenceAsset) => {
    const targetId = item.recordId || item.cloudRecordId
    if (!targetId) {
      Taro.showToast({ title: '来源记录不可用', icon: 'none' })
      return
    }
    const sentenceQuery = item.sentenceId ? `&sentenceId=${encodeURIComponent(item.sentenceId)}` : ''
    const targetKeyQuery = item.key ? `&targetKey=${encodeURIComponent(item.key)}` : ''
    const rangeQuery = item.anchorType === 'text_range' && typeof item.startOffset === 'number' && typeof item.endOffset === 'number'
      ? `&anchorType=text_range&startOffset=${encodeURIComponent(String(item.startOffset))}&endOffset=${encodeURIComponent(String(item.endOffset))}${item.textHash ? `&textHash=${encodeURIComponent(item.textHash)}` : ''}`
      : item.anchorType === 'multi_text'
        ? '&anchorType=multi_text'
        : ''
    Taro.navigateTo({ url: `${ROUTES.RESULT}?recordId=${encodeURIComponent(targetId)}&mode=replay${sentenceQuery}${targetKeyQuery}${rangeQuery}` })
  }

  return (
    <View className='excerpts-page'>
      <NavBar title='我的摘录' showBack />
      <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />
      <ScrollView scrollY className='excerpts-scroll'>
        <View className='excerpts-header'>
          <Text className='excerpts-title'>学习摘录</Text>
          <View className='excerpts-filter-row'>
            {FILTERS.map(filter => (
              <View
                key={filter.key}
                className={`excerpts-filter ${activeFilter === filter.key ? 'is-active' : ''}`}
                onClick={() => setActiveFilter(filter.key)}
              >
                <Text>{filter.label}</Text>
              </View>
            ))}
          </View>
        </View>

        {loading ? (
          <View className='excerpts-empty'>
            <Text>同步中...</Text>
          </View>
        ) : allAssets.length === 0 ? (
          <View className='excerpts-empty'>
            <LucideIcon name='bookOpen' size={54} color='var(--text-muted)' />
            <Text className='empty-title'>还没有摘录</Text>
            <Text className='empty-copy'>在解析页长按句子，可以收藏、高亮或写笔记。</Text>
          </View>
        ) : filteredGroups.length === 0 ? (
          <View className='excerpts-empty excerpts-empty--compact'>
            <Text className='empty-title'>这一类还没有内容</Text>
            <Text className='empty-copy'>切换到其他类型继续复习。</Text>
          </View>
        ) : (
          <View className='article-group-list'>
            {filteredGroups.map(group => (
              <View key={group.key} className='article-group'>
                <View className='article-group-head'>
                  <View className='article-title-block'>
                    <Text className='article-group-title' numberOfLines={2}>{group.title}</Text>
                    {group.subtitle && <Text className='article-group-subtitle' numberOfLines={1}>{group.subtitle}</Text>}
                  </View>
                  <View className='article-group-count'>
                    <Text className='article-count-number'>{group.items.length}</Text>
                    <Text className='article-count-unit'>条</Text>
                  </View>
                </View>
                <View className='sentence-asset-list'>
                  {group.items.map(item => (
                    <View key={item.key} className='sentence-asset' onClick={() => goToRecord(item)}>
                      <View className='asset-status-row'>
                        <View className='asset-chip-group'>
                          {item.isFavorited && (
                            <View className='asset-chip'>
                              <LucideIcon name='bookmark' size={15} color='currentColor' />
                              <Text>收藏</Text>
                            </View>
                          )}
                          {item.isHighlighted && (
                            <View className='asset-chip asset-chip--highlight'>
                              <LucideIcon name='highlighter' size={15} color='currentColor' />
                              <Text>{item.anchorType === 'multi_text' ? '跨句高亮' : item.anchorType === 'text_range' ? '局部高亮' : '高亮'}</Text>
                            </View>
                          )}
                          {item.isNoted && (
                            <View className='asset-chip asset-chip--note'>
                              <LucideIcon name='penLine' size={15} color='currentColor' />
                              <Text>笔记</Text>
                            </View>
                          )}
                        </View>
                        {item.sentenceId && <Text className='asset-sentence-id'>第 {item.sentenceId.replace(/^s/i, '')} 句</Text>}
                      </View>
                      <Text className='asset-text'>{item.text}</Text>
                      {item.translation && <Text className='asset-translation'>{item.translation}</Text>}
                      {item.note && (
                        <View className='asset-note'>
                          <LucideIcon name='penLine' size={15} color='currentColor' />
                          <Text>{item.note}</Text>
                        </View>
                      )}
                      {item.insights.length > 0 && (
                        <View className={`asset-insights ${isInsightMode ? 'asset-insights--full' : ''}`}>
                          {isInsightMode && <Text className='asset-insights-title'>复习要点</Text>}
                          {(isInsightMode ? item.insights : item.insights.slice(0, 2)).map(insight => (
                            <View key={insight.id} className={`asset-insight asset-insight--${insight.type}`}>
                              <Text className='asset-insight-label'>{insight.label}</Text>
                              <View className='asset-insight-copy'>
                                {isInsightMode ? (
                                  <>
                                    <Text className='asset-insight-title'>{insight.title}</Text>
                                    {insight.detail && <Text className='asset-insight-detail'>{insight.detail}</Text>}
                                  </>
                                ) : (
                                  <>
                                    <Text className='asset-insight-title'>{trimInsightPreview(insight.title, 12)}</Text>
                                    {insight.detail && <Text className='asset-insight-excerpt'>{trimInsightPreview(insight.detail || undefined, 38)}</Text>}
                                  </>
                                )}
                              </View>
                            </View>
                          ))}
                          {!isInsightMode && item.insights.length > 2 && (
                            <Text className='asset-insight-more'>+{item.insights.length - 2}</Text>
                          )}
                        </View>
                      )}
                    </View>
                  ))}
                </View>
              </View>
            ))}
          </View>
        )}
        <View className='bottom-spacer' />
      </ScrollView>
    </View>
  )
}
