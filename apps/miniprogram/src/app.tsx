import { PropsWithChildren, useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { ROUTES } from './config/routes'
import { isNavigatingToOnboarding, setNavigatingToOnboarding } from './utils/navigationState'
import { useAuthStore } from './stores/auth'
import { useArticleStore } from './stores/article'
import { CloudSyncService } from './services/cloudSync.service'
import { ensureLoggedIn } from './services/auth'
import LoginGuideModal from './components/LoginGuideModal'
import './app.scss'

const INTERRUPTED_STATE_KEY = 'analysis_interrupted'
const GUEST_DISMISSED_KEY = 'guest_dismissed'

function App({ children }: PropsWithChildren) {
  const [showLoginGuide, setShowLoginGuide] = useState(false)

  useEffect(() => {
    const restoreState = async () => {
      await useAuthStore.getState().restore()
      if (isNavigatingToOnboarding()) return
      setNavigatingToOnboarding(true)

      const { isLoggedIn } = useAuthStore.getState()

      if (isLoggedIn && !Taro.getStorageSync('user_configured')) {
        Taro.navigateTo({ url: ROUTES.ONBOARDING })
      } else if (!isLoggedIn) {
        // 未登录：检查是否已选择过游客模式（当天不重复弹窗）
        const dismissed = Taro.getStorageSync(GUEST_DISMISSED_KEY)
        const today = new Date().toDateString()
        if (dismissed !== today) {
          setShowLoginGuide(true)
        }
      }
    }
    restoreState()
  }, [])

  const handleLogin = async () => {
    setShowLoginGuide(false)
    // LoginGuideModal 已提供确认 UI，跳过 ensureLoggedIn 中的重复弹窗
    const result = await ensureLoggedIn(true)
    if (result.success && result.isFirstLogin) {
      // 首次登录 → 跳转到 Profile 引导填写头像昵称
      Taro.navigateTo({ url: ROUTES.PROFILE })
    }
  }

  const handleGuestDismiss = () => {
    // 记录当天已选择游客模式，明天再弹
    Taro.setStorageSync(GUEST_DISMISSED_KEY, new Date().toDateString())
    setShowLoginGuide(false)
  }

  // 处理小程序切前台/后台事件
  useEffect(() => {
    // 切后台：保存分析中断状态
    const hideHandler = () => {
      const { phase, recordId } = useArticleStore.getState()
      if ((phase === 'loading' || phase === 'polling') && recordId) {
        try {
          Taro.setStorageSync(INTERRUPTED_STATE_KEY, {
            interruptedAt: Date.now(),
            recordId,
          })
        } catch (e) {
        console.error("app.tsx:", e)
        }
      }
    }

    // 切前台：恢复状态 + 尝试同步 pending 数据
    const showHandler = async (options: { path?: string; scene?: number; referrerInfo?: { appId?: string } }) => {
      if (useAuthStore.getState().isLoggedIn) {
        CloudSyncService.flush()
      }

      // 检查是否分析中断需要恢复
      let interrupted: { interruptedAt: number; recordId: string } | null = null
      try {
        interrupted = Taro.getStorageSync(INTERRUPTED_STATE_KEY)
        Taro.removeStorageSync(INTERRUPTED_STATE_KEY)
      } catch (e) {
      console.error("app.tsx:", e)
      }

      if (!interrupted) return

      const { phase, sceneData, recordId } = useArticleStore.getState()

      // 分析中断后优先尝试恢复活跃任务，而不是直接判失败
      if ((phase === 'loading' || phase === 'polling') && !sceneData && interrupted.recordId === recordId) {
        await useArticleStore.getState().recoverActiveTask(recordId || undefined)
      }
    }

    Taro.onAppHide(hideHandler)
    Taro.onAppShow(showHandler)
    return () => {
      Taro.offAppHide(hideHandler)
      Taro.offAppShow(showHandler)
    }
  }, [])

  return (
    <>
      {children}
      <LoginGuideModal
        visible={showLoginGuide}
        onClose={handleGuestDismiss}
        onLogin={handleLogin}
      />
    </>
  )
}

export default App
