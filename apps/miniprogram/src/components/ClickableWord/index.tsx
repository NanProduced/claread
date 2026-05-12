import { memo, useEffect, useRef } from 'react'
import { Text } from '@tarojs/components'
import type { CommonEvent } from '@tarojs/components/types/common'
import type { ClickEvent } from '../../types/taro-events'
import './index.scss'

interface ClickableWordProps {
  word: string
  isSaved?: boolean
  savedStatus?: string
  className?: string
  isInSelection?: boolean
  onClick: (word: string, event: ClickEvent) => void
  onLongPress?: (word: string, event: CommonEvent) => void
}

const ClickableWord = memo(function ClickableWord({ word, isSaved, savedStatus, className, isInSelection, onClick, onLongPress }: ClickableWordProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const didLongPressRef = useRef(false)
  const savedClass = isSaved
    ? `saved ${savedStatus === 'mastered' ? 'saved-mastered' : ''}`
    : ''

  const selectionClass = isInSelection ? 'in-selection' : ''
  const clearTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  useEffect(() => clearTimer, [])

  return (
    <Text
      selectable={false}
      className={['clickable-word', className, savedClass, selectionClass].filter(Boolean).join(' ')}
      onClick={(e) => {
        e.stopPropagation()
        if (didLongPressRef.current) {
          didLongPressRef.current = false
          return
        }
        onClick(word, e)
      }}
      onTouchStart={onLongPress ? (e) => {
        didLongPressRef.current = false
        clearTimer()
        timerRef.current = setTimeout(() => {
          didLongPressRef.current = true
          onLongPress(word, e as unknown as CommonEvent)
        }, 420)
      } : undefined}
      onTouchMove={clearTimer}
      onTouchEnd={clearTimer}
      onTouchCancel={clearTimer}
      onLongPress={onLongPress ? (e) => {
        e.stopPropagation()
        clearTimer()
        didLongPressRef.current = true
        onLongPress(word, e)
      } : undefined}
    >
      {word}
    </Text>
  )
})

export default ClickableWord
