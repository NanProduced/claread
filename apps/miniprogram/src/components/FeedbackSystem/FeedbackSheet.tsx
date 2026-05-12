import { View, Text, Textarea } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState, useMemo } from 'react'
import { submitFeedback } from '../../services/api/feedback.client'
import { ensureLoggedIn } from '../../services/auth'
import LucideIcon from '../LucideIcon'
import FeedbackOptionGrid from './FeedbackOptionGrid'
import FeedbackSuccessPanel from './FeedbackSuccessPanel'
import { FEEDBACK_CONFIG_BY_SCOPE, FeedbackScope, FeedbackSentiment } from '../../config/feedback'
import './FeedbackSheet.scss'

export interface FeedbackContextPayload {
  targetId: string
  analysisRecordId?: string
  annotationType?: string
  content?: string
  contextJson?: Record<string, unknown>
}

interface FeedbackSheetProps {
  scope: FeedbackScope
  prefillSentiment?: FeedbackSentiment
  prefillType?: string
  contextSummary?: string
  payload: FeedbackContextPayload
  onClose: () => void
}

export default function FeedbackSheet({
  scope,
  prefillSentiment = 'negative',
  prefillType = '',
  contextSummary,
  payload,
  onClose
}: FeedbackSheetProps) {
  const config = FEEDBACK_CONFIG_BY_SCOPE[scope]

  const [selectedType, setSelectedType] = useState<string>(prefillType)
  const [content, setContent] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [successResult, setSuccessResult] = useState<{ isUpsert?: boolean } | null>(null)

  const options = useMemo(() => {
    if (prefillSentiment === 'positive') return config.positiveOptions || []
    if (prefillSentiment === 'negative') return config.negativeOptions || []
    return config.neutralOptions || []
  }, [config, prefillSentiment])

  const isSubmitDisabled = !selectedType || submitting || (config.requiresText && !content.trim())

  const getDisabledReason = () => {
    if (!selectedType) return '请选择反馈类型'
    if (config.requiresText && !content.trim()) return '请补充问题描述'
    return ''
  }

  const handleSubmit = async () => {
    if (isSubmitDisabled) return
    const loginRes = await ensureLoggedIn()
    if (!loginRes.success) return
    setSubmitting(true)
    try {
      // Assuming upsert logic depends on context, backend handles it.
      // We pass the payload through.
      await submitFeedback({
        feedbackScope: scope,
        targetId: payload.targetId,
        analysisRecordId: payload.analysisRecordId,
        sentiment: prefillSentiment,
        feedbackType: selectedType,
        annotationType: payload.annotationType,
        content: content || undefined,
        contextJson: payload.contextJson || {},
      })
      // If we had a way to detect upsert from response we'd pass it, assuming false for now
      setSuccessResult({ isUpsert: false })
    } catch (err) {
      console.error('Feedback submit failed', err)
      Taro.showToast({ title: '反馈提交失败', icon: 'none' })
    } finally {
      setSubmitting(false)
    }
  }

  if (successResult) {
    return (
      <View className='feedback-sheet' onClick={(e) => e.stopPropagation()}>
        <FeedbackSuccessPanel
          inline
          isUpsert={successResult.isUpsert}
          onDismiss={onClose}
          onViewMyFeedback={() => {
            onClose()
            // Navigate to my feedback
            // Taro.navigateTo({ url: '/packageC/feedback/my-feedback' })
          }}
        />
      </View>
    )
  }

  return (
    <View className='feedback-sheet' onClick={(e) => e.stopPropagation()}>
      <View className='feedback-sheet__header'>
        <Text className='feedback-sheet__title'>{config.title}</Text>
        <View className='feedback-sheet__close' onClick={onClose}>
          <LucideIcon name='x' size={20} color='var(--text-muted)' />
        </View>
      </View>

      {contextSummary && (
        <View className='feedback-sheet__context-summary'>
          <Text className='feedback-sheet__context-text' numberOfLines={2}>
            {contextSummary}
          </Text>
        </View>
      )}

      <View className='feedback-sheet__body'>
        <FeedbackOptionGrid
          options={options}
          selectedValues={selectedType ? [selectedType] : []}
          onChange={(vals) => setSelectedType(vals[0] || '')}
        />

        <View className='feedback-sheet__input-wrap'>
          <Textarea
            className='feedback-sheet__input'
            value={content}
            onInput={(e) => setContent(e.detail.value)}
            placeholder={config.placeholder}
            maxlength={500}
            autoHeight
          />
        </View>

        <View className='feedback-sheet__footer'>
          {isSubmitDisabled && !submitting && (
            <Text className='feedback-sheet__disabled-hint'>{getDisabledReason()}</Text>
          )}
          <View
            className={`feedback-sheet__submit ${isSubmitDisabled ? 'feedback-sheet__submit--disabled' : ''}`}
            onClick={handleSubmit}
          >
            {submitting ? '提交中...' : '提交反馈'}
          </View>
        </View>
      </View>
    </View>
  )
}
