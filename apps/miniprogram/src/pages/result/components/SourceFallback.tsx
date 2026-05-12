import { View, Text, ScrollView } from '@tarojs/components'
import LucideIcon from '../../../components/LucideIcon'
import NavBar from '../../../components/NavBar'
import { splitSourceParagraphs } from '../utils'
import DegradedBanner from './DegradedBanner'
import type { ResultPageState, AnyRenderSceneVm } from '../../../types/view/render-scene.vm'

interface Props {
  pageState: ResultPageState
  sceneData: AnyRenderSceneVm | null
  requestText: string | undefined
  isReplayMode: boolean
  navBarHeight: number
  onRetry: () => void
}

export default function SourceFallback({ pageState, sceneData, requestText, isReplayMode, navBarHeight, onRetry }: Props) {
  const sourceParagraphs = splitSourceParagraphs(requestText || '')
  const isDegraded = pageState === 'degraded_light' || pageState === 'degraded_heavy'
  const title = isDegraded ? '本次解析未完成' : '未生成可渲染内容'
  const subtitle = isDegraded
    ? '部分分析节点执行失败，结构化结果未能生成。已为您回退展示原文，建议稍后重新解析。'
    : '当前记录没有生成可展示的结构化结果，建议调整原文后重试。'

  return (
    <View className='result-page'>
      <NavBar title='Claread透读' showBack showHome />
      <View style={{ height: navBarHeight + 'px', flexShrink: 0 }} />
      <DegradedBanner pageState={pageState} sceneData={sceneData} onRetry={onRetry} />
      <ScrollView className='article-scroll' scrollY enhanced showScrollbar={false}>
        <View className='article-container fallback-article-container'>
          <View className='fallback-panel'>
            <Text className='fallback-title'>{title}</Text>
            <Text className='fallback-subtitle'>{subtitle}</Text>
          </View>

          {sourceParagraphs.length > 0 && (
            <View className='fallback-source-card'>
              <Text className='fallback-source-label'>原文回退</Text>
              {sourceParagraphs.map((paragraph, idx) => (
                <Text key={`fallback-${idx}`} className='fallback-source-paragraph'>
                  {paragraph}
                </Text>
              ))}
            </View>
          )}

          <View className='article-end-actions'>
            <View className='end-btn-primary' onClick={onRetry}>
              <LucideIcon name='plus' size={18} color='var(--color-white)' />
              <Text>{isReplayMode ? '重新解析这篇' : '再分析一篇'}</Text>
            </View>
          </View>
          <View className='bottom-spacer' />
        </View>
      </ScrollView>
    </View>
  )
}
