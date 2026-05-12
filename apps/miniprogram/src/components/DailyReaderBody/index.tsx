import { View, Text, Image } from '@tarojs/components'
import { memo, useCallback, useState } from 'react'
import type { DailyReaderBody as DailyReaderBodyType, DailyReaderHighlight } from '../../types/view/daily-reader.vm'
import DailyReaderHighlightWord from '../DailyReaderHighlightWord'
import LucideIcon from '../LucideIcon'
import type { ClickEvent } from '../../types/taro-events'
import clareadIcon from '../../assets/brand/claread-icon-fullcolor.png'
import { decodeHtmlEntities } from '../../utils/html-entities'
import './index.scss'

interface Props {
  body: DailyReaderBodyType
  highlights: DailyReaderHighlight[]
  onHighlightClick?: (highlight: DailyReaderHighlight, tapPosition?: { x: number; y: number }, contextSentence?: string) => void
  onWordClick?: (word: string, tapPosition?: { x: number; y: number }) => void
  showHighlightHint?: boolean
}

const DailyReaderBody = memo(function DailyReaderBody({
  body,
  highlights,
  onHighlightClick,
  onWordClick,
  showHighlightHint,
}: Props) {
  const [expandedNotes, setExpandedNotes] = useState<Record<string, boolean>>({})
  const [expandedTranslations, setExpandedTranslations] = useState<Record<string, boolean>>({})

  const handleTextClick = useCallback(
    (e: ClickEvent) => {
      const target = e.target as HTMLElement
      if (!target || !target.dataset?.word) return

      const touch = e.touches?.[0] || e.changedTouches?.[0]
      const position = touch
        ? { x: touch.clientX, y: touch.clientY }
        : undefined

      onWordClick?.(target.dataset.word, position)
    },
    [onWordClick],
  )

  const toggleNote = useCallback((id: string) => {
    setExpandedNotes(prev => ({ ...prev, [id]: !prev[id] }))
  }, [])

  const toggleTranslation = useCallback((id: string) => {
    setExpandedTranslations(prev => ({ ...prev, [id]: !prev[id] }))
  }, [])

  return (
    <View className='daily-body'>
      {body.paragraphs.map((paragraph) => {
        const paraHighlights = highlights.filter(
          (h) => h.paragraphId === paragraph.id,
        )

        const note = paragraph.readingNote
        const translation = paragraph.translation

        const noteExpanded = expandedNotes[paragraph.id]
        const transExpanded = expandedTranslations[paragraph.id]

        return (
          <View key={paragraph.id} className='daily-body__paragraph-container'>
            {note && (
              <View className={`daily-body__strip ${noteExpanded ? 'daily-body__strip--expanded' : ''}`}>
                <View
                  className={`daily-body__strip-header ${noteExpanded ? 'daily-body__strip-header--expanded' : ''}`}
                  onClick={() => toggleNote(paragraph.id)}
                >
                  <View className='daily-body__strip-logo-wrap'>
                    <Image className='daily-body__strip-logo' src={clareadIcon} mode='aspectFit' />
                  </View>
                  <View className='daily-body__strip-main'>
                    <View className='daily-body__strip-label-wrap'>
                      <Text className='daily-body__strip-label'>透读</Text>
                      <Text className='daily-body__strip-kicker'>阅读线索</Text>
                    </View>
                    <Text className={`daily-body__strip-question ${noteExpanded ? 'daily-body__strip-question--expanded' : ''}`}>
                      {note.focusQuestion}
                    </Text>
                  </View>
                  <View className='daily-body__strip-action'>
                    <Text className='daily-body__strip-action-text'>{noteExpanded ? '收起' : '展开'}</Text>
                    <View className={`daily-body__strip-toggle ${noteExpanded ? 'daily-body__strip-toggle--expanded' : ''}`}>
                      <LucideIcon name='ChevronDown' size={16} color='var(--dr-text-muted)' />
                    </View>
                  </View>
                </View>
                {noteExpanded && (
                  <View className='daily-body__strip-content'>
                    <Text className='daily-body__strip-content-label'>线索提示</Text>
                    <Text className='daily-body__strip-content-text'>{note.microSummary}</Text>
                  </View>
                )}
              </View>
            )}

            <View className='daily-body__paragraph'>
              <Text className='daily-body__text' onClick={handleTextClick}>
                {renderParagraphWithHighlights(paragraph.text, paraHighlights, onHighlightClick, showHighlightHint)}
              </Text>
            </View>

            {translation && (
              <View className='daily-body__translation-section'>
                <View className='daily-body__translation-toggle' onClick={() => toggleTranslation(paragraph.id)}>
                  <View className='daily-body__translation-label-wrap'>
                    <LucideIcon name='BookOpen' size={14} color='var(--dr-text-muted)' />
                    <Text className='daily-body__translation-label'>译文</Text>
                  </View>
                  <View className='daily-body__translation-action'>
                    <Text className='daily-body__translation-action-text'>{transExpanded ? '收起' : '展开'}</Text>
                    <LucideIcon name={transExpanded ? 'ChevronUp' : 'ChevronDown'} size={16} color='var(--dr-text-muted)' />
                  </View>
                </View>
                {transExpanded && (
                  <View className='daily-body__translation-box'>
                    <Text className='daily-body__translation-text'>{decodeHtmlEntities(translation)}</Text>
                  </View>
                )}
              </View>
            )}
          </View>
        )
      })}
    </View>
  )
})

function renderParagraphWithHighlights(
  text: string,
  highlights: DailyReaderHighlight[],
  onHighlightClick?: (highlight: DailyReaderHighlight, tapPosition?: { x: number; y: number }, contextSentence?: string) => void,
  showHighlightHint?: boolean,
) {
  if (!highlights.length) return decodeHtmlEntities(text)

  const sorted = [...highlights].sort((a, b) => a.start - b.start)
  const parts: React.ReactNode[] = []
  let lastEnd = 0

  sorted.forEach((hl, idx) => {
    const start = Math.max(0, Math.min(hl.start, text.length))
    const end = Math.max(start, Math.min(hl.end, text.length))
    if (start < lastEnd || end <= start) return

    if (start > lastEnd) {
      parts.push(
        <Text key={`t-${idx}`}>{decodeHtmlEntities(text.slice(lastEnd, start))}</Text>,
      )
    }
    parts.push(
      <DailyReaderHighlightWord
        key={`hl-${hl.id}`}
        highlight={hl}
        displayText={decodeHtmlEntities(text.slice(start, end))}
        contextSentence={extractContextSentence(text, start, end)}
        onWordClick={onHighlightClick}
        isHintTarget={showHighlightHint && idx === 0}
      />,
    )
    lastEnd = end
  })

  if (lastEnd < text.length) {
    parts.push(<Text key='tail'>{decodeHtmlEntities(text.slice(lastEnd))}</Text>)
  }

  return parts
}

function extractContextSentence(text: string, start: number, end: number) {
  const before = text.slice(0, start)
  const after = text.slice(end)
  const left = Math.max(
    before.lastIndexOf('.'),
    before.lastIndexOf('!'),
    before.lastIndexOf('?'),
    before.lastIndexOf(';'),
    before.lastIndexOf('。'),
    before.lastIndexOf('！'),
    before.lastIndexOf('？'),
  )
  const rightCandidates = ['.', '!', '?', ';', '。', '！', '？']
    .map((mark) => after.indexOf(mark))
    .filter((idx) => idx >= 0)
  const right = rightCandidates.length ? Math.min(...rightCandidates) + end + 1 : text.length
  return decodeHtmlEntities(text.slice(left >= 0 ? left + 1 : 0, right).trim())
}

export default DailyReaderBody
