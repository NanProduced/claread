import { PropsWithChildren, useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { ROUTES } from './config/routes'
import { isNavigatingToOnboarding, setNavigatingToOnboarding } from './utils/navigationState'
import { useAuthStore } from './stores/auth'
import { ensureLoggedIn } from './services/auth'
import LoginGuideModal from './components/LoginGuideModal'
import './app.scss'

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

  // CUTOVER-MINI-LONG: 旧分析主链中断恢复和 CloudSyncService.flush 已移除
  // 保留页面不调用 /analysis-tasks、旧 /records、scene
  useEffect(() => {
    const showHandler = () => {
      // Reserved pages handle their own refresh via useDidShow
    }
    Taro.onAppShow(showHandler)
    return () => {
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
