/**
 * 登录引导弹窗
 *
 * 首次打开小程序时显示，说明登录 vs 试用的积分差异。
 * 微信官方合规：不在首次打开时强制要求头像昵称授权，
 * 但登录本身（wx.login）用于获取 openId 实现身份识别是必须的。
 */

import { useState } from 'react'
import { Image, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import clareadLogo from '../../assets/brand/claread-logo.jpg'
import './index.scss'

interface LoginGuideModalProps {
  /** 是否显示弹窗 */
  visible: boolean
  /** 关闭弹窗（选择游客试用） */
  onClose: () => void
  /** 点击微信登录 */
  onLogin: () => Promise<void>
}

export default function LoginGuideModal({ visible, onClose, onLogin }: LoginGuideModalProps) {
  const [loading, setLoading] = useState(false)

  if (!visible) return null

  const handleLogin = async () => {
    if (loading) return
    setLoading(true)
    try {
      await onLogin()
    } finally {
      setLoading(false)
    }
  }

  const handleGuest = () => {
    // 生成 anonymous_id 并存储（用于追踪试用次数）
    let anonymousId = Taro.getStorageSync('anonymous_id')
    if (!anonymousId) {
      anonymousId = `anon_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
      Taro.setStorageSync('anonymous_id', anonymousId)
    }
    onClose()
  }

  return (
    <View className='login-guide-overlay'>
      <View className='login-guide-modal'>
        {/* 顶部图标 */}
        <View className='login-guide-icon'>
          <Image
            className='icon-image'
            src={clareadLogo}
            mode='aspectFit'
            lazyLoad
            fadeIn
          />
        </View>

        {/* 标题 */}
        <Text className='login-guide-title'>欢迎使用 Claread透读</Text>

        {/* 积分说明 */}
        <View className='login-guide-cards'>
          <View className='guide-card guest-card'>
            <View className='guide-card-header'>
              <Text className='guide-card-label'>游客试用</Text>
            </View>
            <Text className='guide-card-desc'>每天 3 次免费试用</Text>
            <Text className='guide-card-note'>无需登录，快速体验</Text>
          </View>

          <View className='guide-card logged-card'>
            <View className='guide-card-header'>
              <Text className='guide-card-label'>微信登录</Text>
              <View className='recommended-badge'>推荐</View>
            </View>
            <Text className='guide-card-desc'>每天 1000 积分</Text>
            <Text className='guide-card-note'>收藏同步，跨设备查看</Text>
          </View>
        </View>

        {/* 按钮 */}
        <View className='login-guide-actions'>
          <View
            className='btn-wechat-login'
            onClick={handleLogin}
          >
            <Text className='btn-text'>{loading ? '登录中...' : '微信登录'}</Text>
          </View>
          <View className='btn-guest-trial' onClick={handleGuest}>
            <Text className='btn-text-ghost'>游客试用</Text>
          </View>
        </View>

        {/* 底部说明 */}
        <Text className='login-guide-footer'>
          登录即表示同意《用户协议》和《隐私政策》
        </Text>
      </View>
    </View>
  )
}
