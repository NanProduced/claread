import { useState } from 'react'
import { View, Text } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import './index.scss'

interface AcademicEntryAggregatorProps {
  totalEntries: number
  entryTypes?: {
    term: number
    logic: number
    interpretation: number
  }
  onToggle: (expanded: boolean) => void
  isAcademicMode?: boolean
}

export default function AcademicEntryAggregator({
  totalEntries,
  entryTypes = { term: 0, logic: 0, interpretation: 0 },
  onToggle,
  isAcademicMode = true,
}: AcademicEntryAggregatorProps) {
  const [expanded, setExpanded] = useState(false)

  const handleToggle = () => {
    const next = !expanded
    setExpanded(next)
    onToggle?.(next)
  }

  if (!isAcademicMode || totalEntries === 0) return null

  return (
    <View className={`academic-entry-aggregator ${expanded ? 'is-expanded' : 'is-collapsed'}`}>
      {/* Collapsed State - 单行 Chip */}
      <View className='aggregator-chip' onClick={handleToggle}>
        <View className='chip-icon'>
          <LucideIcon name='message-square-text' size={14} color='var(--academic-label-color)' />
        </View>

        <Text className='chip-label'>学术注释</Text>

        <View className='chip-count'>
          <Text className='count-number'>{totalEntries}</Text>
        </View>

        {entryTypes.term > 0 && (
          <View className='type-badge type-term'>
            <Text className='badge-count'>{entryTypes.term}</Text>
          </View>
        )}

        {entryTypes.logic > 0 && (
          <View className='type-badge type-logic'>
            <Text className='badge-count'>{entryTypes.logic}</Text>
          </View>
        )}

        <View className={`expand-indicator ${expanded ? 'is-open' : ''}`}>
          <LucideIcon name='chevron-down' size={12} color='var(--reader-muted)' />
        </View>
      </View>

      {/* Expanded State - 类型分布提示 (可选) */}
      {expanded && (
        <View className='aggregator-hint'>
          <Text className='hint-text'>
            包含{entryTypes.term > 0 ? `${entryTypes.term}个术语` : ''}
            {entryTypes.term > 0 && entryTypes.logic > 0 ? '、' : ''}
            {entryTypes.logic > 0 ? `${entryTypes.logic}处论证` : ''}
            {(entryTypes.term > 0 || entryTypes.logic > 0) && entryTypes.interpretation > 0 ? '、' : ''}
            {entryTypes.interpretation > 0 ? `${entryTypes.interpretation}条解释` : ''}
            {' '}· 点击各条目查看详情
          </Text>
        </View>
      )}
    </View>
  )
}
