import { Text } from '@tarojs/components'
import { memo } from 'react'
import type { DailyReaderHighlight } from '../../types/view/daily-reader.vm'
import type { ITouchEvent } from '@tarojs/components/types/common'
import './index.scss'

interface Props {
  highlight: DailyReaderHighlight
  displayText?: string
  contextSentence?: string
  isActive?: boolean
  isHintTarget?: boolean
  onWordClick?: (highlight: DailyReaderHighlight, tapPosition: { x: number; y: number }, contextSentence?: string) => void
}

const TYPE_CLASS: Record<string, string> = {
  vocab_highlight: 'daily-hl--vocab',
  phrase_gloss: 'daily-hl--phrase',
  context_gloss: 'daily-hl--context',
}

const DailyReaderHighlightWord = memo(function DailyReaderHighlightWord({
  highlight,
  displayText,
  contextSentence,
  isActive,
  isHintTarget,
  onWordClick,
}: Props) {
  const typeClass = TYPE_CLASS[highlight.type] || 'daily-hl--vocab'

  const handleClick = (e: ITouchEvent) => {
    e.stopPropagation()
    if (!onWordClick) return

    const touch = e.touches?.[0] || e.changedTouches?.[0]
    const position = touch
      ? { x: touch.clientX, y: touch.clientY }
      : { x: 0, y: 0 }

    onWordClick(highlight, position, contextSentence)
  }

  return (
    <Text
      className={`daily-hl ${typeClass} ${isActive ? 'daily-hl--active' : ''} ${isHintTarget ? 'daily-hl--hint' : ''}`}
      onClick={handleClick}
    >
      {displayText || highlight.text}
    </Text>
  )
})

export default DailyReaderHighlightWord
