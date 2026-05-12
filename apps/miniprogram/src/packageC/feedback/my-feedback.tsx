import { View, Text, Image } from '@tarojs/components'
import { useState, useEffect } from 'react'
import { fetchFeedbackList, FeedbackListItem, deleteFeedback } from '../../services/api/feedback.client'
import { useAuthStore } from '../../stores/auth'
import Taro from '@tarojs/taro'
import LucideIcon from '../../components/LucideIcon'
import NavBar from '../../components/NavBar'
import { useLayoutStore } from '../../stores/layout'
import { FEEDBACK_STATUS_LABELS, FEEDBACK_CONFIG_BY_SCOPE } from '../../config/feedback'
import emptyIllustration from '../../assets/illustrations/empty-feedback.jpg'
import './my-feedback.scss'

const SCOPE_LABELS: Record<string, string> = {
  analysis_result: '结果反馈',
  annotation: '标注反馈',
  dictionary: '词典反馈',
  app: '应用反馈',
}

// 获取反馈类型的中文标签
const getFeedbackTypeLabel = (scope: string, type: string): string => {
  const config = FEEDBACK_CONFIG_BY_SCOPE[scope as keyof typeof FEEDBACK_CONFIG_BY_SCOPE]
  if (!config) return type

  const allOptions = [
    ...(config.positiveOptions || []),
    ...(config.negativeOptions || []),
    ...(config.neutralOptions || []),
  ]

  const option = allOptions.find(opt => opt.value === type)
  return option?.label || type
}

export default function MyFeedbackPage() {
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn)
  const { navBarHeight } = useLayoutStore()
  const [items, setItems] = useState<FeedbackListItem[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (isLoggedIn) loadMore(true)
  }, [isLoggedIn])

  const loadMore = async (isFirst = false) => {
    if (!isFirst && loading) return
    setLoading(true)
    try {
      const res = await fetchFeedbackList({ cursor: isFirst ? undefined : (cursor || undefined), limit: 20 })
      setItems(prev => isFirst ? res.items : [...prev, ...res.items])
      setCursor(res.cursor)
      setHasMore(res.hasMore)
    } catch {
      Taro.showToast({ title: '加载失败', icon: 'none', duration: 1500 })
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteFeedback(id)
      setItems(prev => prev.filter(item => item.id !== id))
    } catch {
      Taro.showToast({ title: '删除失败', icon: 'none', duration: 1500 })
    }
  }

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return `${d.getMonth() + 1}月${d.getDate()}日`
  }

  return (
    <View className='my-feedback'>
      <NavBar
        title='我的反馈'
        showBack
        background='var(--reader-paper, #FAF9F6)'
        color='var(--text-primary, #111111)'
      />
      <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />

      {items.length === 0 && !loading && (
        <View className='my-feedback__empty'>
          <Image
            className='my-feedback__empty-img'
            src={emptyIllustration}
            mode='aspectFit'
          />
          <Text className='my-feedback__empty-title'>还没有反馈记录</Text>
          <Text className='my-feedback__empty-body'>
            如果你在使用中发现释义有误、标注不准或有改进建议，欢迎随时告诉我们。
          </Text>
        </View>
      )}

      <View className='my-feedback__list'>
        {items.map((item, index) => {
          const statusLabel = FEEDBACK_STATUS_LABELS[item.status] || item.status
          const typeLabel = item.feedbackType
            ? getFeedbackTypeLabel(item.feedbackScope, item.feedbackType)
            : ''

          return (
            <View
              key={item.id}
              className='my-feedback__item'
              style={{ animationDelay: `${index * 0.05}s` }}
            >
              <View className='my-feedback__item-header'>
                <Text className='my-feedback__item-scope'>
                  {SCOPE_LABELS[item.feedbackScope] || item.feedbackScope}
                  {typeLabel && ` · ${typeLabel}`}
                </Text>
                <Text className='my-feedback__item-date'>{formatDate(item.createdAt)}</Text>
              </View>

              {item.content && (
                <Text className='my-feedback__item-content'>{item.content}</Text>
              )}

              <View className='my-feedback__item-footer'>
                <View className='my-feedback__item-status-group'>
                  <Text className='my-feedback__item-status' data-status={item.status}>
                    {statusLabel}
                  </Text>
                  {item.rewardPoints > 0 && (
                    <Text className='my-feedback__item-reward'>+{item.rewardPoints} 积分</Text>
                  )}
                </View>
                {item.status === 'pending' && (
                  <View
                    className='my-feedback__item-delete'
                    onClick={() => handleDelete(item.id)}
                  >
                    <Text>撤回</Text>
                  </View>
                )}
              </View>
            </View>
          )
        })}
      </View>

      {hasMore && (
        <View className='my-feedback__load-more' onClick={() => loadMore(false)}>
          <Text className='my-feedback__load-more-text'>
            {loading ? '加载中...' : '加载更多'}
          </Text>
        </View>
      )}
    </View>
  )
}
