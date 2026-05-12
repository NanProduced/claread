/**
 * 收藏全文 VM
 *
 * 只存对 analysis_records.recordId 的引用
 * 不复制整份 renderScene（按需从 record 读取）
 */

export interface FavoriteRecord {
  /** 来源分析记录的前端稳定主键 (client_record_id) */
  recordId: string
  /** 来源分析记录的云端 ID (UUID) */
  cloudId?: string
  /** 创建时间 */
  createdAt: number
  /** 同步状态 */
  syncState?: 'local_only' | 'syncing' | 'synced' | 'sync_failed'
  /** 待执行操作 */
  pendingOp?: 'add' | 'remove' | null
  /** 软删除标记 */
  tombstone?: boolean
}
