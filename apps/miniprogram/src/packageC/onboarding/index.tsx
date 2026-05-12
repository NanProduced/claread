import { useState, useEffect } from 'react'
import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import { useConfigStore, UserPurpose } from '../../stores/config'
import { useLayoutStore } from '../../stores/layout'
import { setNavigatingToOnboarding } from '../../utils/navigationState'
import NavBar from '../../components/NavBar'
import ConfigEditor from '../../components/ConfigEditor'
import './index.scss'

export default function Onboarding() {
  const router = Taro.useRouter()
  const [isReady, setIsReady] = useState(false)
  const { purpose, setPurpose, level, setLevel } = useConfigStore()
  const { navBarHeight } = useLayoutStore()

  useEffect(() => {
    const fromProfile = router.params.from === 'profile'
    const hasConfig = Taro.getStorageSync('user_configured')

    if (hasConfig && !fromProfile) {
      Taro.reLaunch({ url: ROUTES.HOME })
      return
    }
    setIsReady(true)
  }, [router.params.from])

  const finishOnboarding = (selectedPurpose: UserPurpose, selectedLevel: string | null) => {
    setPurpose(selectedPurpose)
    setLevel(selectedLevel)
    Taro.setStorageSync('user_configured', true)
    setNavigatingToOnboarding(false)

    Taro.showToast({ title: '配置已更新', icon: 'success', duration: 1000 })
    setTimeout(() => {
      Taro.reLaunch({ url: ROUTES.HOME })
    }, 1000)
  }

  const skip = () => {
    Taro.setStorageSync('user_configured', true)
    setNavigatingToOnboarding(false)
    Taro.reLaunch({ url: ROUTES.HOME })
  }

  if (!isReady) return null

  return (
    <View className='onboarding-page fade-in'>
      <NavBar
        title='Claread透读'
        renderRight={
          <Text className='skip-btn' onClick={skip}>跳过</Text>
        }
      />
      <View className='nav-spacer' style={{ height: `${navBarHeight}px` }} />

      <View className='content-area'>
        <ConfigEditor
          mode='detailed'
          initialGoal={purpose as UserPurpose}
          initialLevel={level}
          showSubmit
          submitText='开始体验'
          onComplete={(g, l) => finishOnboarding(g as UserPurpose, l)}
        />
      </View>
    </View>
  )
}
