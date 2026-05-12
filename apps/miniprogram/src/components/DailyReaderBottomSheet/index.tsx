import { View, Text } from '@tarojs/components'
import { memo } from 'react'
import type { DailyReaderHighlight } from '../../types/view/daily-reader.vm'
import './index.scss'

interface Props {
  visible: boolean
  highlight: DailyReaderHighlight | null
  onClose: () => void
  onExpand?: () => void
}

const DailyReaderBottomSheet = memo(function DailyReaderBottomSheet({
  visible,
  highlight,
  onClose,
  onExpand,
}: Props) {
  if (!visible || !highlight) return null

  const typeLabel: Record<string, string> = {
    vocab_highlight: '词汇',
    phrase_gloss: '短语',
    context_gloss: '语境',
  }

  return (
    <View className='daily-sheet-overlay' onClick={onClose}>
      <View className='daily-sheet' onClick={(e) => e.stopPropagation()}>
        <View className='daily-sheet__handle' />
        <View className='daily-sheet__header'>
          <Text className='daily-sheet__word'>{highlight.text}</Text>
          <View className={`daily-sheet__type-badge daily-sheet__type-badge--${highlight.type}`}>
            <Text className='daily-sheet__type-text'>{typeLabel[highlight.type] || '标注'}</Text>
          </View>
        </View>

        <View className='daily-sheet__gloss'>
          <Text className='daily-sheet__gloss-text'>{highlight.gloss}</Text>
        </View>

        {highlight.detail && (
          <View className='daily-sheet__detail'>
            {highlight.detail.phonetic && (
              <Text className='daily-sheet__phonetic'>{highlight.detail.phonetic}</Text>
            )}
            {highlight.detail.pos && (
              <Text className='daily-sheet__pos'>{highlight.detail.pos}</Text>
            )}
            {highlight.detail.contextExplanation && (
              <View className='daily-sheet__context'>
                <Text className='daily-sheet__context-label'>语境释义</Text>
                <Text className='daily-sheet__context-text'>
                  {highlight.detail.contextExplanation}
                </Text>
              </View>
            )}
          </View>
        )}

        <View className='daily-sheet__actions'>
          <View className='daily-sheet__action-btn' onClick={onExpand}>
            <Text className='daily-sheet__action-text'>查看词典详情</Text>
          </View>
        </View>
      </View>
    </View>
  )
})

export default DailyReaderBottomSheet
