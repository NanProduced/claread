import { View, Text } from '@tarojs/components'
import NavBar from '../../components/NavBar'
import { useLayoutStore } from '../../stores/layout'
import './index.scss'

export default function AboutPage() {
  const { navBarHeight } = useLayoutStore()

  return (
    <View className='about-page'>
      <NavBar title='关于我们' showBack />
      <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />
      <View className='content-wrap'>
        <View className='logo-box'>
          <Text className='logo-text'>Claread</Text>
        </View>
        <Text className='title'>Claread透读</Text>
        <Text className='version'>v1.0.0</Text>
        <View className='desc-box'>
          <Text className='desc'>Claread透读是一款基于大语言模型的英语阅读辅助工具，旨在为用户提供沉浸式、个性化的英语精读体验。</Text>
        </View>
      </View>
    </View>
  )
}
