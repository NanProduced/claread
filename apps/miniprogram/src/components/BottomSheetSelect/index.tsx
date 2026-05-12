import { View, Text } from '@tarojs/components'
import { ReactNode } from 'react'
import { ReadingGoal } from '../../config/purpose'
import LucideIcon from '../LucideIcon'
import ConfigEditor, { ConfigEditorMode } from '../ConfigEditor'
import './index.scss'

interface BottomSheetSelectProps {
  visible: boolean
  title?: string
  currentGoal?: ReadingGoal
  currentLevel?: string | null
  mode?: ConfigEditorMode
  onClose: () => void
  onSelect?: (goal: ReadingGoal, level: string | null) => void
  children?: ReactNode
}

export default function BottomSheetSelect({
  visible,
  title = '选择分析模式',
  currentGoal,
  currentLevel,
  mode = 'reparse',
  onClose,
  onSelect,
  children,
}: BottomSheetSelectProps) {
  if (!visible) return null

  return (
    <View className='bs-select-overlay' onClick={onClose}>
      <View className='bs-select-container' onClick={(e) => e.stopPropagation()}>
        {/* 拖动条 */}
        <View className='bs-drag-handle' />

        {/* Header */}
        <View className='bs-header'>
          <Text className='bs-title'>{title}</Text>
          <View className='bs-close-btn' onClick={onClose}>
            <LucideIcon name='x' size={20} color='var(--text-muted)' />
          </View>
        </View>

        {/* 列表内容 */}
        <View className='bs-content'>
          {children ? (
            children
          ) : (
            currentGoal !== undefined && onSelect && (
              <ConfigEditor
                mode={mode}
                initialGoal={currentGoal}
                initialLevel={currentLevel}
                onComplete={(g, l) => {
                  onSelect(g, l)
                  onClose()
                }}
              />
            )
          )}
        </View>

        <View className='bs-safe-bottom' />
      </View>
    </View>
  )
}
