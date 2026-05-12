import { View, Text } from '@tarojs/components'
import { useState, useEffect, useMemo } from 'react'
import Taro from '@tarojs/taro'
import { fetchCreditLedger, LedgerEntry } from '../../services/api/credit.client'
import { fetchUserQuota } from '../../services/api/client'
import { useAuthStore } from '../../stores/auth'
import { useLayoutStore } from '../../stores/layout'
import NavBar from '../../components/NavBar'
import LucideIcon from '../../components/LucideIcon'
import './index.scss'

const TYPE_CONFIG: Record<string, { label: string; icon: string; iconColor: string }> = {
  analysis_deduct: { label: '分析扣减', icon: 'search', iconColor: '#7A7D86' },
  feedback_reward: { label: '反馈奖励', icon: 'gift', iconColor: '#059669' },
  daily_grant: { label: '每日发放', icon: 'sun', iconColor: '#059669' },
  bonus_grant: { label: '奖励到账', icon: 'sparkles', iconColor: '#D97706' },
  refund: { label: '积分退回', icon: 'refreshCw', iconColor: 'var(--color-info)' },
  manual_adjust: { label: '管理员调整', icon: 'settings', iconColor: 'var(--reader-subtle)' },
}

const BUCKET_LABELS: Record<string, { text: string; className: string; color?: string }> = {
  daily_free: { text: '每日免费', className: 'credit-detail__bucket--free' },
  bonus: { text: '奖励', className: 'credit-detail__bucket--bonus', color: '#3B82F6' },
}

const WEEK_DAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function formatDateGroup(dateStr: string): string {
  const d = new Date(dateStr)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diff = Math.floor((today.getTime() - target.getTime()) / (1000 * 60 * 60 * 24))

  if (diff === 0) return '今天'
  if (diff === 1) return '昨天'
  if (diff === 2) return '前天'
  if (diff < 7) return WEEK_DAYS[d.getDay()]
  if (d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`
  return `${d.getMonth() + 1}月${d.getDate()}日 ${WEEK_DAYS[d.getDay()]}`
}

function formatTime(dateStr: string): string {
  const d = new Date(dateStr)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function isToday(dateStr: string): boolean {
  const d = new Date(dateStr)
  const now = new Date()
  return d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
}

interface GroupedEntries {
  dateLabel: string
  entries: LedgerEntry[]
}

function groupByDate(entries: LedgerEntry[]): GroupedEntries[] {
  const groups: GroupedEntries[] = []
  let currentLabel = ''
  let currentEntries: LedgerEntry[] = []

  for (const entry of entries) {
    const label = formatDateGroup(entry.createdAt)
    if (label !== currentLabel) {
      if (currentEntries.length > 0) groups.push({ dateLabel: currentLabel, entries: currentEntries })
      currentLabel = label
      currentEntries = []
    }
    currentEntries.push(entry)
  }
  if (currentEntries.length > 0) groups.push({ dateLabel: currentLabel, entries: currentEntries })
  return groups
}

export default function CreditDetailPage() {
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn)
  const [entries, setEntries] = useState<LedgerEntry[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dailyFreeTotal, setDailyFreeTotal] = useState(1000)
  const { navBarHeight } = useLayoutStore()

  useEffect(() => {
    if (isLoggedIn) {
      loadMore()
      fetchUserQuota()
        .then(res => setDailyFreeTotal(res.daily_free_points))
        .catch(() => {})
    }
  }, [isLoggedIn])

  const loadMore = async () => {
    if (loading) return
    setLoading(true)
    try {
      const res = await fetchCreditLedger({ cursor: cursor || undefined, limit: 20 })
      setEntries(prev => [...prev, ...res.items])
      setCursor(res.cursor)
      setHasMore(res.hasMore)
    } catch (e) {
      console.error("index.tsx:", e)
    } finally {
      setLoading(false)
    }
  }

  const summary = useMemo(() => {
    const todayEntries = entries.filter(e => isToday(e.createdAt))
    const todayDailyUsed = todayEntries
      .filter(e => e.points < 0 && e.bucketType === 'daily_free')
      .reduce((s, e) => s + Math.abs(e.points), 0)

    const totalBalance = entries.length > 0 ? entries[0].balanceAfter : null
    const dailyRemaining = Math.max(0, dailyFreeTotal - todayDailyUsed)
    let bonusBalance = 0
    if (totalBalance !== null) bonusBalance = Math.max(0, totalBalance - dailyRemaining)

    return { todayDailyUsed, dailyRemaining, bonusBalance, totalBalance }
  }, [entries])

  const progressPercent = dailyFreeTotal > 0 ? (summary.todayDailyUsed / dailyFreeTotal) * 100 : 0

  const handleBonusCTA = () => {
    Taro.showToast({ title: '邀请功能开发中', icon: 'none' })
  }

  const handleEntryClick = (entry: LedgerEntry) => {
    if (entry.taskId) {
      Taro.navigateTo({ url: `/packageA/history/index?highlightTask=${entry.taskId}` })
    }
  }

  const groups = groupByDate(entries)

  const cleanDescription = (entry: LedgerEntry): string => {
    const label = TYPE_CONFIG[entry.entryType]?.label || entry.entryType
    for (const sep of ['，', '·', ' ', '：', ':']) {
      const prefix = `${label}${sep}`
      if (entry.description.startsWith(prefix)) return entry.description.slice(prefix.length).trim()
    }
    return entry.description
  }

  return (
    <View className='credit-detail'>
      <NavBar
        title='积分记录'
        showBack
        background='var(--reader-paper, #FAF9F6)'
        color='var(--text-primary, #111111)'
      />
      <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />

      <View className='credit-detail__summary'>
        <Text className='credit-detail__summary-label'>今日剩余</Text>
        <View className='credit-detail__summary-main'>
          <Text className='credit-detail__summary-number'>{summary.dailyRemaining}</Text>
          <Text className='credit-detail__summary-total'> / {dailyFreeTotal}</Text>
        </View>
        <View className='credit-detail__progress-track'>
          <View
            className='credit-detail__progress-fill'
            style={{ width: `${Math.min(progressPercent, 100)}%` }}
          />
        </View>
        <View className='credit-detail__summary-bonus-row'>
          <View className='credit-detail__bonus-left'>
            <LucideIcon name='heart' size={22} color='#D97706' strokeWidth={2} />
            <Text className='credit-detail__bonus-label'>奖励余额</Text>
            <Text className={`credit-detail__bonus-value ${summary.bonusBalance === 0 ? 'credit-detail__bonus-value--zero' : ''}`}>
              {summary.bonusBalance ?? '--'}
            </Text>
          </View>
          {(summary.bonusBalance === 0 || summary.bonusBalance == null) && (
            <View className='credit-detail__bonus-cta' onClick={handleBonusCTA}>
              <Text className='credit-detail__bonus-cta-text'>邀请赚积分</Text>
              <LucideIcon name='chevronRight' size={22} color='#3B82F6' strokeWidth={2} />
            </View>
          )}
        </View>
      </View>

      {groups.length > 0 && (
        <View className='credit-detail__list'>
          {groups.map(group => (
            <View key={group.dateLabel} className='credit-detail__group'>
              <Text className='credit-detail__date-label'>{group.dateLabel}</Text>
              {group.entries.map(entry => {
                const config = TYPE_CONFIG[entry.entryType] || { label: entry.entryType, icon: 'circle', iconColor: 'var(--reader-subtle)' }
                const isPositive = entry.points > 0
                const desc = cleanDescription(entry)
                const bucket = BUCKET_LABELS[entry.bucketType]
                const absPoints = Math.abs(entry.points)

                return (
                  <View key={entry.id} className={`credit-detail__entry ${isPositive ? 'credit-detail__entry--positive' : ''}`} onClick={() => handleEntryClick(entry)}>
                    <View className='credit-detail__entry-icon-wrap'>
                      <LucideIcon name={config.icon as any} size={28} color={config.iconColor} strokeWidth={1.8} />
                    </View>
                    <View className='credit-detail__entry-body'>
                      <View className='credit-detail__entry-title-row'>
                        <Text className='credit-detail__entry-desc'>{desc || (entry.articleTitle || '积分变动')}</Text>
                      </View>
                      {entry.articleTitle && desc && !desc.includes(entry.articleTitle.slice(0, 6)) && (
                        <Text className='credit-detail__entry-article'>《{entry.articleTitle.slice(0, 30)}》</Text>
                      )}
                      <View className='credit-detail__entry-meta'>
                        {bucket && (
                          <>
                            <Text className={`credit-detail__entry-bucket ${bucket.className}`} style={bucket.color ? { color: bucket.color } : undefined}>
                              {bucket.text}
                            </Text>
                            <Text className='credit-detail__entry-meta-sep'>·</Text>
                          </>
                        )}
                        <Text className='credit-detail__entry-time'>{formatTime(entry.createdAt)}</Text>
                      </View>
                    </View>
                    <View className='credit-detail__entry-amount'>
                      <Text className={`credit-detail__entry-points ${isPositive ? 'credit-detail__entry-points--plus' : ''}`}>
                        {isPositive ? '+' : '−'}{absPoints}
                      </Text>
                    </View>
                  </View>
                )
              })}
            </View>
          ))}
        </View>
      )}

      {hasMore && (
        <View className='credit-detail__load-more' onClick={loadMore}>
          <Text className='credit-detail__load-more-text'>
            {loading ? '加载中...' : '加载更多'}
          </Text>
        </View>
      )}

      {entries.length === 0 && !loading && (
        <View className='credit-detail__empty'>
          <View className='credit-detail__empty-icon'>
            <LucideIcon name='ticket' size={56} color='var(--reader-subtle)' strokeWidth={1.2} />
          </View>
          <Text className='credit-detail__empty-title'>暂无记录</Text>
          <Text className='credit-detail__empty-desc'>开始阅读后，积分变动会记录在这里</Text>
        </View>
      )}
    </View>
  )
}
