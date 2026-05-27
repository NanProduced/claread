import { View, Text } from '@tarojs/components'
import { memo, useState, useCallback, useEffect } from 'react'
import LucideIcon from '../LucideIcon'
import './index.scss'

export type SelectionToolbarColorValue = 'soft_green' | 'soft_blue' | 'soft_purple' | 'warm_yellow' | 'sage_green'

export interface SelectionToolbarColorOption {
  value: SelectionToolbarColorValue
  label: string
  swatchClassName: string
}

export interface SelectionContext {
  recordId?: string
  paragraphId?: string
  sentenceId: string
  selectedText: string
  startOffset: number
  endOffset: number
  textHash?: string
  translation?: string
  anchorType: 'sentence'
}

type CopyMode = 'original' | 'translation' | 'bilingual'

interface Props {
  visible: boolean
  context: SelectionContext | null
  onClose: () => void
  onCopy: (mode: CopyMode) => void
  onFeedback: () => void
  onHighlight: (color: SelectionToolbarColorValue, selectedText: string) => void
  onNote: (selectedText: string) => void
}

export const selectionToolbarColorOptions: SelectionToolbarColorOption[] = [
  { value: 'warm_yellow', label: '暖黄', swatchClassName: 'sel-swatch--warm-yellow' },
  { value: 'soft_blue', label: '雾青', swatchClassName: 'sel-swatch--soft-blue' },
  { value: 'sage_green', label: '灰绿', swatchClassName: 'sel-swatch--sage-green' },
]

const ReadingSelectionToolbar = memo(function ReadingSelectionToolbar({
  visible,
  context,
  onClose,
  onCopy,
  onFeedback,
  onHighlight,
  onNote,
}: Props) {
  const [showCopyMenu, setShowCopyMenu] = useState(false)
  const [showColorPicker, setShowColorPicker] = useState(false)
  const [activeColor, setActiveColor] = useState<SelectionToolbarColorValue>('warm_yellow')

  useEffect(() => {
    if (!visible) {
      setShowCopyMenu(false)
      setShowColorPicker(false)
    }
  }, [visible])

  const handleAction = useCallback((action: () => void) => (e: any) => {
    e.stopPropagation()
    setShowCopyMenu(false)
    setShowColorPicker(false)
    action()
  }, [])

  const handleCopyClick = useCallback((e: any) => {
    e.stopPropagation()
    setShowCopyMenu(prev => !prev)
    setShowColorPicker(false)
  }, [])

  const handleCopyMode = useCallback((mode: CopyMode) => (e: any) => {
    e.stopPropagation()
    setShowCopyMenu(false)
    onCopy(mode)
  }, [onCopy])

  const handleHighlightClick = useCallback((e: any) => {
    e.stopPropagation()
    setShowCopyMenu(false)
    setShowColorPicker(prev => !prev)
  }, [])

  const handleColorSelect = useCallback((color: SelectionToolbarColorValue) => (e: any) => {
    e.stopPropagation()
    setActiveColor(color)
    setShowColorPicker(false)
    onHighlight(color, context?.selectedText || '')
  }, [onHighlight, context])

  const handleNoteClick = useCallback((e: any) => {
    e.stopPropagation()
    setShowCopyMenu(false)
    setShowColorPicker(false)
    onNote(context?.selectedText || '')
  }, [onNote, context])

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
      {showColorPicker && (
        <View className='sel-color-picker' onClick={e => e.stopPropagation()}>
          <View className='sel-color-picker__label'>选择高亮颜色</View>
          <View className='sel-color-picker__options'>
            {selectionToolbarColorOptions.map((option) => (
              <View
                key={option.value}
                className={`sel-color-btn ${activeColor === option.value ? 'is-active' : ''}`}
                onClick={handleColorSelect(option.value)}
              >
                <View className={`sel-color-btn__swatch ${option.swatchClassName}`} />
                <Text className='sel-color-btn__label'>{option.label}</Text>
              </View>
            ))}
          </View>
        </View>
      )}
      <View className='sel-floating-toolbar' onClick={e => e.stopPropagation()}>
        <View className={`sel-tool-btn ${showColorPicker ? 'sel-tool-btn--active' : ''}`} onClick={handleHighlightClick}>
          <LucideIcon name='highlighter' size={20} color='currentColor' />
          <View className={`sel-tool-indicator ${selectionToolbarColorOptions.find(option => option.value === activeColor)?.swatchClassName || ''}`} />
          <Text className='sel-tool-label'>高亮</Text>
        </View>

        <View className='sel-tool-btn' onClick={handleNoteClick}>
          <LucideIcon name='pen-line' size={20} color='currentColor' />
          <Text className='sel-tool-label'>笔记</Text>
        </View>

        <View className='sel-tool-btn sel-tool-btn--has-menu' onClick={handleCopyClick}>
          <LucideIcon name='copy' size={20} color='currentColor' />
          <Text className='sel-tool-label'>复制</Text>
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
