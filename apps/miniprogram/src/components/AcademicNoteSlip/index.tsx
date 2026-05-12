import { useState } from 'react'
import { View, Text } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import type { AcademicSentenceEntryType } from '../../types/view/render-scene.vm'
import './index.scss'

type NoteVariant = 'term' | 'logic' | 'interpretation'

interface AcademicNoteSlipProps {
  variant: NoteVariant
  title: string
  content: string
  initiallyExpanded?: boolean
  onToggle?: (expanded: boolean) => void
}

const VARIANT_LABEL: Record<NoteVariant, string> = {
  term: '术语',
  logic: '论证',
  interpretation: '阐释',
}

export default function AcademicNoteSlip({
  variant,
  title,
  content,
  initiallyExpanded = false,
  onToggle,
}: AcademicNoteSlipProps) {
  const [isExpanded, setIsExpanded] = useState(initiallyExpanded)

  const handleToggle = () => {
    const next = !isExpanded
    setIsExpanded(next)
    onToggle?.(next)
  }

  const label = VARIANT_LABEL[variant]

  return (
    <View className={`academic-note-slip ${isExpanded ? 'is-expanded' : 'is-collapsed'}`}>
      <View className='slip-header' onClick={handleToggle}>
        <View className='slip-header-main'>
          <Text className='slip-variant'>{label}</Text>
          <Text className='slip-sep'>·</Text>
          <Text className='slip-title' numberOfLines={1}>{title}</Text>
        </View>
        <View className={`slip-chevron ${isExpanded ? 'is-open' : ''}`}>
          <LucideIcon name='chevron-right' size={14} color='var(--reader-muted)' />
        </View>
      </View>

      <View className={`slip-body ${isExpanded ? 'show' : 'hide'}`}>
        <View className='slip-body-inner'>
          <Text className='slip-content'>{content}</Text>
        </View>
      </View>
    </View>
  )
}
