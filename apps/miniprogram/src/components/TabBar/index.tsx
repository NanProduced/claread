import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import LucideIcon from '../LucideIcon'
import './index.scss'

interface TabBarProps {
  current: string;
  onTabChange?: (key: string) => void;
}

export default function TabBar({ current, onTabChange }: TabBarProps) {
  const tabs = [
    { key: 'home', text: '首页', icon: 'home' as const, path: ROUTES.HOME },
    { key: 'history', text: '记录', icon: 'bookOpen' as const, path: ROUTES.HISTORY },
    { key: 'profile', text: '我的', icon: 'smile' as const, path: ROUTES.PROFILE },
  ]

  const switchTab = (tab: typeof tabs[0]) => {
    // 如果提供了 onTabChange (SPA 模式)，则调用它进行组件切换
    if (onTabChange) {
      onTabChange(tab.key)
    } else {
      // 否则回退到物理页面跳转模式 (兼容旧路由)
      Taro.reLaunch({ url: tab.path })
    }
  }

  return (
    <View className='custom-tabbar-container safe-area-bottom' role='tablist'>
      {tabs.map(tab => {
        const isActive = current === tab.key
        return (
          <View
            key={tab.key}
            className={`tab-item ${isActive ? 'active' : ''}`}
            onClick={() => switchTab(tab)}
            role='tab'
            aria-selected={isActive}
            aria-label={tab.text}
          >
            <View className='icon-wrapper'>
              <LucideIcon
                name={tab.icon}
                size={24}
                color={isActive ? 'var(--color-ink)' : 'var(--text-muted)'}
                strokeWidth={isActive ? 2.5 : 2}
              />
            </View>
            <Text className='tab-text'>{tab.text}</Text>
          </View>
        )
      })}
    </View>
  )
}
