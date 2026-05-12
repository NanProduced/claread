/**
 * 认证服务
 *
 * 封装微信登录流程，供非 Profile 页面调用（收藏/生词本等场景引导登录）
 */

import Taro from '@tarojs/taro'
import { useAuthStore } from '../stores/auth'
import { fetchWeChatLogin } from './api/client'
import { getAllRecords, getFavorites, getVocabulary } from './storage'

export interface LoginResult {
  success: boolean
  /** 登录成功时是否为首次登录（user_configured 未设置） */
  isFirstLogin: boolean
}

/**
 * 引导用户登录
 *
 * 流程：
 * 1. 检查是否已登录（已登录直接返回 { success: true, isFirstLogin: false }）
 * 2. 如 skipConfirmModal=false（默认），弹出确认对话框；skipConfirmModal=true 时跳过对话框直接登录
 * 3. 用户确认 → 调用 wx.login → 后端换取 session token → 存到 auth store
 * 4. 登录成功后检查是否首次登录（user_configured 未设置）
 * 5. 登录成功后，同步本地收藏和生词本到云端
 * 6. 用户取消 → 返回 { success: false, isFirstLogin: false }
 *
 * @param skipConfirmModal - 为 true 时跳过确认对话框，适用于已有引导层的场景（如 LoginGuideModal）
 * @returns LoginResult
 */
export async function ensureLoggedIn(skipConfirmModal = false): Promise<LoginResult> {
  // 已登录，直接放行
  if (useAuthStore.getState().isLoggedIn) {
    return { success: true, isFirstLogin: false }
  }

  // 弹确认框（skipConfirmModal=true 时由调用方自行提供确认 UI）
  if (!skipConfirmModal) {
    const { confirm } = await Taro.showModal({
      title: '登录后同步云端',
      content: '登录后可将收藏和生词本同步到云端，跨设备查看。是否立即登录？',
      confirmText: '微信登录',
      confirmColor: '#07c160',
      cancelText: '稍后',
    })
    if (!confirm) {
      return { success: false, isFirstLogin: false }
    }
  }

  // 执行微信登录
  try {
    const loginResult = await Taro.login()
    if (!loginResult.code) {
      Taro.showToast({ title: '微信登录失败', icon: 'none' })
      return { success: false, isFirstLogin: false }
    }

    const res = await fetchWeChatLogin(loginResult.code)
    const authStore = useAuthStore.getState()
    authStore.login(res.session_token, { user_id: res.user_id })

    // 登录后立即获取完整用户信息（包含云端配置和成就）
    await authStore.fetchUserInfo()

    Taro.showToast({ title: '登录成功', icon: 'success' })

    // 检查是否首次登录（user_configured 未设置）
    const isFirstLogin = !Taro.getStorageSync('user_configured')

    // 登录成功后，同步本地资产到云端（静默进行，失败不阻塞）
    // 注意：records 必须先于 favorites/vocab 同步，因为后端 favorites 表依赖 analysis_record_id
    syncLocalAssetsToCloud()

    return { success: true, isFirstLogin }
  } catch (err) {
    console.warn('[auth] ensureLoggedIn failed', err)
    Taro.showToast({ title: '登录失败，请重试', icon: 'none' })
    return { success: false, isFirstLogin: false }
  }
}

/**
 * 登录后同步本地记录、收藏和生词本到云端。
 *
 * 顺序：records → favorites + vocab
 * 原因：后端 favorites 表的 analysis_record_id 依赖 cloud records 已存在。
 * 策略：fire-and-forget，失败静默忽略，不阻塞用户体验。
 */
async function syncLocalAssetsToCloud(): Promise<void> {
  try {
    const { CloudSyncService } = await import('./cloudSync.service')

    const records = getAllRecords()
    const favorites = getFavorites()
    const vocab = getVocabulary()

    // Step 1: 先同步 records（需要时间，且 favorites/vocab 依赖它）
    const recordPromises = records.map((r) =>
      CloudSyncService.syncRecord(r).catch(() => {})
    )

    // Step 2: records 同步完成后，sync favorites 和 vocab（并行）
    Promise.all(recordPromises).then(() => {
      const favPromise = favorites.length > 0
        ? CloudSyncService.syncAllFavorites(favorites)
        : Promise.resolve()
      const vocabPromise = vocab.length > 0
        ? CloudSyncService.syncAllVocab(vocab)
        : Promise.resolve()
      Promise.all([favPromise, vocabPromise]).catch(() => {})
    })
  } catch (err) {
    // 静默失败，不影响登录流程
    console.warn('[auth] syncLocalAssetsToCloud failed', err)
  }
}
