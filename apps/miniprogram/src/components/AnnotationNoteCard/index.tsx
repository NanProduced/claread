import { View, Text } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import FeedbackInlineEntry from '../FeedbackSystem/FeedbackInlineEntry'
import './index.scss'

export interface AnnotationNoteCardProps {
  title: string
  isSaved?: boolean
  onSaveToggle?: () => void
  onHelpful?: () => void
  onInaccurate?: () => void
  onFeedback?: () => void
  children?: React.ReactNode
  className?: string
}

export default function AnnotationNoteCard({
  title,
  isSaved,
  onSaveToggle,
  onHelpful,
  onInaccurate,
  onFeedback,
  children,
  className = ''
}: AnnotationNoteCardProps) {
  return (
    <View className={`annotation-note-card ${className}`}>
      {/* Header */}
      <View className="note-card-header">
        <Text className="note-card-title">{title}</Text>
        <View className="note-card-save" onClick={onSaveToggle}>
          <LucideIcon
            name={isSaved ? "bookmarkCheck" : "bookmark"}
            size={20}
            color={isSaved ? "var(--color-ink)" : "var(--reader-muted)"}
          />
        </View>
      </View>

      {/* Content Body */}
      <View className="note-card-body">
        {children}
      </View>

      {/* Feedback Bar */}
      <View className="note-card-footer">
        <FeedbackInlineEntry
          actions={[
            { id: 'helpful', label: '有帮助', icon: 'thumbsUp', sentiment: 'positive', onClick: () => onHelpful?.() },
            { id: 'inaccurate', label: '不准确', icon: 'thumbsDown', sentiment: 'negative', onClick: () => onInaccurate?.() },
            { id: 'feedback', label: '反馈', icon: 'messageSquare', sentiment: 'neutral', onClick: () => onFeedback?.() }
          ]}
        />
      </View>
    </View>
  )
}
