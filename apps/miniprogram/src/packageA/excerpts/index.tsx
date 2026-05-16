import { useCallback, useMemo, useState } from 'react'
import { View, Text, ScrollView } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import { ensureLoggedIn } from '../../services/auth'
import { useAuthStore } from '../../stores/auth'
import { fetchCloudFavoriteItems, FavoriteItemDto } from '../../services/api/favorites.client'
import { listUserAnnotations, UserAnnotationDto } from '../../services/api/user-annotations.client'
import { fetchCloudRecord, fetchCloudRecordByClientId, fetchCloudRecords } from '../../services/api/records.client'
import type { AnalysisRecord } from '../../types/view/analysis-record.vm'
import type { AnySentenceEntryModel } from '../../types/view/render-scene.vm'
import NavBar from '../../components/NavBar'
import LucideIcon from '../../components/LucideIcon'
import { useLayoutStore } from '../../stores/layout'
import './index.scss'

type ExcerptFilter = 'all' | 'favorite' | 'highlight' | 'note' | 'insight'

interface ExcerptInsight {
  id: string
  type: 'grammar' | 'sentence' | 'term' | 'logic' | 'interpretation' | 'summary'
  label: string
  title: string
  detail?: string
}

interface SentenceAsset {
  key: string
  recordId?: string | null
  cloudRecordId?: string | null
  sentenceId?: string
  anchorType?: 'sentence' | 'text_range' | 'multi_text'
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
  sourceRank?: number
  text: string
  translation?: string
  note?: string
  color?: string
  isFavorited: boolean
  isHighlighted: boolean
  insights: ExcerptInsight[]
  updatedAt: string
}

interface ArticleGroup {
  key: string
  title: string
  subtitle?: string
  sourceRank?: number
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

function getPayloadString(payload: Record<string, unknown> | undefined, key: string): string | undefined {
  if (!payload) return undefined
  const value = payload[key]
  return typeof value === 'string' && value.trim() ? value : undefined
}

function getPayloadNumber(payload: Record<string, unknown> | undefined, key: string): number | undefined {
  if (!payload) return undefined
  const value = payload[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function getPayloadSegments(payload: Record<string, unknown> | undefined): SentenceAsset['segments'] {
  const raw = payload?.segments
  if (!Array.isArray(raw)) return undefined
  const items = raw
    .map(item => {
      if (!item || typeof item !== 'object') return null
      const segment = item as Record<string, unknown>
      const sentenceId = typeof segment.sentence_id === 'string' ? segment.sentence_id : ''
      const selectedText = typeof segment.selected_text === 'string' ? segment.selected_text : ''
      const startOffset = typeof segment.start_offset === 'number' ? segment.start_offset : NaN
      const endOffset = typeof segment.end_offset === 'number' ? segment.end_offset : NaN
      const textHash = typeof segment.text_hash === 'string' ? segment.text_hash : ''
      if (!sentenceId || !selectedText || !Number.isFinite(startOffset) || !Number.isFinite(endOffset) || !textHash) return null
      return {
        paragraphId: typeof segment.paragraph_id === 'string' ? segment.paragraph_id : undefined,
        sentenceId,
        selectedText,
        startOffset,
        endOffset,
        textHash,
      }
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item))
  return items.length > 0 ? items : undefined
}

function trimText(text: string | undefined, max = 54): string | undefined {
  if (!text) return undefined
  const normalized = text.replace(/\s+/g, ' ').trim()
  if (!normalized) return undefined
  return normalized.length > max ? `${normalized.slice(0, max)}...` : normalized
}

function trimInsightPreview(text: string | undefined, max = 34): string | undefined {
  return trimText(text, max)
}

function getFavoriteSentenceKey(item: FavoriteItemDto): string {
  return item.target_key || `${item.analysis_record_id || 'record'}:${getPayloadString(item.payload_json, 'sentence_id') || item.id}`
}

function getAnnotationSentenceKey(item: UserAnnotationDto): string {
  if (item.target_key) return item.target_key
  if (
    item.anchor_type === 'text_range' &&
    typeof item.start_offset === 'number' &&
    typeof item.end_offset === 'number'
  ) {
    return `${item.analysis_record_id || 'record'}:range:${item.sentence_id}:${item.start_offset}:${item.end_offset}:${item.text_hash || ''}`
  }
  return item.target_key || `${item.analysis_record_id || 'record'}:${item.sentence_id}`
}

interface RecordLookupItem {
  record: AnalysisRecord
  rank: number
}

function buildRecordLookup(records: AnalysisRecord[]): Map<string, RecordLookupItem> {
  const map = new Map<string, RecordLookupItem>()
  records.forEach((record, rank) => {
    const item = { record, rank }
    if (record.recordId) map.set(record.recordId, item)
    if (record.cloudId) map.set(record.cloudId, item)
  })
  return map
}

function getRecordDisplayTitle(record?: AnalysisRecord | null): string | undefined {
  const recordTitle = trimText(record?.title || undefined, 72)
  if (recordTitle) return recordTitle
  const scene = record?.renderScene
  const academicTitle = scene && scene.schemaVersion === '3.0.0-academic'
    ? trimText(scene.title || undefined, 72)
    : undefined
  if (academicTitle) return academicTitle
  return undefined
}

function getArticleTitle(payload: Record<string, unknown> | undefined, record?: AnalysisRecord | null): { title: string; subtitle?: string } {
  const zhTitle = getPayloadString(payload, 'article_title_zh') || getPayloadString(payload, 'title_zh')
  const recordTitle = getRecordDisplayTitle(record)
  const payloadTitle = getPayloadString(payload, 'article_title')
  const fallback = payloadTitle || recordTitle || '未命名文章'
  const title = zhTitle || recordTitle || fallback
  const subtitle = payloadTitle && payloadTitle !== title ? payloadTitle : undefined
  return { title, subtitle: trimText(subtitle, 86) }
}

function insightMeta(entryType: string): Pick<ExcerptInsight, 'type' | 'label'> | null {
  switch (entryType) {
    case 'grammar_note':
      return { type: 'grammar', label: '语法' }
    case 'sentence_analysis':
      return { type: 'sentence', label: '句析' }
    case 'term_note':
      return { type: 'term', label: '术语' }
    case 'logic_note':
      return { type: 'logic', label: '逻辑' }
    case 'interpretation_note':
      return { type: 'interpretation', label: '解读' }
    case 'content_summary':
      return { type: 'summary', label: '概要' }
    default:
      return null
  }
}

function getInsightDetail(content: string | undefined): string | undefined {
  if (!content) return undefined
  return content
    .replace(/[#>*_`-]/g, '')
    .replace(/\n+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim() || undefined
}

function getPayloadReviewAssets(payload: Record<string, unknown> | undefined): ExcerptInsight[] {
  const raw = payload?.review_assets
  if (!Array.isArray(raw)) return []
  const results: ExcerptInsight[] = []
  for (let idx = 0; idx < raw.length; idx++) {
    const item = raw[idx]
    if (!item || typeof item !== 'object') continue
    const asset = item as Record<string, unknown>
    const type = typeof asset.type === 'string' ? asset.type : ''
    const meta = insightMeta(type)
    if (!meta) continue
    const title = typeof asset.title === 'string' ? trimText(asset.title, 28) : undefined
    const summary = typeof asset.summary === 'string' ? getInsightDetail(asset.summary) : undefined
    results.push({
      id: typeof asset.id === 'string' ? asset.id : `${type}-${idx}`,
      type: meta.type,
      label: meta.label,
      title: title || meta.label,
      detail: summary,
    })
  }
  return results
}

function extractSentenceInsights(record: AnalysisRecord | undefined, sentenceId?: string): ExcerptInsight[] {
  if (!record?.renderScene || !sentenceId) return []
  const entries = (record.renderScene.sentenceEntries || []) as AnySentenceEntryModel[]
  const seen = new Set<string>()
  const results: ExcerptInsight[] = []
  for (const entry of entries) {
    if (entry.sentenceId !== sentenceId) continue
    const meta = insightMeta(entry.entryType)
    if (!meta) continue
    const title = trimText(entry.title || entry.label || meta.label, 28) || meta.label
    const key = `${meta.type}:${title}:${entry.id}`
    if (seen.has(key)) continue
    seen.add(key)
    results.push({
      id: entry.id,
      type: meta.type,
      label: meta.label,
      title,
      detail: getInsightDetail(entry.content),
    })
  }
  return results
}

function collectRecordRefs(favorites: FavoriteItemDto[], annotations: UserAnnotationDto[]) {
  const cloudIds = new Set<string>()
  const clientIds = new Set<string>()

  favorites.forEach(item => {
    if (item.analysis_record_id) cloudIds.add(item.analysis_record_id)
    const clientId = getPayloadString(item.payload_json, 'client_record_id')
    if (clientId) clientIds.add(clientId)
  })

  annotations.forEach(item => {
    if (item.analysis_record_id) cloudIds.add(item.analysis_record_id)
    const clientId = getPayloadString(item.payload_json, 'client_record_id')
    if (clientId) clientIds.add(clientId)
  })

  return { cloudIds, clientIds }
}

async function fetchSourceRecords(favorites: FavoriteItemDto[], annotations: UserAnnotationDto[]): Promise<AnalysisRecord[]> {
  const recordResult = await fetchCloudRecords(1, 100)
  const records = [...recordResult.items]
  const lookup = buildRecordLookup(records)
  const { cloudIds, clientIds } = collectRecordRefs(favorites, annotations)

  // The list endpoint may return lightweight records. Always fetch referenced
  // records in detail so review insights can use full sentenceEntries content.
  const extraRecords = await Promise.all([
    ...Array.from(cloudIds).map(id => fetchCloudRecord(id).catch(() => null)),
    ...Array.from(clientIds).map(id => fetchCloudRecordByClientId(id).catch(() => null)),
  ])

  extraRecords.forEach(record => {
    if (!record) return
    const existing = (record.recordId && lookup.get(record.recordId)) || (record.cloudId && lookup.get(record.cloudId))
    if (existing) {
      const idx = records.findIndex(item => (
        (record.recordId && item.recordId === record.recordId) ||
        (record.cloudId && item.cloudId === record.cloudId)
      ))
      if (idx >= 0) records[idx] = record
      if (record.recordId) lookup.set(record.recordId, { record, rank: existing.rank })
      if (record.cloudId) lookup.set(record.cloudId, { record, rank: existing.rank })
      return
    }
    const rank = records.length
    records.push(record)
    if (record.recordId) lookup.set(record.recordId, { record, rank })
    if (record.cloudId) lookup.set(record.cloudId, { record, rank })
  })

  return records
}

function upsertAsset(map: Map<string, SentenceAsset>, key: string, patch: Partial<SentenceAsset> & Pick<SentenceAsset, 'sourceTitle' | 'text' | 'updatedAt'>) {
  const current = map.get(key)
  const insightMap = new Map<string, ExcerptInsight>()
  ;[...(current?.insights || []), ...(patch.insights || [])].forEach(insight => {
    insightMap.set(`${insight.type}:${insight.title}`, insight)
  })
  map.set(key, {
    key,
    recordId: patch.recordId ?? current?.recordId,
    cloudRecordId: patch.cloudRecordId ?? current?.cloudRecordId,
    sentenceId: patch.sentenceId ?? current?.sentenceId,
    anchorType: patch.anchorType ?? current?.anchorType,
    startOffset: patch.startOffset ?? current?.startOffset,
    endOffset: patch.endOffset ?? current?.endOffset,
    textHash: patch.textHash ?? current?.textHash,
    segments: patch.segments ?? current?.segments,
    sourceTitle: patch.sourceTitle || current?.sourceTitle || '未命名文章',
    sourceSubtitle: patch.sourceSubtitle ?? current?.sourceSubtitle,
    sourceRank: patch.sourceRank ?? current?.sourceRank,
    text: patch.text || current?.text || '',
    translation: patch.translation ?? current?.translation,
    note: patch.note ?? current?.note,
    color: patch.color ?? current?.color,
    isFavorited: patch.isFavorited ?? current?.isFavorited ?? false,
    isHighlighted: patch.isHighlighted ?? current?.isHighlighted ?? false,
    insights: Array.from(insightMap.values()).slice(0, 4),
    updatedAt: new Date(patch.updatedAt).getTime() > new Date(current?.updatedAt || 0).getTime()
      ? patch.updatedAt
      : current?.updatedAt || patch.updatedAt,
  })
}

function buildSentenceAssets(favorites: FavoriteItemDto[], annotations: UserAnnotationDto[], records: AnalysisRecord[]): SentenceAsset[] {
  const map = new Map<string, SentenceAsset>()
  const recordsById = buildRecordLookup(records)

  favorites.forEach(item => {
    if (item.target_type !== 'sentence' && item.target_type !== 'text_range' && item.target_type !== 'multi_text') return
    const isTextRange = item.target_type === 'text_range'
    const isMultiText = item.target_type === 'multi_text'
    const segments = getPayloadSegments(item.payload_json)
    const text = getPayloadString(item.payload_json, isTextRange ? 'selected_text' : 'text')
      || getPayloadString(item.payload_json, 'text')
      || (isMultiText ? getPayloadString(item.payload_json, 'selected_text') : undefined)
    if (!text) return
    const key = getFavoriteSentenceKey(item)
    const clientRecordId = getPayloadString(item.payload_json, 'client_record_id')
    const recordMatch = recordsById.get(item.analysis_record_id || '') || recordsById.get(clientRecordId || '')
    const record = recordMatch?.record
    const sentenceId = getPayloadString(item.payload_json, 'sentence_id') || segments?.[0]?.sentenceId
    const articleTitle = getArticleTitle(item.payload_json, record)
    const recordInsights = extractSentenceInsights(record, sentenceId)
    upsertAsset(map, key, {
      recordId: record?.recordId || clientRecordId || item.analysis_record_id,
      cloudRecordId: item.analysis_record_id,
      sentenceId,
      anchorType: isTextRange ? 'text_range' : isMultiText ? 'multi_text' : 'sentence',
      startOffset: isTextRange ? getPayloadNumber(item.payload_json, 'start_offset') : undefined,
      endOffset: isTextRange ? getPayloadNumber(item.payload_json, 'end_offset') : undefined,
      textHash: isTextRange ? getPayloadString(item.payload_json, 'text_hash') : undefined,
      segments,
      sourceTitle: articleTitle.title,
      sourceSubtitle: articleTitle.subtitle,
      sourceRank: recordMatch?.rank,
      text,
      translation: getPayloadString(item.payload_json, 'translation'),
      isFavorited: true,
      insights: recordInsights.length > 0 ? recordInsights : getPayloadReviewAssets(item.payload_json),
      updatedAt: item.updated_at || item.created_at,
    })
  })

  annotations.forEach(item => {
    if (item.anchor_type !== 'sentence' && item.anchor_type !== 'text_range' && item.anchor_type !== 'multi_text') return
    const key = getAnnotationSentenceKey(item)
    const clientRecordId = getPayloadString(item.payload_json, 'client_record_id')
    const recordMatch = recordsById.get(item.analysis_record_id || '') || recordsById.get(clientRecordId || '')
    const record = recordMatch?.record
    const articleTitle = getArticleTitle(item.payload_json, record)
    const recordInsights = extractSentenceInsights(record, item.sentence_id)
    const segments = item.segments?.length ? item.segments.map(segment => ({
      paragraphId: segment.paragraph_id,
      sentenceId: segment.sentence_id,
      selectedText: segment.selected_text,
      startOffset: segment.start_offset,
      endOffset: segment.end_offset,
      textHash: segment.text_hash,
    })) : undefined
    upsertAsset(map, key, {
      recordId: record?.recordId || clientRecordId || item.analysis_record_id,
      cloudRecordId: item.analysis_record_id,
      sentenceId: item.sentence_id || segments?.[0]?.sentenceId,
      anchorType: item.anchor_type,
      startOffset: item.start_offset,
      endOffset: item.end_offset,
      textHash: item.text_hash,
      segments,
      sourceTitle: articleTitle.title,
      sourceSubtitle: articleTitle.subtitle,
      sourceRank: recordMatch?.rank,
      text: item.selected_text,
      translation: getPayloadString(item.payload_json, 'translation'),
      note: item.note || undefined,
      color: item.color,
      isHighlighted: true,
      insights: recordInsights.length > 0 ? recordInsights : getPayloadReviewAssets(item.payload_json),
      updatedAt: item.updated_at || item.created_at,
    })
  })

  return Array.from(map.values())
    .filter(item => item.text)
}

function getSentenceOrder(sentenceId?: string): number {
  if (!sentenceId) return Number.MAX_SAFE_INTEGER
  const match = sentenceId.match(/\d+/)
  return match ? Number(match[0]) : Number.MAX_SAFE_INTEGER
}

function compareSentenceOrder(a: SentenceAsset, b: SentenceAsset): number {
  const orderDiff = getSentenceOrder(a.sentenceId) - getSentenceOrder(b.sentenceId)
  if (orderDiff !== 0) return orderDiff
  return new Date(a.updatedAt).getTime() - new Date(b.updatedAt).getTime()
}

function groupByArticle(items: SentenceAsset[]): ArticleGroup[] {
  const groups = new Map<string, ArticleGroup>()
  items.forEach(item => {
    const groupKey = item.cloudRecordId || item.recordId || item.sourceTitle
    const current = groups.get(groupKey)
    const nextUpdatedAt = new Date(item.updatedAt).getTime() > new Date(current?.updatedAt || 0).getTime()
      ? item.updatedAt
      : current?.updatedAt || item.updatedAt
    groups.set(groupKey, {
      key: groupKey,
      title: item.sourceTitle,
      subtitle: item.sourceSubtitle ?? current?.subtitle,
      sourceRank: Math.min(item.sourceRank ?? Number.MAX_SAFE_INTEGER, current?.sourceRank ?? Number.MAX_SAFE_INTEGER),
      recordId: item.recordId ?? current?.recordId,
      cloudRecordId: item.cloudRecordId ?? current?.cloudRecordId,
      updatedAt: nextUpdatedAt,
      items: [...(current?.items || []), item],
    })
  })
  return Array.from(groups.values())
    .map(group => ({
      ...group,
      items: group.items.sort(compareSentenceOrder),
    }))
    .sort((a, b) => {
      const rankDiff = (a.sourceRank ?? Number.MAX_SAFE_INTEGER) - (b.sourceRank ?? Number.MAX_SAFE_INTEGER)
      if (rankDiff !== 0) return rankDiff
      return a.title.localeCompare(b.title)
    })
}

function matchesFilter(item: SentenceAsset, filter: ExcerptFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'favorite') return item.isFavorited
  if (filter === 'highlight') return item.isHighlighted
  if (filter === 'note') return Boolean(item.note)
  return item.insights.length > 0
}

export default function ExcerptsPage() {
  const { navBarHeight } = useLayoutStore()
  const isLoggedIn = useAuthStore(state => state.isLoggedIn)
  const [favorites, setFavorites] = useState<FavoriteItemDto[]>([])
  const [annotations, setAnnotations] = useState<UserAnnotationDto[]>([])
  const [records, setRecords] = useState<AnalysisRecord[]>([])
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
      const [favoriteResult, annotationResult] = await Promise.all([
        fetchCloudFavoriteItems(),
        listUserAnnotations(),
      ])
      setFavorites(favoriteResult.items)
      setAnnotations(annotationResult)
      try {
        setRecords(await fetchSourceRecords(favoriteResult.items, annotationResult))
      } catch (recordErr) {
        console.warn('Failed to load excerpt source records', recordErr)
        setRecords([])
      }
    } catch (err) {
      console.warn('Failed to load excerpts', err)
      Taro.showToast({ title: '摘录加载失败', icon: 'none' })
    } finally {
      setLoading(false)
    }
  }, [isLoggedIn])

  useDidShow(loadExcerpts)

  const allAssets = useMemo(() => buildSentenceAssets(favorites, annotations, records), [favorites, annotations, records])
  const filteredAssets = useMemo(() => allAssets.filter(item => matchesFilter(item, activeFilter)), [allAssets, activeFilter])
  const groups = useMemo(() => groupByArticle(filteredAssets), [filteredAssets])
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
        ) : groups.length === 0 ? (
          <View className='excerpts-empty excerpts-empty--compact'>
            <Text className='empty-title'>这一类还没有内容</Text>
            <Text className='empty-copy'>切换到其他类型继续复习。</Text>
          </View>
        ) : (
          <View className='article-group-list'>
            {groups.map(group => (
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
                          {item.note && (
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
                                    {insight.detail && <Text className='asset-insight-excerpt'>{trimInsightPreview(insight.detail, 38)}</Text>}
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
