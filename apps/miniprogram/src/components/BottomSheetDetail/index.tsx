import { View, Text, ScrollView } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { SentenceEntryModel } from '../../types/view/render-scene.vm'
import { parseMarkdown } from '../../utils/parseMarkdown'
import LucideIcon from '../LucideIcon'
import './index.scss'

interface BottomSheetDetailProps {
  visible: boolean
  entry: SentenceEntryModel | null
  onClose: () => void
  /** 收藏此项回调（Phase 2 将接入真实数据） */
  onFavorite?: (entry: SentenceEntryModel) => void
  /** 有帮助回调（Phase 2 将接入真实数据） */
  onHelpful?: (entry: SentenceEntryModel) => void
}

/**
 * 渲染 Markdown 内容片段
 */
function renderMarkdownSegment(segment: ReturnType<typeof parseMarkdown>[number], index: number) {
  const { text, bold, italic, code, list, quote, header } = segment

  // Extract base classes
  const classes = []
  if (quote) classes.push('quote-segment')
  if (header) classes.push('header-segment')

  const style: Record<string, string> = {}
  if (bold) style.fontWeight = 'bold'
  if (italic) style.fontStyle = 'italic'
  if (code) {
    style.backgroundColor = 'var(--dr-surface)'
    style.padding = '2rpx 8rpx'
    style.borderRadius = '8rpx'
    style.fontFamily = 'var(--font-mono)'
    style.color = 'var(--color-danger)'
  }

  // For spacing, if it's the end of a line (we inserted \n in the parser)
  // Or handle line breaks natively
  return (
    <Text
      key={index}
      className={classes.join(' ')}
      style={Object.keys(style).length > 0 ? style : undefined}
    >
      {text}{list && text.trim() ? '\n' : ''}
    </Text>
  )
}

export default function BottomSheetDetail({ visible, entry, onClose, onFavorite, onHelpful }: BottomSheetDetailProps) {
  if (!visible || !entry) return null

  const contentSegments = parseMarkdown(entry.content)

  const handleFavorite = () => {
    Taro.showToast({ title: '已收藏', icon: 'success', duration: 1500 })
    onFavorite?.(entry)
  }

  const handleHelpful = () => {
    Taro.showToast({ title: '感谢反馈', icon: 'success', duration: 1500 })
    onHelpful?.(entry)
  }

  return (
    <View className='bottom-sheet-overlay' onClick={onClose}>
      <View className='bottom-sheet-container' onClick={(e) => e.stopPropagation()}>
        <View className='sheet-drag-handle' />
        <View className='sheet-header'>
          <View className='header-left'>
            <Text className='sheet-title'>{entry.title || entry.label}</Text>
          </View>
          <View className='sheet-close-btn' onClick={onClose}>
            <LucideIcon name='x' size={24} color='var(--text-muted)' />
          </View>
        </View>

        <ScrollView className='sheet-scroll-content' scrollY>
          <View className='markdown-body'>
            {contentSegments.map(renderMarkdownSegment)}
          </View>
        </ScrollView>

        <View className='sheet-footer-actions safe-area-bottom'>
          <View className='footer-action-btn secondary' onClick={handleFavorite}>
            <LucideIcon name='star' size={18} color='var(--text-sub)' />
            <Text className='btn-text'>收藏此项</Text>
          </View>
          <View className='footer-action-btn primary' onClick={handleHelpful}>
            <LucideIcon name='thumbsUp' size={18} color='var(--color-white)' />
            <Text className='btn-text'>非常有帮助</Text>
          </View>
        </View>
      </View>
    </View>
  )
}
