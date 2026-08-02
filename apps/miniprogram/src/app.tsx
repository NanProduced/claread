import { PropsWithChildren, useEffect } from 'react'
import { useAuthStore } from './stores/auth'
import './app.scss'

function App({ children }: PropsWithChildren) {
  // CUTOVER-MINI-LONG: 旧 analysis onboarding、LoginGuideModal、guest trial banner
  // 和空 onAppShow listener 已移除。登录仅由头像/Profile 显式触发。
  // 保留页面通过 useDidShow 自行刷新。
  useEffect(() => {
    useAuthStore.getState().restore()
  }, [])

  return <>{children}</>
}

export default App