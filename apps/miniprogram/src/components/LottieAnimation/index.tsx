import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Canvas, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import lottie from 'lottie-miniprogram'
import './index.scss'

interface LottieAnimationProps {
  animationData?: unknown
  path?: string
  className?: string
  loop?: boolean | number
  autoplay?: boolean
  fallback?: ReactNode
  onError?: () => void
  onLoopComplete?: () => void
}

type LottieInstance = ReturnType<typeof lottie.loadAnimation>

export default function LottieAnimation({
  animationData,
  path,
  className = '',
  loop = true,
  autoplay = true,
  fallback,
  onError,
  onLoopComplete
}: LottieAnimationProps) {
  const animationRef = useRef<LottieInstance | null>(null)
  const [failed, setFailed] = useState(false)
  const canvasId = useMemo(() => `lottie-${Math.random().toString(36).slice(2, 10)}`, [])

  useEffect(() => {
    let disposed = false

    const destroyAnimation = () => {
      if (animationRef.current) {
        animationRef.current.destroy()
        animationRef.current = null
      }
    }

    const fail = () => {
      if (!disposed) {
        setFailed(true)
        onError?.()
      }
    }

    if (!animationData && !path) {
      fail()
      return destroyAnimation
    }

    Taro.nextTick(() => {
      Taro.createSelectorQuery()
        .select(`#${canvasId}`)
        .fields({ node: true, size: true }, (res) => {
          if (disposed) return

          const canvas = res?.node
          const context = canvas?.getContext?.('2d')
          if (!canvas || !context) {
            fail()
            return
          }

          // 适配高分屏和正确的宽高比例
          const windowInfo = Taro.getWindowInfo()
          const dpr = windowInfo.pixelRatio
          canvas.width = (res.width || 300) * dpr
          canvas.height = (res.height || 300) * dpr
          context.scale(dpr, dpr)

          destroyAnimation()
          lottie.setup(canvas)
          const anim = lottie.loadAnimation({
            renderer: 'canvas',
            loop,
            autoplay,
            animationData,
            path,
            rendererSettings: {
              context,
              clearCanvas: true
            }
          })
          animationRef.current = anim

          if (onLoopComplete) {
            anim.addEventListener('loopComplete', onLoopComplete)
          }
        })
        .exec()
    })

    return () => {
      disposed = true
      destroyAnimation()
    }
  }, [animationData, autoplay, canvasId, loop, onError, path, onLoopComplete])

  if (failed) {
    return fallback ? <>{fallback}</> : null
  }

  return (
    <View className={`lottie-animation ${className}`}>
      <Canvas id={canvasId} canvasId={canvasId} type='2d' className='lottie-animation-canvas' />
    </View>
  )
}
