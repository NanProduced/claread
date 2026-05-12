/**
 * API 配置
 *
 * 统一管理 API 相关的配置项
 */

import { envConfig } from './env'
import { getToken } from '../stores/auth'

/** API 超时配置 (ms)
 *
 * 注意：LLM 生成可能需要很长时间（数十秒到数分钟），
 * 不应设置超时限制，否则会导致：
 * 1. 浪费 token（服务端已在生成但客户端断开）
 * 2. 用户体验差（看到失败但实际上后端在处理）
 *
 * 设置为 0 表示不限制超时，由 Taro.request 自身决定。
 * 但实际上 Taro.request 的 timeout 参数为 0 时可能使用默认超时，
 * 建议设置为一个非常大的值（如 10 分钟）。
 */
export const API_TIMEOUT = 600_000 // 10 分钟

/**
 * 请求头配置
 * 所有 API 调用自动附带认证 header（若已登录）
 */
export interface RequestHeaders {
  'Content-Type': 'application/json'
  'Authorization'?: string
  'X-Request-Id'?: string
  [key: string]: string | undefined
}

export const defaultHeaders: RequestHeaders = {
  'Content-Type': 'application/json',
}

/**
 * 获取带认证的请求头
 * Phase 2: 从 auth store 获取 token，注入到所有 API 请求
 */
export function getAuthHeaders(): RequestHeaders {
  const token = getToken()
  if (token) {
    return {
      ...defaultHeaders,
      'Authorization': `Bearer ${token}`,
    }
  }
  return { ...defaultHeaders }
}

export const apiConfig = {
  baseUrl: envConfig.apiBaseUrl,
  timeout: API_TIMEOUT,
  headers: defaultHeaders,
}
