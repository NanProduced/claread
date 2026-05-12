import { View, Text } from '@tarojs/components'
import { useState } from 'react'
import { submitFeedback } from '../../services/api/feedback.client'
import { ensureLoggedIn } from '../../services/auth'
import LucideIcon from '../LucideIcon'
import FeedbackSheet from '../FeedbackSystem/FeedbackSheet'
import Taro from '@tarojs/taro'
import './index.scss'

export interface FeedbackWidgetProps {
  recordId: string
  cloudId?: string
  readingGoal?: string
  readingVariant?: string
  userFacingState?: string
  sourceTextLength?: number
  annotationCount?: Record<string, number>
}

export default function FeedbackWidget({
  recordId,
  cloudId,
  readingGoal,
  readingVariant,
  userFacingState,
  sourceTextLength,
  annotationCount,
}: FeedbackWidgetProps) {
  const [submittedPositive, setSubmittedPositive] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [sheetState, setSheetState] = useState<{ visible: boolean, prefill?: 'negative' | 'neutral' }>({ visible: false })

  const contextJson = {
    reading_goal: readingGoal,
    reading_variant: readingVariant,
    user_facing_state: userFacingState,
    source_text_length: sourceTextLength,
    annotation_count: annotationCount,
  }

  const handleThumbsUp = async () => {
    if (submitting || submittedPositive) return
    const loginRes = await ensureLoggedIn()
    if (!loginRes.success) return
    setSubmitting(true)
    try {
      await submitFeedback({
        feedbackScope: 'analysis_result',
        targetId: cloudId || recordId,
        analysisRecordId: cloudId || recordId,
        sentiment: 'positive',
        feedbackType: 'thumbs_up',
        contextJson,
      })
      setSubmittedPositive(true)
      Taro.showToast({ title: '感谢反馈', icon: 'success', duration: 1200 })
    } catch {
      Taro.showToast({ title: '提交失败', icon: 'none', duration: 1200 })
    } finally {
      setSubmitting(false)
    }
  }

  const handleInaccurate = () => {
    setSheetState({ visible: true, prefill: 'negative' })
  }

  const handleWriteFeedback = () => {
    setSheetState({ visible: true, prefill: 'neutral' })
  }

  return (
    <>
      <View className='feedback-widget'>
        <View className='feedback-widget__divider' />
        <Text className='feedback-widget__title'>本次解读对你有帮助吗？</Text>

        <View className='feedback-widget__actions'>
          <View
            className={`feedback-widget__btn ${submittedPositive ? 'is-active' : ''}`}
            onClick={handleThumbsUp}
          >
            <View className='icon-wrap'>
              <LucideIcon
                name='thumbsUp'
                size={36}
                strokeWidth={2}
                color={submittedPositive ? 'var(--color-success, #16A34A)' : 'var(--reader-muted, #7A7D86)'}
              />
            </View>
            <Text>{submittedPositive ? '已感谢' : '有帮助'}</Text>
          </View>

          <View
            className='feedback-widget__btn'
            onClick={handleInaccurate}
          >
            <View className='icon-wrap'>
              <LucideIcon
                name='thumbsDown'
                size={36}
                strokeWidth={2}
                color='var(--reader-muted, #7A7D86)'
              />
            </View>
            <Text>不准确</Text>
          </View>

          <View
            className='feedback-widget__btn'
            onClick={handleWriteFeedback}
          >
            <View className='icon-wrap'>
              <LucideIcon
                name='messageSquare'
                size={36}
                strokeWidth={2}
                color='var(--reader-muted, #7A7D86)'
              />
            </View>
            <Text>写反馈</Text>
          </View>
        </View>
      </View>

      {sheetState.visible && (
        <View className='popup-feedback-overlay' onClick={() => setSheetState({ visible: false })}>
          <FeedbackSheet
            scope='analysis_result'
            prefillSentiment={sheetState.prefill === 'neutral' ? undefined : sheetState.prefill}
            payload={{
              targetId: cloudId || recordId,
              analysisRecordId: cloudId || recordId,
              contextJson,
            }}
            contextSummary='关于本次整篇文章的解读结果'
            onClose={() => setSheetState({ visible: false })}
          />
        </View>
      )}
    </>
  )
}
