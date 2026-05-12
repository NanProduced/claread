import { View, Text } from '@tarojs/components'
import LucideIcon from '../../../components/LucideIcon'
import type { ResultPageState, AnyRenderSceneVm } from '../../../types/view/render-scene.vm'

interface Props {
  pageState: ResultPageState
  sceneData: AnyRenderSceneVm | null
  onRetry: () => void
}

export default function DegradedBanner({ pageState, sceneData, onRetry }: Props) {
  if (pageState !== 'degraded_light' && pageState !== 'degraded_heavy') return null

  const isHeavy = pageState === 'degraded_heavy'
  const isAcademic = sceneData?.schemaVersion === '3.0.0-academic'

  const message = isHeavy
    ? isAcademic
      ? '学术解析未能完整执行，部分术语标注或逻辑分析可能缺失。建议稍后重新解析。'
      : '由于网络环境影响，当前为您呈现的是"极速分析"结果。部分深度解析可能暂不可用。'
    : isAcademic
      ? '学术解析部分节点轻量化运行，术语和逻辑标注已精简，核心内容不受影响。'
      : '分析引擎正在轻量化运行，已为您精选了最重要的解读，细节稍有简化，不影响整体理解。'

  return (
    <View className={`degraded-banner ${isHeavy ? 'heavy' : ''} ${isAcademic ? 'academic' : ''}`}>
      <LucideIcon name='info' size={14} color={isAcademic ? 'var(--term-accent)' : 'var(--color-focus)'} />
      <View className='degraded-banner-content'>
        <Text className='degraded-banner-text'>{message}</Text>
      </View>
      {isHeavy && (
        <View className='degraded-retry-btn' onClick={onRetry}>
          <Text className='degraded-retry-text'>获取深度解析</Text>
        </View>
      )}
    </View>
  )
}
