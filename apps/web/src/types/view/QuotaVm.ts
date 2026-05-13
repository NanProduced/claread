/**
 * 配额 VM
 */
export interface QuotaVm {
  profileId: string
  quotaUsed: number
  quotaLimit: number
  quotaType: 'daily' | 'monthly' | 'unlimited'
  resetAt?: string
  dailyFreePoints?: number
  dailyUsedPoints?: number
  bonusPoints?: number
  remainingPoints?: number
  unit?: 'points'
}
