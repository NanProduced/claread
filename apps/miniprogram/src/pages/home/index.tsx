import { Image, Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import NavBar from '../../components/NavBar'
import TabBar from '../../components/TabBar'
import { useLayoutStore } from '../../stores/layout'
import { useAuthStore } from '../../stores/auth'
import { useDailyReaderStore } from '../../stores/daily-reader'
import { ensureLoggedIn } from '../../services/auth'
import defaultCover from '../../assets/covers/daily-reader-default.jpg'
import emptyDailyReader from '../../assets/illustrations/empty-daily-reader.png'
import './index.scss'

function HomeView() {
  const { navBarHeight } = useLayoutStore()
  const { isLoggedIn, userInfo } = useAuthStore()

  const getGreetingConfig = () => {
    const hour = new Date().getHours()
    if (hour >= 0 && hour < 5) {
      return { main: '夜深了', sub: '还在挑灯夜读吗？阅读之余也请早点休息。' }
    }
    if (hour >= 5 && hour < 11) {
      return { main: '早上好', sub: '又是元气满满的一天，来读点有深度的内容吧。' }
    }
    if (hour >= 11 && hour < 13) {
      return { main: '中午好', sub: '午间的小憩，也可以是心灵的补给。' }
    }
    if (hour >= 13 && hour < 18) {
      return { main: '下午好', sub: '一杯茶，一段文字，享受此刻的静谧。' }
    }
    return { main: '晚上好', sub: '在忙碌的一天结束前，沉浸在文字的呼吸中。' }
  }

  const greeting = getGreetingConfig()

  // 渲染右上角头像/用户图标
  const renderAvatar = () => {
    if (isLoggedIn) {
      const avatarUrl = userInfo?.avatar_url
      const nickname = userInfo?.nickname || ''
      const initial = nickname ? nickname.charAt(0).toUpperCase() : 'U'

      if (avatarUrl) {
        return (
          <Image
            className='user-avatar-img'
            src={avatarUrl}
            mode='aspectFill'
            lazyLoad
            onClick={() => Taro.navigateTo({ url: ROUTES.PROFILE })}
          />
        )
      }
      return (
        <View
          className='user-avatar logged'
          onClick={() => Taro.navigateTo({ url: ROUTES.PROFILE })}
        >
          <Text className='avatar-initial'>{initial}</Text>
        </View>
      )
    }

    // 未登录：显示游客图标，点击触发登录
    return (
      <View
        className='user-avatar guest'
        onClick={async () => {
          const result = await ensureLoggedIn()
          if (result.success && result.isFirstLogin) {
            Taro.navigateTo({ url: ROUTES.PROFILE })
          }
        }}
      >
        <Text className='avatar-guest-icon'>U</Text>
      </View>
    )
  }

  const { latestArticles, fetchLatest } = useDailyReaderStore()

  useDidShow(() => {
    fetchLatest()
  })

  const DIFFICULTY_LABELS: Record<string, string> = {
    A2: 'A2',
    B1: 'B1',
    B2: 'B2',
    C1: 'C1',
  }

  return (
    <View className='home-page'>
      <NavBar title='Claread透读' />
      <View className='nav-placeholder' style={{ height: `${navBarHeight}px` }} />
      <View className='recommendation-list'>

        <View className='header-section'>
          <View className='greeting-row'>
            <Text className='greeting'>{greeting.main}</Text>
            {renderAvatar()}
          </View>
          <Text className='sub-greeting'>{greeting.sub}</Text>
        </View>

        <View className='section-header'>
          <Text className='section-title'>最新精读</Text>
          <Text
            className='section-more'
            onClick={() => Taro.navigateTo({ url: ROUTES.DAILY_READER_ARCHIVE })}
          >更多 →</Text>
        </View>

        <View className='feed-content'>
          {latestArticles.length > 0 ? (
            latestArticles.map((article) => (
              <View
                key={article.id}
                className='feed-card'
                onClick={() => Taro.navigateTo({ url: `${ROUTES.DAILY_READER}?id=${article.id}` })}
              >
                <View className='card-cover-box'>
                  <Image className='card-cover' src={article.coverImageUrl || defaultCover} mode='aspectFill' lazyLoad />
                  <View className='card-badge'>{DIFFICULTY_LABELS[article.difficulty] || article.difficulty}</View>
                </View>
                <View className='card-info'>
                  <Text className='item-title'>{article.title}</Text>
                  <View className='item-meta'>
                    <Text className='meta-text'>{article.source}</Text>
                    <View className='meta-dot' />
                    <Text className='meta-text'>{article.readTimeMinutes} min</Text>
                  </View>
                </View>
              </View>
            ))
          ) : (
            <View className='feed-empty'>
              <Image className='feed-empty-illustration' src={emptyDailyReader} mode='aspectFit' />
              <Text className='feed-empty-title'>精读文章正在准备中</Text>
              <Text className='feed-empty-desc'>精选优质英文文章，带你深度阅读</Text>
            </View>
          )}
        </View>

        <View className='list-footer' />
      </View>
      <TabBar current='home' />
    </View>
  )
}

export default function Home() {
  return <HomeView />
}