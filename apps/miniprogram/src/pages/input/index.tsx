import { useState, useEffect } from 'react'
import { View, Text, Textarea } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import { useConfigStore, UserPurpose } from '../../stores/config'
import { useArticleStore } from '../../stores/article'
import { useLayoutStore } from '../../stores/layout'
import { saveDraft, getDraft, clearDraft } from '../../services/storage'
import { ensureLoggedIn } from '../../services/auth'
import { track } from '../../services/analytics'
import LucideIcon from '../../components/LucideIcon'
import NavBar from '../../components/NavBar'
import BottomSheetSelect from '../../components/BottomSheetSelect'
import { READING_CONFIG_MAP, getDisplayLabel, getApiParams, getCompactLabel, ReadingGoal } from '../../config/purpose'
import './index.scss'

export default function InputPage() {
  const [content, setContent] = useState('')
  const [isFocused, setIsFocused] = useState(false)
  const [clipboardContent, setClipboardContent] = useState('')
  const [showClipboardBubble, setShowClipboardBubble] = useState(false)
  const [showModeSheet, setShowModeSheet] = useState(false)

  // 从 Store 获取默认配置
  const { purpose, level } = useConfigStore()
  const { navBarHeight } = useLayoutStore()

  // 临时配置状态：默认从 Store 同步，但修改后只影响当前页面
  const [tempConfig, setTempConfig] = useState<{
    purpose: ReadingGoal;
    level: string | null;
  }>({
    purpose: purpose as ReadingGoal,
    level: level
  })

  // 当全局配置改变时，如果当前没有正在输入，则同步到临时配置
  useEffect(() => {
    if (!content) {
      setTempConfig({
        purpose: purpose as ReadingGoal,
        level: level
      })
    }
  }, [purpose, level, content])

  const analyze = useArticleStore((s) => s.analyze)
  const recoverActiveTask = useArticleStore((s) => s.recoverActiveTask)

  // 简单的单词计数
  const wordsCount = content.trim().split(/\s+/).filter(Boolean).length

  // === 剪贴板检测逻辑 ===
  const checkClipboard = async () => {
    try {
      const res = await Taro.getClipboardData()
      const text = res.data?.trim() || ''
      const isEnglish = /[a-zA-Z]{5,}/.test(text)
      if (text.length > 20 && isEnglish && text !== content) {
        setClipboardContent(text)
        setShowClipboardBubble(true)
        const bubbleTimer = setTimeout(() => setShowClipboardBubble(false), 8000)
        return () => clearTimeout(bubbleTimer)
      }
    } catch (e) { console.error("index.tsx:", e) }
  }

  useEffect(() => {
    const draft = getDraft()
    if (draft?.text) setContent(draft.text)
  }, [])

  Taro.useDidShow(() => {
    checkClipboard()
    // 尝试恢复是否有未完成的活跃任务
    recoverActiveTask().then(() => {
      const phase = useArticleStore.getState().phase
      if (phase === 'polling' || phase === 'loading') {
        Taro.navigateTo({ url: ROUTES.RESULT })
      }
    })
  })

  useEffect(() => {
    if (!content) return
    const timer = setTimeout(() => {
      const { reading_goal, reading_variant } = getApiParams(tempConfig.purpose, tempConfig.level)
      saveDraft({
        text: content,
        reading_goal: reading_goal,
        reading_variant: reading_variant,
        savedAt: Date.now(),
      })
    }, 500)
    return () => clearTimeout(timer)
  }, [content, tempConfig])

  const handleBack = () => {
    if (content.trim().length > 0) {
      Taro.showModal({
        title: '离开',
        content: '草稿已自动保存',
        confirmText: '离开',
        cancelText: '继续',
        success: (res) => { if (res.confirm) Taro.navigateBack() },
      })
    } else {
      Taro.navigateBack()
    }
  }

  const handleModeChange = () => {
    setShowModeSheet(true)
  }

  const handleModeSelect = (goal: ReadingGoal, level: string | null) => {
    setTempConfig({ purpose: goal, level })
  }

  const handleSubmit = async () => {
    if (wordsCount < 10) {
      Taro.showToast({ title: '最少输入10个单词', icon: 'none' })
      return
    }

    // 提交任务前先确保登录，避免后端 401
    const loginRes = await ensureLoggedIn()
    if (!loginRes.success) return

    const { reading_goal, reading_variant } = getApiParams(tempConfig.purpose, tempConfig.level)
    track('submit_article', {
      wordCount: wordsCount,
      reading_goal,
      reading_variant,
      is_temporary_config: tempConfig.purpose !== purpose || tempConfig.level !== level
    })
    clearDraft()
    // 重置状态，确保进入 Result 页时一定显示 loading，避免闪现旧结果
    useArticleStore.getState().reset()
    analyze({
      text: content,
      reading_goal: reading_goal,
      reading_variant,
      source_type: 'user_input',
      extended: false,
    })
    // redirectTo 销毁当前页，防止用户通过返回键回到未重置的 Input 页
    Taro.redirectTo({ url: ROUTES.RESULT })
  }

  return (
    <View className={`input-page ${isFocused ? 'is-focused' : ''}`}>
      <NavBar
        title='Claread透读'
        showBack
        onBack={handleBack}
        background='transparent'
      />
      <View className='nav-placeholder' style={{ height: navBarHeight + 'px' }} />

      <View className='canvas-area'>
        {/* 指令栏：模式选择 + 功能快捷键 */}
        <View className='canvas-toolbar'>
          <View className='mode-chip-v2' onClick={handleModeChange}>
            <View className='dot' />
            <Text className='mode-label'>{getCompactLabel(tempConfig.purpose, tempConfig.level)}</Text>
            <LucideIcon name='chevronDown' size={14} color='var(--text-sub)' />
          </View>

          <View className='toolbar-actions'>
            {content && (
              <View className='t-btn clear' onClick={() => setContent('')} role='button' aria-label='清空内容'>
                <LucideIcon name='eraser' size={18} color='var(--text-muted)' />
              </View>
            )}
          </View>
        </View>

        <View className='textarea-wrapper'>
          <Textarea
            className='content-textarea'
            placeholder=''
            placeholderClass='placeholder-style'
            maxlength={10000}
            value={content}
            onInput={(e) => setContent(e.detail.value)}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            autoFocus
            cursorSpacing={100}
          />
          {!content && (
             <View className='empty-guide' onClick={() => setIsFocused(true)}>
               <Text className='guide-title'>输入英文篇章</Text>
               <Text className='guide-subtitle'>在此开始你的深度阅读之旅</Text>
             </View>
          )}

          {/* 弱化后的剪贴板提示 - 改为静默的侧边提示 */}
          {showClipboardBubble && !content && (
            <View className='paste-shortcut' onClick={() => {
              setContent(clipboardContent)
              setShowClipboardBubble(false)
              Taro.showToast({ title: '已注入剪贴板内容', icon: 'none' })
            }}>
              <LucideIcon name='clipboard' size={14} color='var(--text-muted)' />
              <Text>粘贴自剪贴板</Text>
            </View>
          )}
        </View>
      </View>

      {/* 底部动态统计与操作 */}
      <View className='bottom-bar safe-area-bottom'>
        <View className={`interpret-btn ${wordsCount >= 10 ? 'active' : ''}`} onClick={handleSubmit}>
          <View className='btn-content'>
            <Text className='btn-text'>开始透读</Text>
            <View className='btn-divider' />
            <Text className='btn-stats'>{wordsCount} words</Text>
          </View>
          <LucideIcon name='sparkles' size={18} color={wordsCount >= 10 ? 'var(--color-white)' : 'var(--text-muted)'} />
        </View>
      </View>

      {/* 自定义 BottomSheet 选择分析模式 */}
      <BottomSheetSelect
        visible={showModeSheet}
        currentGoal={tempConfig.purpose}
        currentLevel={tempConfig.level}
        onClose={() => setShowModeSheet(false)}
        onSelect={handleModeSelect}
      />
    </View>
  )
}
