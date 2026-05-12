import { View } from '@tarojs/components'
import { memo, useState, useEffect } from 'react'
import Taro from '@tarojs/taro'
import './index.scss'

interface Props {
  containerId?: string
}

const DailyReaderProgress = memo(function DailyReaderProgress(_props: Props) {
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    const handleScroll = (res: { scrollTop: number }) => {
      const query = Taro.createSelectorQuery()
      query.select('.daily-page').boundingClientRect()
      query.exec((rects) => {
        if (rects && rects[0]) {
          const node = rects[0] as { scrollHeight?: number; height?: number }
          const scrollHeight = (node.scrollHeight ?? 0) - (node.height ?? 0)
          if (scrollHeight > 0) {
            setProgress(Math.min(100, (res.scrollTop / scrollHeight) * 100))
          }
        }
      })
    }

    Taro.eventCenter.on('dailyReaderPageScroll', handleScroll)
    return () => {
      Taro.eventCenter.off('dailyReaderPageScroll', handleScroll)
    }
  }, [])

  return (
    <View className='daily-progress'>
      <View
        className='daily-progress__bar'
        style={{ width: `${progress}%` }}
      />
    </View>
  )
})

export default DailyReaderProgress
