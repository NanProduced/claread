import { useState } from 'react'
import { View, Text, ScrollView } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import type { AcademicInlineGlossary } from '../../types/view/render-scene.vm'
import './index.scss'

interface AcademicTermSheetProps {
  visible: boolean
  term: string  // 原文术语
  glossary?: AcademicInlineGlossary | null
  contextSentence?: string  // 可选的上下文句子
  onClose: () => void
  onFeedback?: () => void
}

export default function AcademicTermSheet({
  visible,
  term,
  glossary,
  contextSentence,
  onClose,
  onFeedback,
}: AcademicTermSheetProps) {
  const [showContext, setShowContext] = useState(true)

  if (!visible) return null

  const hasContextDef = glossary?.contextDefinition && glossary.contextDefinition.trim().length > 0
  const hasCategory = glossary?.termCategory && glossary.termCategory.trim().length > 0
  const isUncertain = glossary?.zhUncertain === true

  return (
    <View className='academic-term-sheet-overlay' onClick={onClose}>
      <View className='academic-term-sheet' onClick={(e) => e.stopPropagation()}>
        {/* Header - 术语标题 */}
        <View className='term-sheet-header'>
          <Text className='term-title'>{term}</Text>
          <View className='term-close-btn' onClick={onClose}>
            <LucideIcon name='x' size={18} color='var(--reader-muted)' strokeWidth={1.5} />
          </View>
        </View>

        {/* Category Tag - 如果有 */}
        {hasCategory && (
          <View className='term-category-tag'>
            <Text className='category-text'>{glossary!.termCategory}</Text>
          </View>
        )}

        {/* Content */}
        <ScrollView className='term-sheet-content' scrollY enhanced showScrollbar={false}>
          {/* Uncertainty Notice */}
          {isUncertain && (
            <View className='uncertainty-notice'>
              <LucideIcon name='alert-circle' size={14} color='var(--academic-warning-text)' />
              <Text className='uncertainty-text'>术语翻译可能因领域不同而有差异</Text>
            </View>
          )}

          {/* Chinese Translation */}
          {glossary?.zh && (
            <View className='term-section'>
              <Text className='section-label'>中文译名</Text>
              <Text className={`term-zh ${isUncertain ? 'is-uncertain' : ''}`}>
                {glossary.zh}
              </Text>
            </View>
          )}

          {/* Context Definition (本文语境) - 优先展示 */}
          {hasContextDef && (
            <View className='term-section context-definition'>
              <View className='section-header' onClick={() => setShowContext(!showContext)}>
                <Text className='section-label'>本文语境</Text>
                <LucideIcon
                  name={showContext ? 'chevron-up' : 'chevron-down'}
                  size={14}
                  color='var(--reader-muted)'
                />
              </View>
              {showContext && (
                <Text className='context-text'>{glossary!.contextDefinition}</Text>
              )}
            </View>
          )}

          {/* Context Sentence (可选) */}
          {contextSentence && (
            <View className='term-section'>
              <Text className='section-label'>例句</Text>
              <Text className='context-sentence'>{contextSentence}</Text>
            </View>
          )}
        </ScrollView>

        {/* Footer */}
        <View className='term-sheet-footer'>
          <View className='feedback-btn' onClick={onFeedback}>
            <LucideIcon name='message-square' size={14} color='var(--reader-muted)' />
            <Text className='feedback-text'>写反馈</Text>
          </View>
        </View>
      </View>
    </View>
  )
}
