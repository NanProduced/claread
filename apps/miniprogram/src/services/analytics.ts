/**
 * 埋点服务（Phase 3: console.log 输出，后续替换为真实埋点 SDK）
 */

const track = (event: string, params?: Record<string, unknown>) => {
  // TODO: 后续替换为真实埋点 SDK（如、神策、Mixpanel 等）
  if (process.env.NODE_ENV === 'development') {
    console.log('[track]', event, params)
  }
}

export { track }
