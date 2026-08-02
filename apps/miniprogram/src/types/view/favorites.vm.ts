/**
 * 收藏 VM
 *
 * CUTOVER-MINI-LONG: 中性本地收藏模型，仅引用 Daily Reader 文章 ID。
 * 旧 analysis_records.recordId / cloudId 字段已移除。
 */

export interface FavoriteRecord {
  /** 来源 Daily Reader 文章 ID */
  dailyReaderArticleId: string
  /** 创建时间 */
  createdAt: number
  /** 同步状态 */
  syncState?: 'local_only' | 'syncing' | 'synced' | 'sync_failed'
  /** 待执行操作 */
  pendingOp?: 'add' | 'remove' | null
  /** 软删除标记 */
  tombstone?: boolean
}