const ROUTES = {
  HOME: '/pages/home/index',
  INPUT: '/pages/input/index',
  RESULT: '/pages/result/index',
  HISTORY: '/packageA/history/index',
  VOCAB: '/packageA/vocab/index',
  VOCAB_REVIEW: '/packageA/vocab-review/index',
  PROFILE: '/packageA/profile/index',
  CREDIT_DETAIL: '/packageA/credit-detail/index',
  DAILY_READER: '/packageB/daily-reader/index',
  DAILY_READER_ARCHIVE: '/packageB/daily-reader-archive/index',
  FEEDBACK: '/packageC/feedback/index',
  FEEDBACK_MY: '/packageC/feedback/my-feedback',
  ONBOARDING: '/packageC/onboarding/index',
  ABOUT: '/packageC/about/index',
  AGREEMENT: '/packageC/agreement/index',
} as const

type RouteKey = keyof typeof ROUTES
type RoutePath = (typeof ROUTES)[RouteKey]

export { ROUTES }
export type { RouteKey, RoutePath }
