import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { getCompactLabel } from '../../config/purpose'
import LucideIcon from '../LucideIcon'
import './index.scss'

interface ReaderContextBarProps {
  sourceType?: string
  readingGoal?: string
  readingVariant?: string
  pageMode?: string
  isAcademicMode?: boolean
  onClick?: () => void
  onEdit?: () => void
  onModeToggle?: () => void
  onSettingsClick?: () => void
}

export default function ReaderContextBar({
  sourceType,
  readingGoal,
  readingVariant,
  pageMode,
  isAcademicMode,
  onClick,
  onEdit,
  onModeToggle,
  onSettingsClick,
}: ReaderContextBarProps) {
  const goalLabel = getCompactLabel(readingGoal || 'daily_reading', readingVariant)
  const isImmersive = pageMode === 'immersive'

  const handleReparse = (e: { stopPropagation: () => void }) => {
    e.stopPropagation()

    Taro.showModal({
      title: '更换解析模式',
      content: '将使用新模式重新生成解析页面，当前页面的标注和笔记将被保留在历史记录中。\n\n是否继续？',
      confirmText: '继续',
      cancelText: '取消',
      confirmColor: '#2E5BB2',
      success: (res) => {
        if (res.confirm) {
          onEdit?.()
        }
      },
    })
  }

  return (
    <View className='reader-context-bar' onClick={onClick}>
      <View className='reader-context-items'>
        <Text className='context-item goal' numberOfLines={1}>{goalLabel}</Text>

        <Text className='context-divider'>·</Text>

        <View className='mode-switcher'>
          <View
            className={`mode-option ${!isImmersive ? 'active' : ''}`}
            onClick={(e) => {
              e.stopPropagation()
              if (isImmersive) onModeToggle?.()
            }}
          >
            <Text>精读</Text>
          </View>
          <View
            className={`mode-option ${isImmersive ? 'active' : ''}`}
            onClick={(e) => {
              e.stopPropagation()
              if (!isImmersive) onModeToggle?.()
            }}
          >
            <Text>原文</Text>
          </View>
        </View>

        {isAcademicMode && goalLabel !== '学术' && (
          <>
            <Text className='context-divider'>·</Text>
            <Text className='context-item academic'>学术</Text>
          </>
        )}
      </View>

      <View className='actions-group'>
        <View
          className='settings-button'
          onClick={(e) => {
            e.stopPropagation()
            onSettingsClick?.()
          }}
        >
          <Text className='settings-text'>Aa</Text>
        </View>

        <View className='action-divider' />

        <View
          className='reparse-button'
          onClick={handleReparse}
        >
          <LucideIcon name='refresh-cw' size={16} color='currentColor' strokeWidth={1.8} />
          <Text className='reparse-text'>换模式</Text>
        </View>
      </View>
    </View>
  )
}
