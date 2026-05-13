/**
 * 配额 VM
 */
export interface QuotaVm {
  profileId: string
  quotaUsed: number
  quotaLimit: number
  quotaType: 'daily' | 'monthly' | 'unlimited'
  resetAt?: string
}