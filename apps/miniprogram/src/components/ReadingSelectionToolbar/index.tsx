import { View, Text } from '@tarojs/components'
import { memo, useState, useCallback, useEffect } from 'react'
import LucideIcon from '../LucideIcon'
import './index.scss'

export interface SelectionContext {
  recordId?: string
  paragraphId?: string
  sentenceId: string
  selectedText: string
  startOffset: number
  endOffset: number
  textHash?: string
  translation?: string
  anchorType: 'sentence' | 'paragraph' | 'text_range'
}

type CopyMode = 'original' | 'translation' | 'bilingual'

interface Props {
  visible: boolean
  context: SelectionContext | null
  isFavorited?: boolean
  hasAnnotation?: boolean
  hasNote?: boolean
  onClose: () => void
  onCopy: (mode: CopyMode) => void
  onFavorite: () => void
  onNote: () => void
  onFeedback: () => void
}

const ReadingSelectionToolbar = memo(function ReadingSelectionToolbar({
  visible,
  context,
  isFavorited,
  hasAnnotation,
  hasNote,
  onClose,
  onCopy,
  onFavorite,
  onNote,
  onFeedback,
}: Props) {
  const [showCopyMenu, setShowCopyMenu] = useState(false)

  useEffect(() => {
    if (!visible) {
      setShowCopyMenu(false)
    }
  }, [visible])

  const handleAction = useCallback((action: () => void) => (e: any) => {
    e.stopPropagation()
    setShowCopyMenu(false)
    action()
  }, [])

  const handleCopyClick = useCallback((e: any) => {
    e.stopPropagation()
    setShowCopyMenu(prev => !prev)
  }, [])

  const handleCopyMode = useCallback((mode: CopyMode) => (e: any) => {
    e.stopPropagation()
    setShowCopyMenu(false)
    onCopy(mode)
  }, [onCopy])

  if (!visible || !context) return null

  return (
    <View className='sel-toolbar-root'>
      <View className='sel-backdrop' onClick={onClose} />
      {showCopyMenu && (
        <View className='sel-copy-menu' onClick={e => e.stopPropagation()}>
          <View className='sel-copy-menu-item' onClick={handleCopyMode('original')}>
            <Text>复制原文</Text>
          </View>
          {context.translation && (
            <View className='sel-copy-menu-item' onClick={handleCopyMode('translation')}>
              <Text>复制译文</Text>
            </View>
          )}
          <View className='sel-copy-menu-item' onClick={handleCopyMode('bilingual')}>
            <Text>复制双语</Text>
          </View>
        </View>
      )}
      <View className='sel-floating-toolbar' onClick={e => e.stopPropagation()}>
        <View className={`sel-tool-btn ${hasAnnotation || hasNote ? 'sel-tool-btn--noted' : ''}`} onClick={handleAction(onNote)}>
          <LucideIcon name='pen-line' size={20} color='currentColor' />
          <Text className='sel-tool-label'>{hasNote ? '编辑' : hasAnnotation ? '批注' : '笔记'}</Text>
        </View>

        <View className='sel-tool-btn sel-tool-btn--has-menu' onClick={handleCopyClick}>
          <LucideIcon name='copy' size={20} color='currentColor' />
          <Text className='sel-tool-label'>复制</Text>
        </View>

        <View className={`sel-tool-btn ${isFavorited ? 'sel-tool-btn--active' : ''}`} onClick={handleAction(onFavorite)}>
          <LucideIcon name='bookmark' size={20} color='currentColor' />
          <Text className='sel-tool-label'>收藏</Text>
        </View>

        <View className='sel-tool-btn' onClick={handleAction(onFeedback)}>
          <LucideIcon name='message-square-warning' size={20} color='currentColor' />
          <Text className='sel-tool-label'>反馈</Text>
        </View>
      </View>
    </View>
  )
})

export default ReadingSelectionToolbar
export type { CopyMode }
