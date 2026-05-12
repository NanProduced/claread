/**
 * CloudSyncService
 *
 * 离线优先的持久化同步服务。
 * 所有用户资产 mutation 先写本地，再入队后台 flush。
 * 未登录时静默跳过。
 * 队列持久化到 Taro storage，重启后不丢失。
 */

import { useAuthStore } from '../stores/auth'
import type { AnalysisRecord } from '../types/view/analysis-record.vm'
import type { FavoriteRecord } from '../types/view/favorites.vm'
import type { VocabEntry } from '../types/view/vocabulary.vm'
import {
  getRecord,
  updateRecord,
  getVocabulary,
  getVocabEntryByLemma,
  removeVocabEntry,
  saveVocabEntry,
  updateVocabEntry,
  saveRecordIdentity,
  resolveCloudIdFromMap,
  getSyncQueue,
  saveSyncQueue,
  enqueueSyncItem,
  updateSyncQueueItem,
  removeSyncQueueItem,
  getPendingSyncItems,
  type SyncQueueItem,
} from './storage'
import {
  saveRecordToCloud,
  fetchCloudRecordByClientId,
  deleteCloudRecord,
} from './api/records.client'
import {
  addFavoriteToCloud,
  removeFavoriteFromCloud,
} from './api/favorites.client'
import {
  addVocabToCloud,
  updateCloudVocabulary,
  deleteCloudVocabulary,
} from './api/vocabulary.client'

function hashString(str: string): string {
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i)
    hash = (hash << 5) - hash + char
    hash = hash & hash
  }
  return Math.abs(hash).toString(16).padStart(8, '0')
}

function generateOpId(): string {
  return `op_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

// ---------------------------------------------------------------------------
// resolveCloudId: 优先读本地映射 + storage，再走网络
// ---------------------------------------------------------------------------

const resolveCloudIdCache = new Map<string, Promise<string | null>>()

async function resolveCloudId(clientRecordId: string): Promise<string | null> {
  const local = getRecord(clientRecordId)
  if (local?.cloudId) return local.cloudId

  const fromMap = resolveCloudIdFromMap(clientRecordId)
  if (fromMap) return fromMap

  if (resolveCloudIdCache.has(clientRecordId)) {
    return resolveCloudIdCache.get(clientRecordId)!
  }

  const promise = (async () => {
    try {
      const cloudRecord = await fetchCloudRecordByClientId(clientRecordId)
      if (cloudRecord?.cloudId) {
        saveRecordIdentity(clientRecordId, cloudRecord.cloudId)
        updateRecord(clientRecordId, { cloudId: cloudRecord.cloudId })
        return cloudRecord.cloudId
      }
    } catch (e) {
    console.error("cloudSync.service.ts:", e)
    } finally {
      setTimeout(() => resolveCloudIdCache.delete(clientRecordId), 5000)
    }
    return null
  })()

  resolveCloudIdCache.set(clientRecordId, promise)
  return promise
}

// ---------------------------------------------------------------------------
// Flush Worker: 全局单例，串行执行 pending 队列
// ---------------------------------------------------------------------------

let flushRunning = false

function recoverStuckRunningItems(): void {
  const queue = getSyncQueue()
  let dirty = false
  for (let i = 0; i < queue.length; i++) {
    if (queue[i].status === 'running') {
      queue[i] = { ...queue[i], status: 'pending', updatedAt: Date.now() }
      dirty = true
    }
  }
  if (dirty) saveSyncQueue(queue)
}

const FAILED_ITEM_TTL_MS = 24 * 60 * 60 * 1000
const FAILED_ITEM_MAX = 50

function cleanupFailedItems(): void {
  const queue = getSyncQueue()
  const now = Date.now()
  const filtered = queue.filter(item => {
    if (item.status !== 'failed') return true
    if (now - item.updatedAt > FAILED_ITEM_TTL_MS) return false
    return true
  })

  if (filtered.length === queue.length) {
    const failedItems = filtered.filter(item => item.status === 'failed')
    if (failedItems.length > FAILED_ITEM_MAX) {
      const toRemove = new Set(
        failedItems
          .sort((a, b) => a.updatedAt - b.updatedAt)
          .slice(0, failedItems.length - FAILED_ITEM_MAX)
          .map(item => item.opId)
      )
      const afterMaxFilter = filtered.filter(item => !toRemove.has(item.opId))
      saveSyncQueue(afterMaxFilter)
      return
    }
    return
  }

  saveSyncQueue(filtered)
}

async function flushQueue(): Promise<void> {
  if (flushRunning) return
  if (!useAuthStore.getState().isLoggedIn) return

  recoverStuckRunningItems()
  cleanupFailedItems()

  flushRunning = true
  try {
    const pending = getPendingSyncItems()
    if (pending.length === 0) return

    for (const item of pending) {
      const now = Date.now()
      if (item.nextRetryAt && now < item.nextRetryAt) continue

      updateSyncQueueItem(item.opId, { status: 'running' })

      try {
        await executeQueueItem(item)
        removeSyncQueueItem(item.opId)
      } catch (err) {
        const retryCount = item.retryCount + 1
        const maxRetries = 5
        const backoffMs = Math.min(1000 * Math.pow(2, retryCount), 60000)

        if (retryCount >= maxRetries) {
          updateSyncQueueItem(item.opId, {
            status: 'failed',
            retryCount,
            lastError: err instanceof Error ? err.message : String(err),
          })
        } else {
          updateSyncQueueItem(item.opId, {
            status: 'pending',
            retryCount,
            nextRetryAt: Date.now() + backoffMs,
            lastError: err instanceof Error ? err.message : String(err),
          })
        }
      }
    }
  } finally {
    flushRunning = false
  }
}

async function executeQueueItem(item: SyncQueueItem): Promise<void> {
  switch (item.action) {
    case 'SYNC_RECORD':
      await executeSyncRecord(item)
      break
    case 'ADD_FAVORITE':
      await executeAddFavorite(item)
      break
    case 'REMOVE_FAVORITE':
      await executeRemoveFavorite(item)
      break
    case 'UPSERT_VOCAB':
      await executeUpsertVocab(item)
      break
    case 'UPDATE_VOCAB_MASTERY':
      await executeUpdateVocabMastery(item)
      break
    case 'DELETE_VOCAB':
      await executeDeleteVocab(item)
      break
    case 'DELETE_RECORD':
      await executeDeleteRecord(item)
      break
    default:
      console.warn('[cloudSync] unknown action:', item.action)
  }
}

async function executeSyncRecord(item: SyncQueueItem): Promise<void> {
  const { clientRecordId } = item.payload as { clientRecordId: string }
  const record = getRecord(clientRecordId as string)
  if (!record || !record.sourceText) return

  const fallbackTitle = record.sourceText.split('\n')[0]?.trim() || ''
  const isFallbackTitle = record.title && (
    record.title === fallbackTitle ||
    record.title === (fallbackTitle.length > 50 ? `${fallbackTitle.slice(0, 50)}...` : fallbackTitle)
  )
  const syncTitle = isFallbackTitle ? null : (record.title ?? null)

  const res = await saveRecordToCloud({
    clientRecordId: record.recordId,
    title: syncTitle,
    sourceText: record.sourceText,
    sourceTextHash: hashString(record.sourceText),
    requestPayload: record.requestPayload,
    renderScene: record.renderScene,
    pageState: record.pageState,
  })

  const cloudRecordId = res.id
  saveRecordIdentity(record.recordId, String(cloudRecordId))
  updateRecord(record.recordId, {
    cloudId: String(cloudRecordId),
    syncState: 'synced',
    lastSyncedAt: Date.now(),
  })
}

async function executeAddFavorite(item: SyncQueueItem): Promise<void> {
  const { clientRecordId, targetType } = item.payload as { clientRecordId: string; targetType?: string }
  const isDailyReader = targetType === 'daily_reader_article'
  let cloudId = resolveCloudIdFromMap(clientRecordId)
  if (!cloudId) {
    const record = getRecord(clientRecordId)
    cloudId = record?.cloudId ?? null
  }
  if (!cloudId && !isDailyReader) {
    cloudId = (await resolveCloudId(clientRecordId)) ?? null
  }
  if (!cloudId && !isDailyReader) {
    throw new Error(`Cannot resolve cloudId for favorite add: ${clientRecordId}`)
  }
  await addFavoriteToCloud(cloudId, clientRecordId, isDailyReader ? 'daily_reader_article' : 'analysis_record')
}

async function executeRemoveFavorite(item: SyncQueueItem): Promise<void> {
  const { clientRecordId, targetType } = item.payload as { clientRecordId: string; targetType?: string }
  const isDailyReader = targetType === 'daily_reader_article'
  let cloudId = resolveCloudIdFromMap(clientRecordId)
  if (!cloudId) {
    const record = getRecord(clientRecordId)
    cloudId = record?.cloudId ?? null
  }
  if (!cloudId && !isDailyReader) {
    cloudId = (await resolveCloudId(clientRecordId)) ?? null
  }
  if (!cloudId && !isDailyReader) {
    throw new Error(`Cannot resolve cloudId for favorite remove: ${clientRecordId}`)
  }
  if (isDailyReader) {
    await removeFavoriteFromCloud(clientRecordId, 'daily_reader_article')
  } else {
    await removeFavoriteFromCloud(cloudId!)
  }
}

async function executeUpsertVocab(item: SyncQueueItem): Promise<void> {
  const { vocabEntry, sourceType } = item.payload as { vocabEntry: VocabEntry; sourceType?: string }
  const entry = vocabEntry
  const isDailyReader = sourceType === 'daily_reader_article'

  const primaryRef = entry.sourceRefs?.[0]
  let resolvedRecordId = primaryRef?.cloudRecordId
  if (!resolvedRecordId && primaryRef?.clientRecordId && !isDailyReader) {
    resolvedRecordId = (await resolveCloudId(primaryRef.clientRecordId)) || undefined
  }
  if (!resolvedRecordId && primaryRef?.clientRecordId && !isDailyReader) {
    throw new Error(`Cannot resolve cloudRecordId for vocab upsert: ${entry.word}`)
  }

  const syncedRefs = (entry.sourceRefs || []).map(ref => ({
    ...ref,
    cloudRecordId: ref.cloudRecordId || resolvedRecordId,
  }))

  const res = await addVocabToCloud({ ...entry, sourceRefs: syncedRefs })

  if (res.id && res.id !== entry.id) {
    const currentVocab = getVocabulary()
    const target = currentVocab.find(v => v.id === entry.id)
    if (target) {
      removeVocabEntry(entry.id)
      const newEntry: VocabEntry = { ...target, id: res.id, sourceRefs: syncedRefs, syncState: 'synced' }
      saveVocabEntry(newEntry)
    }
  } else {
    updateVocabEntry(entry.id, { syncState: 'synced' })
  }
}

function _isCloudUuid(id: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
}

async function executeUpdateVocabMastery(item: SyncQueueItem): Promise<void> {
  const { lemma, masteryStatus } = item.payload as { lemma: string; masteryStatus: string; cloudId?: string }
  const targetId = resolveCurrentVocabId(lemma, item.entityId)
  if (!targetId) return
  if (!_isCloudUuid(targetId)) throw new Error(`vocab mastery sync: local ID not yet replaced by cloud UUID (${lemma})`)
  await updateCloudVocabulary(targetId, { mastery_status: masteryStatus as 'new' | 'learning' | 'review' | 'mastered' | 'archived' })
}

async function executeDeleteVocab(item: SyncQueueItem): Promise<void> {
  const { lemma } = item.payload as { lemma: string; cloudId?: string }
  const targetId = resolveCurrentVocabId(lemma, item.entityId)
  if (!targetId) return
  if (!_isCloudUuid(targetId)) throw new Error(`vocab delete sync: local ID not yet replaced by cloud UUID (${lemma})`)
  await deleteCloudVocabulary(targetId)
}

function resolveCurrentVocabId(lemma: string, fallbackId: string): string | null {
  const match = getVocabEntryByLemma(lemma)
  if (match) return match.id
  const fallback = getVocabEntryByLemma(fallbackId) || _getVocabEntryById(fallbackId)
  if (fallback && !fallback.tombstone) return fallback.id
  return null
}

function _getVocabEntryById(id: string): VocabEntry | null {
  const all = getVocabulary()
  return all.find(v => v.id === id && !v.tombstone) || null
}

async function executeDeleteRecord(item: SyncQueueItem): Promise<void> {
  const { cloudRecordId } = item.payload as { cloudRecordId: string }
  if (!cloudRecordId) return
  await deleteCloudRecord(cloudRecordId)
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export const CloudSyncService = {
  /**
   * 同步分析记录到云端（upsert）
   * 成功后回填 cloudId 并写入 ID 映射
   */
  async syncRecord(record: AnalysisRecord): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return
    if (!record.sourceText) return

    updateRecord(record.recordId, { syncState: 'syncing' })

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'record',
      entityId: record.recordId,
      action: 'SYNC_RECORD',
      payload: { clientRecordId: record.recordId },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

    flushQueue()
  },

  /**
   * 同步收藏状态到云端
   */
  async syncFavorite(cloudId: string | undefined, clientRecordId: string, action: 'add' | 'remove', targetType?: string): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'favorite',
      entityId: clientRecordId,
      action: action === 'add' ? 'ADD_FAVORITE' : 'REMOVE_FAVORITE',
      payload: { clientRecordId, cloudId: cloudId || null, targetType },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

    flushQueue()
  },

  /**
   * 同步生词本条目到云端
   */
  async syncVocab(entry: VocabEntry, sourceType?: string): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'vocab',
      entityId: entry.id,
      action: 'UPSERT_VOCAB',
      payload: { vocabEntry: { ...entry }, sourceType },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

    flushQueue()
  },

  /**
   * 同步生词掌握状态到云端
   */
  async syncVocabMastery(vocabId: string, masteryStatus: string, lemma: string): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'vocab',
      entityId: vocabId,
      action: 'UPDATE_VOCAB_MASTERY',
      payload: { lemma, masteryStatus, cloudId: vocabId },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

    flushQueue()
  },

  /**
   * 同步删除生词到云端
   */
  async syncDeleteVocab(vocabId: string, lemma: string): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'vocab',
      entityId: vocabId,
      action: 'DELETE_VOCAB',
      payload: { lemma, cloudId: vocabId },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

    flushQueue()
  },

  /**
   * 同步删除记录到云端
   */
  async syncDeleteRecord(cloudRecordId: string, clientRecordId: string): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'record',
      entityId: clientRecordId,
      action: 'DELETE_RECORD',
      payload: { cloudRecordId, clientRecordId },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

    flushQueue()
  },

  /**
   * 同步所有本地收藏到云端（登录后全量同步）
   */
  async syncAllFavorites(localFavorites: FavoriteRecord[]): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    for (const fav of localFavorites) {
      if (fav.tombstone) continue
      const record = getRecord(fav.recordId)
      if (!record?.isFavorited) continue

      enqueueSyncItem({
        opId: generateOpId(),
        entityType: 'favorite',
        entityId: fav.recordId,
        action: 'ADD_FAVORITE',
        payload: { clientRecordId: fav.recordId, cloudId: record.cloudId || null },
        status: 'pending',
        retryCount: 0,
        createdAt: Date.now(),
        updatedAt: Date.now(),
      })
    }

    flushQueue()
  },

  /**
   * 同步所有本地生词本到云端（登录后全量同步）
   */
  async syncAllVocab(localVocab: VocabEntry[]): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    for (const entry of localVocab) {
      if (entry.tombstone) continue

      enqueueSyncItem({
        opId: generateOpId(),
        entityType: 'vocab',
        entityId: entry.id,
        action: 'UPSERT_VOCAB',
        payload: { vocabEntry: { ...entry } },
        status: 'pending',
        retryCount: 0,
        createdAt: Date.now(),
        updatedAt: Date.now(),
      })
    }

    flushQueue()
  },

  /**
   * 手动触发 flush（App 启动、登录成功、onShow 时调用）
   */
  flush: flushQueue,

  /**
   * 获取队列状态（调试用）
   */
  getQueueStatus() {
    const queue = getSyncQueue()
    return {
      total: queue.length,
      pending: queue.filter(i => i.status === 'pending').length,
      running: queue.filter(i => i.status === 'running').length,
      failed: queue.filter(i => i.status === 'failed').length,
      done: queue.filter(i => i.status === 'done').length,
    }
  },
}
