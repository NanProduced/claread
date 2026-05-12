import { View, Text, ScrollView } from '@tarojs/components'
import { ReactNode } from 'react'
import LucideIcon from '../LucideIcon'
import './index.scss'

interface CenterModalProps {
  visible: boolean
  title?: string
  onClose: () => void
  children: ReactNode
}

export default function CenterModal({
  visible,
  title,
  onClose,
  children,
}: CenterModalProps) {
  if (!visible) return null

  return (
    <View className='modal-overlay' onClick={onClose}>
      <View className='modal-container' onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <View className='modal-header'>
          {title && <Text className='modal-title'>{title}</Text>}
          <View className='modal-close-btn' onClick={onClose}>
            <LucideIcon name='x' size={20} color='var(--text-muted)' />
          </View>
        </View>

        {/* Content */}
        <View className='modal-content'>
          <ScrollView scrollY className='modal-scroll'>
            {children}
          </ScrollView>
        </View>
      </View>
    </View>
  )
}
