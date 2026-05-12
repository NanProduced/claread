import { useState, useEffect, useCallback } from 'react'
import { View, Text, Image } from '@tarojs/components'
import clareadLoadingAnimation from '../../assets/animations/claread-loading'
import fallbackImg from '../../assets/illustrations/loading-analysis-fallback.jpg'
import LottieAnimation from '../LottieAnimation'
import './index.scss'

const LOADING_STEPS = [
  '梳理文章结构',
  '识别关键表达',
  '整理语法线索',
  '生成精读笔记',
  '准备阅读视图'
]

export default function ActiveLoading() {
  const [step, setStep] = useState(0)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    // 延迟 350ms 挂载，彻底避开微信小程序原生 Canvas 在页面推入（Slide-in）时的残影重叠问题
    const timer = setTimeout(() => {
      setMounted(true)
    }, 350)
    return () => clearTimeout(timer)
  }, [])

  const handleLoopComplete = useCallback(() => {
    setStep((s) => (s + 1) % LOADING_STEPS.length)
  }, [])

  if (!mounted) {
    return <View className='active-loading-container' />
  }

  return (
    <View className='active-loading-container'>
      <View className='loading-content'>
        {/* Lottie 动画区域，使用 CSS 入场动画控制首屏展现 */}
        <View className='lottie-wrapper fade-in-scale'>
          <LottieAnimation
            className='analysis-lottie'
            animationData={clareadLoadingAnimation}
            loop={true}
            autoplay={true}
            onLoopComplete={handleLoopComplete}
            fallback={
              <View className='lottie-fallback'>
                <Image src={fallbackImg} className='fallback-image' mode='aspectFit' />
              </View>
            }
          />
        </View>

        {/* 状态文案 - 使用 key 触发优雅的重绘动画 */}
        <View className='status-panel' key={step}>
          <Text className='step-text text-pulse'>{LOADING_STEPS[step]}</Text>
        </View>
      </View>

      <View className='loading-footer fade-in-delayed'>
        <Text className='footer-text'>AI 深度解析中 · 稍候即可阅读</Text>
      </View>
    </View>
  )
}
