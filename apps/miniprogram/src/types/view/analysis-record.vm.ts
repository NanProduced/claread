/**
 * 分析记录 VM
 *
 * 对应本地存储的 analysis_record 数据结构
 * 不直接等同于后端 DTO，是前端用于回看的快照模型
 */

import type { AnalyzeRequest } from '../../services/api'
import type { AnyRenderSceneVm, ResultPageState } from './render-scene.vm'

export interface AnalysisRecord {
  /** 前端生成的稳定记录主键 (client_record_id) */
  recordId: string
  /** 云端主键 ID (UUID)，用于 API 操作如删除 */
  cloudId?: string
  /** 同步状态 */
  syncState?: 'local_only' | 'syncing' | 'synced' | 'sync_failed'
  /** 最近一次同步尝试时间 */
  lastSyncAttemptAt?: number
  /** 最近一次同步成功时间 */
  lastSyncedAt?: number
  /** 最近一次同步错误 */
  lastSyncError?: string | null
  /** 软删除标记 */
  tombstone?: boolean
  /** 摘要标题（由 translation agent 生成） */
  title?: string | null
  /** 原始输入文本（用于重新分析） */
  sourceText: string
  /** 发给 /analyze 的请求参数 */
  requestPayload: {
    reading_goal: AnalyzeRequest['reading_goal']
    reading_variant: AnalyzeRequest['reading_variant']
    source_type: AnalyzeRequest['source_type']
  }
  /** 分析结果快照（null 表示分析失败/异常） */
  renderScene: AnyRenderSceneVm | null
  /** 回看时的页面状态 */
  pageState: ResultPageState
  /** 创建时间（timestamp） */
  createdAt: number
  /** 最近一次更新时间（timestamp） */
  updatedAt: number
  /** 是否已收藏全文 */
  isFavorited: boolean
  /** 关联生词数 */
  vocabCount?: number
}
