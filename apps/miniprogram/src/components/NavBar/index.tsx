import { useState, useEffect } from 'react'
import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import LucideIcon from '../LucideIcon'
import { useLayoutStore } from '../../stores/layout'
import './index.scss'

interface NavBarProps {
  title?: string;
  showBack?: boolean;
  showHome?: boolean;
  background?: string;
  color?: string;
  onBack?: () => void;
  renderRight?: React.ReactNode;
}

export default function NavBar({
  title,
  showBack,
  showHome,
  background = 'var(--bg-color)',
  color = 'var(--color-ink)',
  onBack,
  renderRight
}: NavBarProps) {
  const { setNavHeights } = useLayoutStore()
  const [navStyle, setNavStyle] = useState({
    statusBarHeight: 0,
    navBarHeight: 0,
    capsuleRight: 7
  })

  useEffect(() => {
    const capsule = Taro.getMenuButtonBoundingClientRect()

    const windowInfo = Taro.getWindowInfo()
    const sH = windowInfo.statusBarHeight || 0
    const nH = (capsule.top - sH) * 2 + capsule.height

    setNavStyle({
      statusBarHeight: sH,
      navBarHeight: nH,
      capsuleRight: (windowInfo.screenWidth || 375) - capsule.right,
    })

    setNavHeights(nH + sH, sH)
  }, [setNavHeights])

  const handleBack = () => {
    if (onBack) {
      onBack()
    } else {
      Taro.navigateBack()
    }
  }
  const handleHome = () => { Taro.reLaunch({ url: ROUTES.HOME }) }

  return (
    <View className='custom-navbar-container' style={{ background }}>
      <View style={{ height: navStyle.statusBarHeight + 'px' }} />
      <View className='navbar-content' style={{ height: navStyle.navBarHeight + 'px' }}>
        <View className='navbar-actions' style={{ marginLeft: navStyle.capsuleRight + 'px' }}>
          {showBack && (
            <View
              className='action-btn-wrapper'
              onClick={handleBack}
              role='button'
              aria-label='返回'
            >
              <LucideIcon name='chevronLeft' size={24} color={color} />
            </View>
          )}
          {showHome && (
            <View
              className='action-btn-wrapper'
              onClick={handleHome}
              role='button'
              aria-label='首页'
            >
              <LucideIcon name='home' size={20} color={color} />
            </View>
          )}
        </View>
        <Text className='navbar-title' style={{ color }}>{title}</Text>
        <View className='navbar-right'>
          {renderRight}
        </View>
      </View>
    </View>
  )
}
