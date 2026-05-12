import { View, Text } from '@tarojs/components'
import AnnotationGlyph from '../AnnotationGlyph'
import LucideIcon from '../LucideIcon'
import './index.scss'

interface NoteTabProps {
  type: 'grammar_note' | 'sentence_analysis'
  title?: string
  label?: string
  onClick?: () => void
  className?: string
}

export default function NoteTab({
  type,
  title,
  label,
  onClick,
  className = ''
}: NoteTabProps) {
  // Construct display text based on available props
  // "语法 · 定语从句", "语法", "句式解析"
  let displayText = ''
  if (label && title) {
    displayText = `${label} · ${title}`
  } else if (label) {
    displayText = label
  } else if (title) {
    displayText = title
  } else {
    displayText = type === 'grammar_note' ? '语法' : '句式解析'
  }

  return (
    <View className={`note-tab ${className}`} onClick={onClick}>
      <AnnotationGlyph type={type} size={18} className="note-tab-icon" />
      <Text className="note-tab-text">{displayText}</Text>
      <LucideIcon name="chevron-right" size={16} color="#7A7D86" className="note-tab-arrow" />
    </View>
  )
}
