/**
 * 个人中心页
 *
 * 展示用户信息、登录/登出入口，
 * 以及学习统计和设置入口。
 */

import { View, Text, ScrollView, Image, Button, Input } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { ROUTES } from '../../config/routes'
import { setNavigatingToOnboarding } from '../../utils/navigationState'
import type { ChooseAvatarEvent, InputEvent } from '../../types/taro-events'

interface MenuItem {
  label: string
  value?: string
  icon: string
  url?: string
  onClick?: () => void
  color: string
}
import { useState, useEffect, useCallback, useRef } from 'react'
import { useConfigStore } from '../../stores/config'
import { useAuthStore } from '../../stores/auth'
import { ensureLoggedIn } from '../../services/auth'
import { getAllRecords, getVocabulary, getVocabCount } from '../../services/storage'
import { fetchCloudRecords } from '../../services/api/records.client'
import { fetchCloudVocabulary } from '../../services/api/vocabulary.client'
import type { VocabEntry } from '../../types/view/vocabulary.vm'
import { fetchUserQuota, updateProfile } from '../../services/api/client'
import NavBar from '../../components/NavBar'
import TabBar from '../../components/TabBar'
import LucideIcon from '../../components/LucideIcon'
import CenterModal from '../../components/CenterModal'
import ConfigEditor from '../../components/ConfigEditor'
import { useLayoutStore } from '../../stores/layout'
import { getDisplayLabel, getStandardLabel, ReadingGoal } from '../../config/purpose'
import { getReadingTier, getAllTiers } from '../../utils/achievement'
import './index.scss'

interface ProfilePageProps {
  isSubView?: boolean
}

export default function ProfilePage({ isSubView = false }: ProfilePageProps) {
  const { purpose, level, setPurpose, setLevel } = useConfigStore()
  const { navBarHeight } = useLayoutStore()
  const { isLoggedIn, userInfo, logout, fetchUserInfo, updateUserInfo } = useAuthStore()
  const [articleCount, setArticleCount] = useState(0)
  const [wordCount, setWordCount] = useState(0)
  const [quota, setQuota] = useState<{ remaining: number, dailyFree: number, bonus: number } | null>(null)
  const [loadingStats, setLoadingStats] = useState(false)
  const [showModeSheet, setShowModeSheet] = useState(false)
  const [showAchievementSheet, setShowAchievementSheet] = useState(false)

  const allTiers = getAllTiers()
  const tier = getReadingTier(articleCount)
  // 昵称更新防抖定时器
  const nicknameTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /**
   * 加载统计数据。
   */
  const loadStats = useCallback(async () => {
    setLoadingStats(true)
    if (isLoggedIn) {
      try {
        await fetchUserInfo().catch(() => {})

        const [vocabResult, quotaResult, recordResult] = await Promise.all([
          fetchCloudVocabulary(1, 1).catch(() => ({ total: 0, items: [] as VocabEntry[] })),
          fetchUserQuota().catch(() => null),
          fetchCloudRecords(1, 1).catch(() => ({ total: 0 })),
        ])

        const latestInfo = useAuthStore.getState().userInfo
        setArticleCount(latestInfo?.cumulativeArticleCount ?? recordResult.total ?? 0)

        const localVocab = getVocabulary()
        const cloudTotal = vocabResult.total
        const localCount = localVocab.filter(v => !v.tombstone && v.pendingOp !== 'delete').length
        setWordCount(Math.max(cloudTotal, localCount))

        if (quotaResult) {
          setQuota({
            remaining: quotaResult.remaining_points,
            dailyFree: quotaResult.daily_free_points,
            bonus: quotaResult.bonus_points || 0
          })
        }
      } catch {
        const records = getAllRecords()
        setArticleCount(records.length)
        setWordCount(getVocabCount())
      }
    } else {
      const records = getAllRecords()
      setArticleCount(records.length)
      setWordCount(getVocabCount())
      setQuota(null)
    }
    setLoadingStats(false)
  }, [isLoggedIn])

  useEffect(() => {
    loadStats()
  }, [isLoggedIn, loadStats])

  useDidShow(loadStats)

  const handleLogin = async () => {
    setNavigatingToOnboarding(true)
    const result = await ensureLoggedIn()
    if (result.success && result.isFirstLogin) {
      Taro.navigateTo({ url: ROUTES.ONBOARDING })
    }
  }

  const handleLogout = () => {
    Taro.showModal({
      title: '确认退出登录？',
      content: '退出后，您的阅读进度和生词本将保留在本地。',
      confirmText: '退出登录',
      confirmColor: '#ef4444',
      success: (res) => { if (res.confirm) logout() }
    })
  }

  const onChooseAvatar = (e: ChooseAvatarEvent) => {
    const { avatarUrl } = e.detail
    updateUserInfo({ avatar_url: avatarUrl })
    updateProfile({ avatar_url: avatarUrl }).catch(() => {
      Taro.showToast({ title: '头像保存失败', icon: 'error' })
    })
  }

  const onNicknameChange = (e: InputEvent) => {
    const nickname = e.detail.value
    updateUserInfo({ nickname })
    if (nicknameTimerRef.current) clearTimeout(nicknameTimerRef.current)
    nicknameTimerRef.current = setTimeout(() => {
      updateProfile({ nickname }).catch(() => {
        Taro.showToast({ title: '保存失败', icon: 'error' })
      })
    }, 300)
  }

  const handleModeSelect = (g: ReadingGoal, l: string | null) => {
    setPurpose(g)
    setLevel(l)
    Taro.showToast({ title: '默认配置已更新', icon: 'success' })
  }

  const menuGroups: { title: string; items: MenuItem[] }[] = [
    {
      title: "学习管理",
      items: [
        {
          label: "当前模式配置",
          value: getStandardLabel(purpose as string, level),
          icon: 'settings',
          onClick: () => setShowModeSheet(true),
          color: 'blue',
        },
        {
          label: "我的生词本",
          value: wordCount > 0 ? `${wordCount}词` : "暂无生词",
          icon: 'bookmark',
          url: ROUTES.VOCAB,
          color: 'yellow',
        },
      ]
    },
    {
      title: "反馈与帮助",
      items: [
        {
          label: "我的反馈记录",
          icon: 'inbox',
          url: ROUTES.FEEDBACK_MY,
          color: 'blue',
        },
        {
          label: "意见反馈",
          icon: 'messageSquare',
          url: ROUTES.FEEDBACK,
          color: 'green',
        },
      ]
    },
    {
      title: "关于与合规",
      items: [
        { label: "用户协议与隐私政策", icon: 'file', url: ROUTES.AGREEMENT, color: 'gray' },
        { label: "关于我们", icon: 'info', url: ROUTES.ABOUT, color: 'gray' },
      ]
    }
  ]

  const handleMenuClick = (item: MenuItem) => {
    if (item.onClick) item.onClick()
    else if (item.url) Taro.navigateTo({ url: item.url })
  }

  const displayName = userInfo?.nickname || (isLoggedIn ? `用户 ${(userInfo?.user_id || '').slice(0, 8)}` : '未登录')

  return (
    <View className={`profile-page ${isSubView ? 'sub-view' : ''}`}>
      {!isSubView && <NavBar title='我的' />}
      {!isSubView && <View className='nav-spacer' style={{ height: navBarHeight + 'px' }} />}
      <ScrollView scrollY className='profile-scroll'>

        {/* User Card */}
        <View className='user-card'>
          <View className='user-profile-section'>
            <Button
              className='avatar-btn'
              openType={isLoggedIn ? 'chooseAvatar' : undefined}
              onChooseAvatar={isLoggedIn ? onChooseAvatar : undefined}
              onClick={!isLoggedIn ? handleLogin : undefined}
              aria-label={isLoggedIn ? '修改头像' : '点击登录'}
            >
              {userInfo?.avatar_url ? (
                <Image className='avatar-img' src={userInfo.avatar_url} mode='aspectFill' lazyLoad />
              ) : (
                <LucideIcon name='user' size={64} color={isLoggedIn ? 'var(--color-white)' : 'var(--text-muted)'} />
              )}
            </Button>

            <View className='user-info'>
              {isLoggedIn ? (
                <>
                  <View className='identity-tag'>
                    <Text className='tag-text'>{tier.cnTitle}</Text>
                  </View>
                  <View className='nickname-wrapper'>
                    <Input
                      className='nickname-input'
                      type='nickname'
                      value={userInfo?.nickname || ''}
                      placeholder='点击设置昵称'
                      placeholderStyle='color: var(--text-muted); font-weight: 500;'
                      maxlength={20}
                      onInput={onNicknameChange}
                      onBlur={onNicknameChange}
                      onConfirm={onNicknameChange}
                    />
                    <View className='edit-icon-box' aria-label='修改昵称'>
                      <LucideIcon name='pencil' size={24} color='currentColor' />
                    </View>
                  </View>
                </>
              ) : (
                <View className='nickname-wrapper' onClick={handleLogin}>
                  <Text className='nickname'>点击登录微信</Text>
                  <Text className='login-subtitle'>登录后可同步数据到云端</Text>
                </View>
              )}
            </View>

            {isLoggedIn && (
              <View
                className='logout-icon-btn'
                onClick={handleLogout}
                aria-label='退出登录'
              >
                <LucideIcon name='logOut' size={40} color='var(--text-muted)' />
              </View>
            )}
          </View>

          {/* Account Metrics Portfolio */}
          <View className='stats-dashboard' onClick={() => Taro.navigateTo({ url: ROUTES.CREDIT_DETAIL })}>
            <View className='dashboard-header'>
              <View className='panel-label'>
                <LucideIcon name='ticket' size={28} color='var(--text-muted)' />
                <Text className='label-text'>额度明细</Text>
              </View>
              <View className='total-preview'>
                <Text className='label'>当前可用</Text>
                <Text className={`value ${loadingStats ? 'is-loading' : ''}`}>
                  {loadingStats ? '同步中' : ((quota?.remaining ?? 0) + (quota?.bonus ?? 0))}
                </Text>
              </View>
            </View>

            <View className='credits-list'>
              <View className='credit-item'>
                <View className='item-info'>
                  <LucideIcon name='calendar' size={28} color='var(--text-muted)' />
                  <View className='text-group'>
                    <Text className='title'>每日常规额度</Text>
                    <Text className='subtitle'>每日 00:00 自动刷新</Text>
                  </View>
                </View>
                <Text className='count'>{loadingStats ? '...' : (quota?.remaining ?? 0)}</Text>
              </View>

              <View className='credit-divider' />

              <View className='credit-item'>
                <View className='item-info'>
                  <LucideIcon name='sparkles' size={28} color='var(--text-muted)' />
                  <View className='text-group'>
                    <Text className='title'>永久奖励积分</Text>
                    <Text className='subtitle'>通过活动或分享获得</Text>
                  </View>
                </View>
                <Text className='count'>{loadingStats ? '...' : (quota?.bonus ?? 0)}</Text>
              </View>
            </View>

            {/* Achievement Coronation */}
            <View className='dashboard-footer' onClick={(e) => { e.stopPropagation(); setShowAchievementSheet(true) }}>
              <View className='achievement-badge'>
                <View className='medal-icon' style={{ borderColor: tier.color + '40' }}>
                  <LucideIcon name={tier.icon} size={36} color={tier.color} />
                </View>
                <View className='text-group'>
                  <Text className='history-label'>阅读成就</Text>
                  <View className='tier-row'>
                    <Text className='tier-text' style={{ color: tier.color }}>{tier.cnTitle}</Text>
                    <Text className='lv-tag' style={{ backgroundColor: tier.color + '15', color: tier.color }}>Lv.{tier.level}</Text>
                  </View>
                </View>
              </View>
              <View className='history-value-box'>
                <Text className='history-value' style={{ color: articleCount > 0 ? tier.color : 'var(--color-ink)' }}>
                  累计 {loadingStats ? '...' : articleCount} 篇
                </Text>
              </View>

              <View className='click-indicator'>
                <LucideIcon name='chevronRight' size={28} color='var(--text-muted)' />
              </View>
            </View>
          </View>
        </View>

        <View className='menu-list'>
          {menuGroups.map((group, gIdx) => (
            <View key={gIdx} className='menu-group'>
              <Text className='group-title'>{group.title}</Text>
              <View className='group-box'>
                {group.items.map((item, iIdx) => (
                  <View key={iIdx} className='menu-item' onClick={() => handleMenuClick(item)}>
                    <View className='item-left'>
                      <View className={`icon-box ${item.color}`}>
                        <LucideIcon name={item.icon} size={36} color='currentColor' />
                      </View>
                      <Text className='label'>{item.label}</Text>
                    </View>
                    <View className='item-right'>
                      {item.value && <Text className='value-tag'>{item.value}</Text>}
                      {item.url && <LucideIcon name='chevronRight' size={32} color='var(--text-muted)' />}
                    </View>
                  </View>
                ))}
              </View>
            </View>
          ))}

        </View>

        <View className='version-tag'>
          <Text>Claread V1.0.0</Text>
        </View>
        <View className='bottom-spacer' />
      </ScrollView>

      {!isSubView && <TabBar current='profile' />}

      <CenterModal
        visible={showModeSheet}
        title='设置默认分析模式'
        onClose={() => setShowModeSheet(false)}
      >
        <View className='modal-config-wrapper'>
          <ConfigEditor
            mode='detailed'
            initialGoal={purpose as ReadingGoal}
            initialLevel={level}
            onComplete={(g, l) => {
              handleModeSelect(g, l)
              setShowModeSheet(false)
            }}
          />
        </View>
      </CenterModal>

      <CenterModal
        visible={showAchievementSheet}
        title='阅读勋章体系'
        onClose={() => setShowAchievementSheet(false)}
      >
        <View className='achievement-guide-content'>
          <View className='guide-header'>
            <Text className='guide-desc'>累计阅读篇数即可解锁更高级别的学术勋章。</Text>
          </View>

          <View className='tier-list'>
            {allTiers.map((t) => (
              <View key={t.level} className={`tier-item ${tier.level === t.level ? 'current' : ''}`}>
                <View className='tier-icon-box' style={{ color: t.color, backgroundColor: t.color + '15' }}>
                  <LucideIcon name={t.icon} size={42} color={t.color} />
                </View>
                <View className='tier-info'>
                  <View className='tier-main'>
                    <Text className='tier-name'>{t.cnTitle}</Text>
                    <Text className='tier-lv'>Lv.{t.level}</Text>
                  </View>
                  <Text className='tier-requirement'>
                    {t.level === 0 ? '初始勋章' : `累计阅读达 ${[0, 1, 30, 80, 120, 300][t.level]} 篇`}
                  </Text>
                </View>
                {tier.level === t.level && (
                  <View className='current-label'>当前等级</View>
                )}
              </View>
            ))}
          </View>

          <View className='guide-footer'>
            <Text className='footer-tips'>* 数据同步自云端，删除本地记录不影响等级</Text>
          </View>
        </View>
      </CenterModal>
    </View>
  )
}
