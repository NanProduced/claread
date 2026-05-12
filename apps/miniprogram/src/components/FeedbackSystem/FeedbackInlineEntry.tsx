import { View, Text } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import './FeedbackInlineEntry.scss'

export interface FeedbackInlineAction {
  id: string
  label: string
  icon?: string
  sentiment?: 'positive' | 'negative' | 'neutral'
  submitted?: boolean
  onClick: () => void
}

interface FeedbackInlineEntryProps {
  actions: FeedbackInlineAction[]
}

export default function FeedbackInlineEntry({ actions }: FeedbackInlineEntryProps) {
  return (
    <View className='feedback-inline-entry'>
      {actions.map(action => (
        <View
          key={action.id}
          className={`feedback-inline-entry__action feedback-inline-entry__action--${action.sentiment || 'neutral'} ${action.submitted ? 'is-submitted' : ''}`}
          onClick={(e) => {
            e.stopPropagation()
            action.onClick()
          }}
        >
          {action.icon && (
            <View className='feedback-inline-entry__icon-wrap'>
              <LucideIcon name={action.icon} size={32} strokeWidth={2.2} />
            </View>
          )}
          <Text className='feedback-inline-entry__label'>{action.label}</Text>
        </View>
      ))}
    </View>
  )
}
