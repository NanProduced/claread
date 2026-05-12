import { View, Text, Image } from '@tarojs/components'
import NavBar from '../../../components/NavBar'
import ActiveLoading from '../../../components/ActiveLoading'
import { PAGE_STATE_MESSAGES } from '../utils'
import type { ResultPageState } from '../../../types/view/render-scene.vm'
import stateEmptyImg from '../../../assets/illustrations/state-empty.jpg'
import stateErrorNetworkImg from '../../../assets/illustrations/state-error-network.jpg'
import stateErrorTimeoutImg from '../../../assets/illustrations/state-error-timeout.jpg'
import stateNoCreditImg from '../../../assets/illustrations/state-no-credit.jpg'

function StateIllustration({ src }: { src: string }) {
  return <Image className='state-illustration-img' src={src} mode='aspectFit' />
}

interface Props {
  pageState: ResultPageState
  errorCode: string | null
  errorMsg: string | null
  navBarHeight: number
  onRetry: () => void
}

export default function StateViews({ pageState, errorCode, errorMsg, navBarHeight, onRetry }: Props) {
  const shell = (content: React.ReactNode) => (
    <View className='result-page'>
      <NavBar title='Claread透读' showBack showHome />
      <View style={{ height: navBarHeight + 'px', flexShrink: 0 }} />
      {content}
    </View>
  )

  if (pageState === 'loading') {
    return shell(
      <View className='state-container loading-state'>
        <ActiveLoading />
      </View>
    )
  }

  if (pageState === 'empty') {
    const msg = PAGE_STATE_MESSAGES.empty!
    return shell(
      <View className='state-container'>
        <View className='state-vertical'>
          <StateIllustration src={stateEmptyImg} />
          <Text className='state-title'>{msg.title}</Text>
          <Text className='state-subtitle'>{msg.subtitle}</Text>
        </View>
        <View className='state-cta safe-area-bottom'>
          <View className='btn-primary' onClick={onRetry}>
            <Text className='btn-primary-text'>修改重试</Text>
          </View>
        </View>
      </View>
    )
  }

  if (pageState === 'failed' || pageState === 'timeout' || pageState === 'network_fail') {
    const defaultMsg = PAGE_STATE_MESSAGES[pageState]!
    const title = errorCode === 'INSUFFICIENT_CREDITS' ? '今日积分不足' : defaultMsg.title
    const subtitle = errorCode === 'INSUFFICIENT_CREDITS' ? errorMsg || '您的积分已耗尽，请明天再试' : defaultMsg.subtitle
    const illustrationSrc = errorCode === 'INSUFFICIENT_CREDITS'
      ? stateNoCreditImg
      : pageState === 'timeout'
        ? stateErrorTimeoutImg
        : stateErrorNetworkImg

    return shell(
      <View className='state-container'>
        <View className='state-vertical'>
          <StateIllustration src={illustrationSrc} />
          <Text className='state-title'>{title}</Text>
          <Text className='state-subtitle'>{subtitle}</Text>
        </View>
        <View className='state-cta safe-area-bottom'>
          <View className='btn-primary' onClick={onRetry}>
            <Text className='btn-primary-text'>重新分析</Text>
          </View>
        </View>
      </View>
    )
  }

  return shell(
    <View className='state-container'>
      <View className='state-vertical'>
        <ActiveLoading />
        <Text className='state-title'>正在解析文章...</Text>
        <Text className='state-subtitle-secondary'>首次解析可能需要 20-40 秒，请耐心等待</Text>
      </View>
    </View>
  )
}
