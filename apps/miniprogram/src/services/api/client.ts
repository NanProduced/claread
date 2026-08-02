/**
 * API Client - 统一请求入口
 *
 * 所有 API 调用必须通过此模块
 * 支持环境配置 + 认证头注入
 */

import Taro from '@tarojs/taro'
import { apiConfig, getAuthHeaders } from '../../config/api.config'
import type { DictEntryResultDto, DictResponseDto } from '../../types/api/dict-response.dto'

/** API 错误类型 */
export class ApiError extends Error {
  constructor(
    message: string,
    public code: string,
    public statusCode: number,
    public response?: unknown
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function extractApiErrorMessage(statusCode: number, responseData: unknown): string {
  if (!responseData || typeof responseData !== 'object') {
    return `请求失败: ${statusCode}`
  }

  const detail = (responseData as { detail?: string }).detail
  if (typeof detail === 'string' && detail.trim()) {
    return `请求失败: ${statusCode} - ${detail}`
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const firstDetail = detail[0]
    if (typeof firstDetail?.msg === 'string') {
      return `请求失败: ${statusCode} - ${firstDetail.msg}`
    }
  }

  return `请求失败: ${statusCode}`
}

/** 请求选项 */
interface RequestOptions {
  url: string
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  data?: unknown
  headers?: Record<string, string>
  timeout?: number
}

let isRefreshing = false
let refreshSubscribers: ((success: boolean) => void)[] = []

function onRefreshed(success: boolean) {
  refreshSubscribers.forEach((cb) => cb(success))
  refreshSubscribers = []
}

/**
 * 统一请求方法
 * - 自动添加 baseURL
 * - 自动注入认证头
 * - 统一错误处理
 * - 自动拦截 401 并触发重新登录重试
 */
export async function request<T>(options: RequestOptions): Promise<T> {
  const { url, method = 'GET', data, headers = {}, timeout = apiConfig.timeout } = options

  const fullUrl = url.startsWith('http') ? url : `${apiConfig.baseUrl}${url}`

  try {
    const response = await Taro.request({
      url: fullUrl,
      method,
      data,
      header: {
        ...getAuthHeaders(),
        ...headers,
      },
      timeout,
    })

    const { statusCode, data: responseData } = response

    // 拦截 401 未授权
    if (statusCode === 401 && !fullUrl.includes('/auth/wechat/login')) {
      if (!isRefreshing) {
        isRefreshing = true
        // 使用动态 import 避免与 auth.ts 的循环依赖
        import('../auth')
          .then(({ ensureLoggedIn }) => ensureLoggedIn(true))
          .then((res) => {
            isRefreshing = false
            onRefreshed(res.success)
          })
          .catch(() => {
            isRefreshing = false
            onRefreshed(false)
          })
      }

      // 将当前请求挂起，等待登录完成
      return new Promise<T>((resolve, reject) => {
        refreshSubscribers.push((success: boolean) => {
          if (success) {
            // 登录成功，带上新的 header 重试
            request<T>(options).then(resolve).catch(reject)
          } else {
            // 登录失败或取消，抛出原始 401 错误
            reject(new ApiError('请先登录', 'UNAUTHORIZED', 401, responseData))
          }
        })
      })
    }

    if (statusCode === 204) {
      return undefined as T
    }

    if (statusCode >= 400) {
      throw new ApiError(
        extractApiErrorMessage(statusCode, responseData),
        'HTTP_ERROR',
        statusCode,
        responseData
      )
    }

    return responseData as T
  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }

    // Taro.request 可能的网络错误
    if (error instanceof Error) {
      const msg = error.message.toLowerCase()
      // 超时类错误：timeout / etimedout / etimeout / timedout
      if (msg.includes('timeout') || msg.includes('etimedout') || msg.includes('etimeout') || msg.includes('timedout')) {
        throw new ApiError('请求超时', 'TIMEOUT', 0)
      }
      // 网络类错误：network / err_ / econnreset / econnrefused / enotfound / eai_again
      if (msg.includes('network') || msg.includes('err_') || msg.includes('econnreset') ||
          msg.includes('econnrefused') || msg.includes('enotfound') || msg.includes('eai_again') ||
          msg.includes('socket') || msg.includes('aborted')) {
        throw new ApiError('网络错误', 'NETWORK_ERROR', 0)
      }
    }

    // 记录完整错误便于调试
    console.error('[api] request failed:', error)
    throw new ApiError('未知错误', 'UNKNOWN', 0, error)
  }
}

// ============ /auth API ============

interface WeChatLoginResponse {
  user_id: string
  session_token: string
  expires_at: string
}

/**
 * 微信小程序登录
 *
 * 流程: wx.login() → POST /auth/wechat/login → 存 token
 */
export async function fetchWeChatLogin(code: string): Promise<WeChatLoginResponse> {
  return request<WeChatLoginResponse>({
    url: '/auth/wechat/login',
    method: 'POST',
    data: { code },
  })
}

interface SessionUserResponse {
  user_id: string
  session_id: string
  avatar_url?: string
  nickname?: string
  cumulative_article_count?: number
  settings?: Record<string, any>
}

/** 获取当前会话用户信息 */
export async function fetchSessionUser(): Promise<SessionUserResponse> {
  return request<SessionUserResponse>({
    url: '/auth/session/me',
    method: 'GET',
  })
}

export interface UpdateProfileRequest {
  nickname?: string
  avatar_url?: string
  settings?: Record<string, any>
}

/** 更新用户资料（昵称、头像、设置等） */
export async function updateProfile(data: UpdateProfileRequest): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>({
    url: '/auth/profile',
    method: 'PATCH',
    data,
  })
}

/** 登出（撤销 session） */
export async function fetchSessionLogout(sessionToken: string): Promise<void> {
  return request<void>({
    url: '/auth/session/logout',
    method: 'POST',
    data: { session_token: sessionToken },
  })
}

// ============ /me/quota API ============

export interface QuotaResponse {
  daily_free_points: number
  daily_used_points: number
  bonus_points: number
  remaining_points: number
}

export async function fetchUserQuota(): Promise<QuotaResponse> {
  return request<QuotaResponse>({
    url: '/me/quota',
    method: 'GET',
  })
}

// ============ /dict API ============

/**
 * 调用 /dict 接口查询单词或短语释义
 */
export async function fetchDict(
  word: string,
  type: 'word' | 'phrase' = 'word',
  contextSentence?: string,
  occurrence?: number,
): Promise<DictResponseDto> {
  let url = `/dict?q=${encodeURIComponent(word)}&type=${type}`
  if (contextSentence) {
    const truncated = contextSentence.length > 500 ? contextSentence.slice(0, 500) : contextSentence
    url += `&context_sentence=${encodeURIComponent(truncated)}`
  }
  if (occurrence != null) url += `&occurrence=${occurrence}`

  return request<DictResponseDto>({
    url,
  })
}

export async function fetchDictEntry(entryId: number): Promise<DictEntryResultDto> {
  return request<DictEntryResultDto>({
    url: `/dict/entry?id=${entryId}`,
  })
}