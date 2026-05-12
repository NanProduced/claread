import { View, Text, Textarea } from '@tarojs/components'
import { useState } from 'react'
import Taro from '@tarojs/taro'
import { submitFeedback } from '../../services/api/feedback.client'
import { ensureLoggedIn } from '../../services/auth'
import NavBar from '../../components/NavBar'
import { useLayoutStore } from '../../stores/layout'
import FeedbackOptionGrid from '../../components/FeedbackSystem/FeedbackOptionGrid'
import FeedbackSuccessPanel from '../../components/FeedbackSystem/FeedbackSuccessPanel'
import { FEEDBACK_CONFIG_BY_SCOPE } from '../../config/feedback'
import './index.scss'

const config = FEEDBACK_CONFIG_BY_SCOPE.app
const MAX_CONTENT_LENGTH = 300

export default function FeedbackPage() {
  const [selectedCategory, setSelectedCategory] = useState('')
  const [content, setContent] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const { navBarHeight } = useLayoutStore()

  const isSubmitDisabled = !selectedCategory || !content.trim()
  const isFormReady = selectedCategory && content.trim().length > 5

  const handleSubmit = async () => {
    if (isSubmitDisabled || submitting) return
    const loginRes = await ensureLoggedIn()
    if (!loginRes.success) return
    setSubmitting(true)
    try {
      await submitFeedback({
        feedbackScope: 'app',
        targetId: selectedCategory,
        sentiment: 'neutral',
        feedbackType: selectedCategory,
        content: content.trim(),
        contextJson: { app_area: selectedCategory },
      })
      setSubmitted(true)
    } catch {
      Taro.showToast({ title: '提交失败，请稍后重试', icon: 'none', duration: 2000 })
    } finally {
      setSubmitting(false)
    }
  }

  const resetForm = () => {
    setSelectedCategory('')
    setContent('')
    setSubmitted(false)
  }

  return (
    <View className='feedback-page'>
      <NavBar
        title='意见反馈'
        showBack
        background='var(--reader-paper, #FAF9F6)'
        color='var(--text-primary, #111111)'
      />
      <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />

      <View className='feedback-page__content'>
        {submitted ? (
          <View className='feedback-page__success-container'>
            <FeedbackSuccessPanel inline onDismiss={resetForm} onViewMyFeedback={() => Taro.navigateBack()} />
          </View>
        ) : (
          <>
            <View className='feedback-page__header'>
              <View className='feedback-page__header-text'>
                <Text className='feedback-page__title'>告诉我们你的想法</Text>
                <Text className='feedback-page__subtitle'>遇到问题、发现不准确，或有一个小建议，都可以留在这里。</Text>
              </View>
              <View className={`feedback-page__mascot ${selectedCategory ? 'feedback-page__mascot--happy' : ''}`}>
                <View className='doc-face' />
                <View className='eye-left' />
                <View className='eye-right' />
                <View className='smile' />
              </View>
            </View>

            <FeedbackOptionGrid
              options={config.neutralOptions || []}
              selectedValues={selectedCategory ? [selectedCategory] : []}
              onChange={(vals) => setSelectedCategory(vals[0] || '')}
            />

            <View className='feedback-page__input-wrap'>
              <Textarea
                id='feedback-content'
                className='feedback-page__textarea'
                value={content}
                onInput={(e) => setContent(e.detail.value)}
                placeholder={'比如：在哪个页面遇到？你原本期待什么？'}
                maxlength={MAX_CONTENT_LENGTH}
                autoHeight
              />
              <Text className={`feedback-page__char-count ${content.length > 0 ? 'feedback-page__char-count--active' : ''}`}>{content.length}/{MAX_CONTENT_LENGTH}</Text>
            </View>

            <View className='feedback-page__submit-wrap'>
              <View
                className={`feedback-page__submit ${isSubmitDisabled ? 'feedback-page__submit--disabled' : ''} ${!isSubmitDisabled && isFormReady ? 'feedback-page__submit--ready' : ''}`}
                onClick={handleSubmit}
              >
                <Text>{submitting ? '提交中...' : '提交反馈'}</Text>
              </View>
            </View>
          </>
        )}

        <View className='feedback-page__footer-note'>
          <Text>可在「我的 → 我的反馈记录」中查看处理结果</Text>
        </View>
      </View>
    </View>
  )
}
