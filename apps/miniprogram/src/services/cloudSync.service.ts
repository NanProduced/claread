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
import type { UserAnnotationDto } from './api/user-annotations.client'
import type { ReaderNoteDto } from './api/reader-notes.client'
import {
  getRecord,
  updateRecord,
  saveRecordIdentity,
  resolveCloudIdFromMap,
  getSyncQueue,
  saveSyncQueue,
  enqueueSyncItem,
  updateSyncQueueItem,
  removeSyncQueueItem,
  getPendingSyncItems,
  addLocalUserAnnotation,
  removeLocalUserAnnotation,
  updateLocalUserAnnotation,
  addLocalReaderNote,
  removeLocalReaderNote,
  updateLocalReaderNote,
  type SyncQueueItem,
} from './storage'
import {
  saveRecordToCloud,
  fetchCloudRecordByClientId,
  deleteCloudRecord,
} from './api/records.client'
import {
  createUserAnnotation,
  deleteUserAnnotation,
  updateUserAnnotation,
} from './api/user-annotations.client'
import {
  createReaderNote,
  deleteReaderNote,
  updateReaderNote,
} from './api/reader-notes.client'

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
    case 'DELETE_RECORD':
      await executeDeleteRecord(item)
      break
    case 'UPSERT_ANNOTATION':
      await executeUpsertAnnotation(item)
      break
    case 'DELETE_ANNOTATION':
      await executeDeleteAnnotation(item)
      break
    case 'UPSERT_NOTE':
      await executeUpsertNote(item)
      break
    case 'DELETE_NOTE':
      await executeDeleteNote(item)
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

async function executeDeleteRecord(item: SyncQueueItem): Promise<void> {
  const { cloudRecordId } = item.payload as { cloudRecordId: string }
  if (!cloudRecordId) return
  await deleteCloudRecord(cloudRecordId)
}

// ---------------------------------------------------------------------------
// Annotation Sync
// ---------------------------------------------------------------------------

interface AnnotationPayload {
  annotation: UserAnnotationDto
  isNew?: boolean
}

async function executeUpsertAnnotation(item: SyncQueueItem): Promise<void> {
  const payload = item.payload as Record<string, unknown>
  const annotation = payload.annotation as UserAnnotationDto | undefined
  const isNew = payload.isNew as boolean | undefined
  if (!annotation) return

  try {
    if (isNew) {
      const created = await createUserAnnotation({
        analysis_record_id: annotation.analysis_record_id,
        anchor_type: annotation.anchor_type,
        target_key: annotation.target_key,
        paragraph_id: annotation.paragraph_id ?? undefined,
        sentence_id: annotation.sentence_id,
        selected_text: annotation.selected_text,
        start_offset: annotation.start_offset ?? undefined,
        end_offset: annotation.end_offset ?? undefined,
        text_hash: annotation.text_hash ?? undefined,
        segments: annotation.segments,
        color: annotation.color,
        payload_json: annotation.payload_json,
      })
      addLocalUserAnnotation(created)
    } else {
      await updateUserAnnotation(annotation.id, { color: annotation.color })
      updateLocalUserAnnotation(annotation.id, annotation)
    }
  } catch (err) {
    console.warn('[cloudSync] executeUpsertAnnotation failed:', err)
    throw err
  }
}

async function executeDeleteAnnotation(item: SyncQueueItem): Promise<void> {
  const { annotationId } = item.payload as { annotationId: string }
  if (!annotationId) return

  try {
    await deleteUserAnnotation(annotationId)
    removeLocalUserAnnotation(annotationId)
  } catch (err) {
    console.warn('[cloudSync] executeDeleteAnnotation failed:', err)
    throw err
  }
}

// ---------------------------------------------------------------------------
// Note Sync
// ---------------------------------------------------------------------------

interface NotePayload {
  note: ReaderNoteDto
  isNew?: boolean
}

async function executeUpsertNote(item: SyncQueueItem): Promise<void> {
  const payload = item.payload as Record<string, unknown>
  const note = payload.note as ReaderNoteDto | undefined
  const isNew = payload.isNew as boolean | undefined
  if (!note) return

  try {
    if (isNew) {
      const created = await createReaderNote({
        analysis_record_id: note.analysis_record_id,
        anchor_sentence_id: note.anchor_sentence_id,
        quote_mode: note.quote_mode,
        target_key: note.target_key,
        sentence_id: note.sentence_id ?? undefined,
        selected_text: note.selected_text,
        start_offset: note.start_offset ?? undefined,
        end_offset: note.end_offset ?? undefined,
        text_hash: note.text_hash ?? undefined,
        segments: note.segments,
        note_text: note.note_text,
      })
      addLocalReaderNote(created)
    } else {
      await updateReaderNote(note.id, { note_text: note.note_text })
      updateLocalReaderNote(note.id, note)
    }
  } catch (err) {
    console.warn('[cloudSync] executeUpsertNote failed:', err)
    throw err
  }
}

async function executeDeleteNote(item: SyncQueueItem): Promise<void> {
  const { noteId } = item.payload as { noteId: string }
  if (!noteId) return

  try {
    await deleteReaderNote(noteId)
    removeLocalReaderNote(noteId)
  } catch (err) {
    console.warn('[cloudSync] executeDeleteNote failed:', err)
    throw err
  }
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
   * 同步新建高亮到云端
   */
  async syncAnnotation(annotation: UserAnnotationDto, isNew = true): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    addLocalUserAnnotation(annotation)

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'annotation',
      entityId: annotation.id,
      action: 'UPSERT_ANNOTATION',
      payload: { annotation, isNew },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

    flushQueue()
  },

  /**
   * 同步删除高亮到云端
   */
  async syncDeleteAnnotation(annotationId: string): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    removeLocalUserAnnotation(annotationId)

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'annotation',
      entityId: annotationId,
      action: 'DELETE_ANNOTATION',
      payload: { annotationId },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

    flushQueue()
  },

  /**
   * 同步新建笔记到云端
   */
  async syncNote(note: ReaderNoteDto, isNew = true): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    addLocalReaderNote(note)

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'note',
      entityId: note.id,
      action: 'UPSERT_NOTE',
      payload: { note, isNew },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

    flushQueue()
  },

  /**
   * 同步删除笔记到云端
   */
  async syncDeleteNote(noteId: string): Promise<void> {
    if (!useAuthStore.getState().isLoggedIn) return

    removeLocalReaderNote(noteId)

    enqueueSyncItem({
      opId: generateOpId(),
      entityType: 'note',
      entityId: noteId,
      action: 'DELETE_NOTE',
      payload: { noteId },
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })

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