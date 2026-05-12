import { View, Text, ScrollView } from '@tarojs/components'
import NavBar from '../../components/NavBar'
import { useLayoutStore } from '../../stores/layout'
import './index.scss'

export default function AgreementPage() {
  const { navBarHeight } = useLayoutStore()

  return (
    <View className='agreement-page'>
      <NavBar title='用户协议与隐私政策' showBack />
      <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />
      <ScrollView scrollY className='content-scroll'>
        <View className='content-wrap'>
          <Text className='title'>用户协议与隐私政策</Text>
          <Text className='date'>最近更新时间：2026年4月</Text>
          <Text className='paragraph'>欢迎使用 Claread透读（以下简称“本产品”）。我们非常重视您的隐私保护和个人信息安全。本政策将帮助您了解我们如何收集、使用和保护您的个人信息。</Text>
          <Text className='h2'>1. 信息收集与使用</Text>
          <Text className='paragraph'>为了向您提供更好的阅读与生词管理服务，我们可能会收集您的微信号或匿名设备标识，以及您的阅读记录和生词本数据。这些数据仅用于本产品的核心服务。</Text>
          <Text className='h2'>2. 数据安全</Text>
          <Text className='paragraph'>我们将采取合理可行的安全防护措施，保护您的个人信息不被未经授权的访问、使用或泄露。</Text>
          <Text className='h2'>3. 服务变更与免责声明</Text>
          <Text className='paragraph'>本产品依赖大模型技术，解读结果仅供参考，不保证100%准确性。我们保留随时修改或中断服务的权利。</Text>
        </View>
      </ScrollView>
    </View>
  )
}
