import { View, Text, Textarea } from '@tarojs/components'
import { memo, useState, useEffect } from 'react'
import LucideIcon from '../LucideIcon'
import './index.scss'

interface Props {
  visible: boolean
  selectionText?: string
  initialColor?: string
  initialNote?: string
  hasExistingAnnotation?: boolean
  onClose: () => void
  onSave: (color: string, note: string) => void
  onHighlightOnly?: (color: string) => void
  onDelete?: () => void
}

const THEMES = [
  { value: 'soft_green', label: '淡绿', color: '#A8D8B9', bg: 'rgba(168, 216, 185, 0.16)' },
  { value: 'soft_blue', label: '淡蓝', color: '#A9CBE8', bg: 'rgba(169, 203, 232, 0.16)' },
  { value: 'soft_purple', label: '淡紫', color: '#C7B9E6', bg: 'rgba(199, 185, 230, 0.15)' },
]

function normalizeInitialColor(color?: string): string {
  if (color === 'soft_blue') return 'soft_blue'
  if (color === 'soft_purple') return 'soft_purple'
  return 'soft_green'
}

const UserNoteSheet = memo(function UserNoteSheet({
  visible,
  selectionText,
  initialColor = 'soft_green',
  initialNote = '',
  hasExistingAnnotation = false,
  onClose,
  onSave,
  onHighlightOnly,
  onDelete
}: Props) {
  const [color, setColor] = useState(initialColor)
  const [note, setNote] = useState(initialNote)

  useEffect(() => {
    if (visible) {
      setColor(normalizeInitialColor(initialColor))
      setNote(initialNote)
    }
  }, [visible, initialColor, initialNote])

  if (!visible) return null

  const handleSave = () => {
    onSave(color, note.trim())
  }

  const handleHighlightOnly = () => {
    onHighlightOnly?.(color)
  }

  const activeTheme = THEMES.find(t => t.value === color) || THEMES[0]

  return (
    <View className='un-overlay' onClick={onClose}>
      <View className='un-sheet' onClick={e => e.stopPropagation()}>
        <View className='un-handle' />

        <View className='un-body'>
          {selectionText && (
            <View className='un-selection-preview' style={{ backgroundColor: activeTheme.bg }}>
              <Text className='un-selection-text' numberOfLines={3}>
                {selectionText}
              </Text>
            </View>
          )}

          <View className='un-annotation-bar'>
            <View className='un-color-strip'>
              {THEMES.map(t => (
                <View
                  key={t.value}
                  className={`un-color-dot ${color === t.value ? 'un-color-dot--active' : ''}`}
                  style={{ backgroundColor: t.color }}
                  onClick={() => setColor(t.value)}
                >
                  {color === t.value && <LucideIcon name='check' size={13} color='rgba(36, 33, 28, 0.62)' />}
                </View>
              ))}
            </View>

            {onHighlightOnly && (
              <View className='un-bar-action' onClick={handleHighlightOnly}>
                <LucideIcon name='highlighter' size={17} color='currentColor' />
                <Text className='un-bar-action-text'>{hasExistingAnnotation && !note.trim() ? '更新高亮' : '仅高亮'}</Text>
              </View>
            )}

            {onDelete && (
              <View className='un-bar-action un-bar-action--danger' onClick={onDelete}>
                <LucideIcon name='trash2' size={17} color='currentColor' />
                <Text className='un-bar-action-text'>{note.trim() ? '删除' : '取消'}</Text>
              </View>
            )}
          </View>

          <Textarea
            className='un-textarea'
            placeholder='写下你的想法...'
            placeholderClass='un-textarea-placeholder'
            value={note}
            onInput={e => setNote(e.detail.value)}
            maxlength={500}
          />

          <View className='un-actions-row'>
            <View className='un-btn un-btn--primary' onClick={handleSave}>
              <Text className='un-btn-text un-btn-text--primary'>{hasExistingAnnotation ? '保存修改' : '保存笔记'}</Text>
            </View>
          </View>
        </View>
      </View>
    </View>
  )
})

export default UserNoteSheet
