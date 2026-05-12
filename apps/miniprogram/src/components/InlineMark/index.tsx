import { Text } from '@tarojs/components'
import { useEffect, useRef } from 'react'
import { AnyInlineMarkModel, VisualTone, AcademicVisualTone } from '../../types/view/render-scene.vm'
import type { WordClickPayload } from '../ParagraphBlock'
import type { ClickEvent } from '../../types/taro-events'
import type { CommonEvent } from '@tarojs/components/types/common'
import './index.scss'

const TONE_CLASSES: Record<VisualTone | AcademicVisualTone, string> = {
  vocab: 'tone-vocab',
  phrase: 'tone-phrase',
  context: 'tone-context',
  grammar: 'tone-grammar',
  term: 'tone-term',
  logic: 'tone-logic',
}

interface InlineMarkProps {
  mark: AnyInlineMarkModel
  text: string
  isActive?: boolean
  isSaved?: boolean
  savedStatus?: string
  isAcademicMode?: boolean
  isInSelection?: boolean
  userHighlightClass?: string
  onWordClick?: (payload: WordClickPayload) => void
  onLongPress?: (text: string, event: CommonEvent) => void
}

export default function InlineMark({ mark, text, isActive, isSaved, savedStatus, isAcademicMode, isInSelection, userHighlightClass, onWordClick, onLongPress }: InlineMarkProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const didLongPressRef = useRef(false)
  const toneClass = TONE_CLASSES[mark.visualTone]

  const clearTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  useEffect(() => clearTimer, [])

  const handleClick = (e: ClickEvent) => {
    e.stopPropagation()
    if (didLongPressRef.current) {
      didLongPressRef.current = false
      return
    }
    if (mark.clickable && onWordClick) {
      onWordClick({ word: text, mark, event: e })
    }
  }

  const savedClass = isSaved
    ? `saved ${savedStatus === 'mastered' ? 'saved-mastered' : ''}`
    : ''

  const academicClass = isAcademicMode ? 'academic-mode' : ''
  const selectionClass = isInSelection ? 'in-selection' : ''

  return (
    <Text
      selectable={false}
      className={`inline-mark ${mark.renderType} ${toneClass} ${mark.clickable ? 'clickable' : ''} ${isActive ? 'active' : ''} ${savedClass} ${academicClass} ${selectionClass} ${userHighlightClass || ''}`}
      onClick={handleClick}
      onTouchStart={onLongPress ? (e) => {
        didLongPressRef.current = false
        clearTimer()
        timerRef.current = setTimeout(() => {
          didLongPressRef.current = true
          onLongPress(text, e as unknown as CommonEvent)
        }, 420)
      } : undefined}
      onTouchMove={clearTimer}
      onTouchEnd={clearTimer}
      onTouchCancel={clearTimer}
      onLongPress={onLongPress ? (e: CommonEvent) => {
        e.stopPropagation()
        clearTimer()
        didLongPressRef.current = true
        onLongPress(text, e)
      } : undefined}
    >
      {text}
    </Text>
  )
}
