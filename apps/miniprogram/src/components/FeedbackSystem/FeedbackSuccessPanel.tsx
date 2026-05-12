import { View, Text } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import { FEEDBACK_STATUS_LABELS } from '../../config/feedback'
import './FeedbackSuccessPanel.scss'

interface FeedbackSuccessPanelProps {
  isUpsert?: boolean
  onViewMyFeedback: () => void
  onDismiss: () => void
  inline?: boolean
}

export default function FeedbackSuccessPanel({
  isUpsert,
  onViewMyFeedback,
  onDismiss,
  inline = false
}: FeedbackSuccessPanelProps) {
  const title = '已收到反馈'
  const desc = inline
    ? (isUpsert ? '已更新你的反馈。' : '感谢说明。处理结果会出现在「我的反馈」中。')
    : (isUpsert ? '已更新你的反馈。你可以在「我的反馈」查看处理状态。' : '我们已记录这处内容。你可以在「我的反馈」查看处理状态。')

  return (
    <View className={`feedback-success-panel ${inline ? 'feedback-success-panel--inline' : ''}`}>
      <View className='feedback-success-panel__icon'>
        <LucideIcon name='check-circle' size={48} color='var(--color-success)' />
      </View>
      <Text className='feedback-success-panel__title'>{title}</Text>
      <Text className='feedback-success-panel__desc'>{desc}</Text>

      <View className='feedback-success-panel__actions'>
        <View className='feedback-success-panel__btn feedback-success-panel__btn--primary' onClick={onViewMyFeedback}>
          查看我的反馈
        </View>
        <View className='feedback-success-panel__btn feedback-success-panel__btn--secondary' onClick={onDismiss}>
          知道了
        </View>
      </View>
    </View>
  )
}
