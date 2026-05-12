/**
 * 存储服务
 *
 * 统一封装所有 Taro.setStorageSync/getStorageSync 调用
 * 禁止在其他地方直接调用 storage API
 *
 * 3 类 key 分离：
 * - article_draft:          输入草稿（高频读写，体积小）
 * - analysis_record_ids:     历史记录 ID 有序列表
 * - analysis_record_{id}:    单条分析快照（按需懒加载）
 * - user_preferences:       用户偏好、onboarding 状态
 */

import Taro from '@tarojs/taro'
import type { AnalysisRecord } from '../../types/view/analysis-record.vm'
import type { FavoriteRecord } from '../../types/view/favorites.vm'
import type { VocabEntry, SourceRef, SaveVocabResult } from '../../types/view/vocabulary.vm'
import type { AnalyzeRequest } from '../api'
import type { AnyRenderSceneVm, ResultPageState } from '../../types/view/render-scene.vm'

// ============ Key 定义 ============

const KEYS = {
  DRAFT: 'article_draft',
  RECORD_IDS: 'analysis_record_ids',
  RECORD: (id: string) => `analysis_record_${id}`,
  FAVORITES: 'favorite_records',
  VOCAB_IDS: 'vocab_ids',
  VOCAB_ENTRY: (id: string) => `vocab_entry_${id}`,
  VOCAB_LEMMA_INDEX: 'vocab_lemma_index',
  VOCAB_INSPECT_ENTRY: 'vocab_inspect_entry',
  USER_PREF: 'user_preferences',
  RECORD_IDENTITY_MAP: 'record_identity_map',
  SYNC_QUEUE: 'sync_queue',
} as const

// ============ Article Draft ============

export interface ArticleDraft {
  text: string
  reading_goal: AnalyzeRequest['reading_goal']
  reading_variant: AnalyzeRequest['reading_variant']
  savedAt: number
}

export function saveDraft(draft: ArticleDraft): void {
  try {
    Taro.setStorageSync(KEYS.DRAFT, draft)
  } catch (e) {
    console.error('[storage] saveDraft failed', e)
  }
}

export function getDraft(): ArticleDraft | null {
  try {
    const raw = Taro.getStorageSync(KEYS.DRAFT)
    return raw || null
  } catch (e) {
    console.error('[storage] getDraft failed', e)
    return null
  }
}

export function clearDraft(): void {
  try {
    Taro.removeStorageSync(KEYS.DRAFT)
  } catch (e) {
    console.error('[storage] clearDraft failed', e)
  }
}

// ============ Analysis Records ============

/**
 * 生成唯一 ID
 */
export function generateRecordId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

/**
 * 获取所有历史记录 ID（按时间倒序）
 */
export function getRecordIds(): string[] {
  try {
    const raw = Taro.getStorageSync<string[]>(KEYS.RECORD_IDS)
    return raw || []
  } catch (e) {
    console.error('[storage] getRecordIds failed', e)
    return []
  }
}

/**
 * 获取单条分析记录
 */
export function getRecord(id: string): AnalysisRecord | null {
  try {
    const raw = Taro.getStorageSync<AnalysisRecord>(KEYS.RECORD(id))
    return raw || null
  } catch (e) {
    console.error('[storage] getRecord failed', e)
    return null
  }
}

/**
 * 保存分析记录（追加到列表头部）
 */
export function saveRecord(record: AnalysisRecord): void {
  try {
    Taro.setStorageSync(KEYS.RECORD(record.recordId), record)

    const ids = getRecordIds()
    const filtered = ids.filter((id) => id !== record.recordId)
    Taro.setStorageSync(KEYS.RECORD_IDS, [record.recordId, ...filtered])

    enforceRecordLimit()
  } catch (e) {
    console.error('[storage] saveRecord failed', e)
  }
}

/**
 * 更新已有记录（如收藏状态变化）
 */
export function updateRecord(id: string, patch: Partial<AnalysisRecord>): void {
  try {
    const record = getRecord(id)
    if (!record) return
    const updated = { ...record, ...patch, updatedAt: Date.now() }
    Taro.setStorageSync(KEYS.RECORD(id), updated)
  } catch (e) {
    console.error('[storage] updateRecord failed', e)
  }
}

/**
 * 删除记录
 */
export function deleteRecord(id: string): void {
  try {
    Taro.removeStorageSync(KEYS.RECORD(id))
    const ids = getRecordIds().filter((recordId) => recordId !== id)
    Taro.setStorageSync(KEYS.RECORD_IDS, ids)
  } catch (e) {
    console.error('[storage] deleteRecord failed', e)
  }
}

/**
 * 获取所有记录（按时间倒序）
 * 注意：大列表场景建议用 getRecordIds + 按需加载单条
 */
export function getAllRecords(): AnalysisRecord[] {
  const ids = getRecordIds()
  const records: AnalysisRecord[] = []
  for (const id of ids) {
    const record = getRecord(id)
    if (record) records.push(record)
  }
  return records
}

// ============ Favorites ============

export function getFavorites(): FavoriteRecord[] {
  try {
    const raw = Taro.getStorageSync<FavoriteRecord[]>(KEYS.FAVORITES)
    return raw || []
  } catch (e) {
    console.error('[storage] getFavorites failed', e)
    return []
  }
}

export function saveFavorite(favorite: FavoriteRecord): void {
  try {
    const favorites = getFavorites()
    const exists = favorites.some((f) => f.recordId === favorite.recordId)
    if (exists) return
    Taro.setStorageSync(KEYS.FAVORITES, [favorite, ...favorites])
  } catch (e) {
    console.error('[storage] saveFavorite failed', e)
  }
}

export function removeFavorite(recordId: string): void {
  try {
    const favorites = getFavorites().filter((f) => f.recordId !== recordId)
    Taro.setStorageSync(KEYS.FAVORITES, favorites)
  } catch (e) {
    console.error('[storage] removeFavorite failed', e)
  }
}

export function isFavorited(recordId: string): boolean {
  return getFavorites().some((f) => f.recordId === recordId)
}

// ============ Vocabulary (sharded) ============

const SOURCE_REFS_MAX = 20
const FIRST_REVIEW_DELAY_MS = 24 * 60 * 60 * 1000

function _initialReviewPatch(entry: VocabEntry): Partial<VocabEntry> {
  if (entry.mastered || entry.masteryStatus === 'mastered') {
    return {
      masteryStatus: 'mastered',
      reviewStage: entry.reviewStage ?? 0,
      nextReviewAt: entry.nextReviewAt,
      reviewCount: entry.reviewCount ?? 0,
    }
  }
  return {
    masteryStatus: entry.masteryStatus || 'new',
    reviewStage: entry.reviewStage ?? 0,
    nextReviewAt: entry.nextReviewAt || new Date(Date.now() + FIRST_REVIEW_DELAY_MS).toISOString(),
    reviewCount: entry.reviewCount ?? 0,
  }
}

function _getVocabIds(): string[] {
  try {
    const raw = Taro.getStorageSync<string[]>(KEYS.VOCAB_IDS)
    return raw || []
  } catch (e) {
    console.error('[storage] _getVocabIds failed', e)
    return []
  }
}

function _saveVocabIds(ids: string[]): void {
  Taro.setStorageSync(KEYS.VOCAB_IDS, ids)
}

function _getVocabLemmaIndex(): Record<string, string> {
  try {
    const raw = Taro.getStorageSync<Record<string, string>>(KEYS.VOCAB_LEMMA_INDEX)
    return raw || {}
  } catch (e) {
    console.error('[storage] _getVocabLemmaIndex failed', e)
    return {}
  }
}

function _saveVocabLemmaIndex(index: Record<string, string>): void {
  Taro.setStorageSync(KEYS.VOCAB_LEMMA_INDEX, index)
}

function _getVocabEntry(id: string): VocabEntry | null {
  try {
    const raw = Taro.getStorageSync<VocabEntry>(KEYS.VOCAB_ENTRY(id))
    return raw || null
  } catch (e) {
    console.error('[storage] _getVocabEntry failed', e)
    return null
  }
}

function _saveVocabEntry(entry: VocabEntry): void {
  Taro.setStorageSync(KEYS.VOCAB_ENTRY(entry.id), entry)
}

function _removeVocabEntry(id: string): void {
  Taro.removeStorageSync(KEYS.VOCAB_ENTRY(id))
}

export function getVocabulary(): VocabEntry[] {
  const ids = _getVocabIds()
  const entries: VocabEntry[] = []
  for (const id of ids) {
    const entry = _getVocabEntry(id)
    if (entry) entries.push(entry)
  }
  return entries
}

export function saveVocabEntry(entry: VocabEntry): SaveVocabResult {
  try {
    const entryLemma = (entry.lemma || entry.word).toLowerCase()
    const lemmaIndex = _getVocabLemmaIndex()
    const existingId = lemmaIndex[entryLemma]

    if (existingId) {
      const existing = _getVocabEntry(existingId)
      if (existing && !existing.tombstone) {
        const mergedRefs = mergeSourceRefs(existing.sourceRefs || [], entry.sourceRefs || [])
        const mergedForms = mergeCollectedForms(existing.collectedForms || [], entry.word)
        const merged: VocabEntry = {
          ...existing,
          word: entry.word,
          partOfSpeech: entry.partOfSpeech || existing.partOfSpeech,
          meaning: entry.meaning || existing.meaning,
          detailMeanings: entry.detailMeanings || existing.detailMeanings,
          detailPhrases: entry.detailPhrases || existing.detailPhrases,
          detailExamples: entry.detailExamples || existing.detailExamples,
          phonetic: entry.phonetic || existing.phonetic,
          tags: entry.tags || existing.tags,
          exchange: entry.exchange || existing.exchange,
          dictEntryId: entry.dictEntryId ?? existing.dictEntryId,
          sentence: entry.sentence ?? existing.sentence,
          context: entry.context ?? existing.context,
          sourceRefs: mergedRefs,
          collectedForms: mergedForms,
          audioUrl: entry.audioUrl || existing.audioUrl,
          addedAt: existing.addedAt,
        }
        _saveVocabEntry(merged)
        return {
          entry: merged,
          merged: true,
          totalSourceCount: mergedRefs.length,
        }
      }
      // Bug 7: lemma index points to a tombstone entry — clean it up so a new entry with the same lemma can be added
      if (existing && existing.tombstone) {
        const ids = _getVocabIds().filter((vId) => vId !== existingId)
        _saveVocabIds(ids)
        _removeVocabEntry(existingId)
      }
    }

    const newEntry: VocabEntry = {
      ...entry,
      ..._initialReviewPatch(entry),
      sourceRefs: entry.sourceRefs || [],
      collectedForms: entry.collectedForms || (entry.word ? [entry.word] : []),
    }

    const ids = _getVocabIds()
    _saveVocabIds([newEntry.id, ...ids])
    _saveVocabEntry(newEntry)
    lemmaIndex[entryLemma] = newEntry.id
    _saveVocabLemmaIndex(lemmaIndex)

    return {
      entry: newEntry,
      merged: false,
      totalSourceCount: newEntry.sourceRefs?.length || 0,
    }
  } catch (e) {
    console.error('[storage] saveVocabEntry failed', e)
    return { entry, merged: false, totalSourceCount: 0 }
  }
}

function mergeSourceRefs(
  existing: SourceRef[],
  incoming: SourceRef[]
): SourceRef[] {
  const map = new Map<string, SourceRef>()
  for (const ref of existing) {
    const key = `${ref.clientRecordId}|${ref.sourceSentenceId || ''}`
    map.set(key, ref)
  }
  for (const ref of incoming) {
    const key = `${ref.clientRecordId}|${ref.sourceSentenceId || ''}`
    if (!map.has(key)) {
      map.set(key, ref)
    }
  }
  const all = Array.from(map.values())
  if (all.length > SOURCE_REFS_MAX) {
    return all.slice(-SOURCE_REFS_MAX)
  }
  return all
}

function mergeCollectedForms(existing: string[], incomingWord: string): string[] {
  const set = new Set(existing.map(f => f.toLowerCase()))
  const result = [...existing]
  if (incomingWord && !set.has(incomingWord.toLowerCase())) {
    result.push(incomingWord)
  }
  return result
}

export function removeVocabEntry(id: string): void {
  try {
    const entry = _getVocabEntry(id)
    const ids = _getVocabIds().filter((vId) => vId !== id)
    _saveVocabIds(ids)
    _removeVocabEntry(id)

    if (entry) {
      const lemma = (entry.lemma || entry.word).toLowerCase()
      const lemmaIndex = _getVocabLemmaIndex()
      if (lemmaIndex[lemma] === id) {
        delete lemmaIndex[lemma]
        _saveVocabLemmaIndex(lemmaIndex)
      }
    }
  } catch (e) {
    console.error('[storage] removeVocabEntry failed', e)
  }
}

export function updateVocabEntry(id: string, updates: Partial<VocabEntry>): void {
  try {
    const entry = _getVocabEntry(id)
    if (!entry) return
    const updated = { ...entry, ...updates }
    _saveVocabEntry(updated)

    if (updates.lemma !== undefined || updates.word !== undefined) {
      const newLemma = (updates.lemma || updates.word || entry.lemma || entry.word).toLowerCase()
      const oldLemma = (entry.lemma || entry.word).toLowerCase()
      if (newLemma !== oldLemma) {
        const lemmaIndex = _getVocabLemmaIndex()
        delete lemmaIndex[oldLemma]
        lemmaIndex[newLemma] = id
        _saveVocabLemmaIndex(lemmaIndex)
      }
    }
  } catch (e) {
    console.error('[storage] updateVocabEntry failed', e)
  }
}

export function getVocabCount(): number {
  return _getVocabIds().length
}

export function isVocabByLemma(lemma: string): boolean {
  const lemmaIndex = _getVocabLemmaIndex()
  const id = lemmaIndex[lemma.toLowerCase()]
  if (!id) return false
  const entry = _getVocabEntry(id)
  return entry !== null && !entry.tombstone
}

export function getVocabLemmaSet(): Set<string> {
  return new Set(Object.keys(_getVocabLemmaIndex()))
}

export function getVocabEntryByLemma(lemma: string): VocabEntry | null {
  const lemmaIndex = _getVocabLemmaIndex()
  const id = lemmaIndex[lemma.toLowerCase()]
  if (!id) return null
  const entry = _getVocabEntry(id)
  if (!entry || entry.tombstone) return null
  return entry
}

export function saveVocabInspectEntry(entry: VocabEntry): void {
  try {
    Taro.setStorageSync(KEYS.VOCAB_INSPECT_ENTRY, entry)
  } catch (e) {
    console.error('[storage] saveVocabInspectEntry failed', e)
  }
}

export function getVocabInspectEntry(): VocabEntry | null {
  try {
    return Taro.getStorageSync<VocabEntry>(KEYS.VOCAB_INSPECT_ENTRY) || null
  } catch (e) {
    console.error('[storage] getVocabInspectEntry failed', e)
    return null
  }
}

export function getVocabEntryByLookupForm(form: string): VocabEntry | null {
  const normalized = form.trim().toLowerCase()
  if (!normalized) return null

  const byLemma = getVocabEntryByLemma(normalized)
  if (byLemma) return byLemma

  return getVocabulary().find((entry) => {
    if (entry.tombstone) return false
    if (entry.word.toLowerCase() === normalized) return true
    if (entry.lemma.toLowerCase() === normalized) return true
    return entry.collectedForms?.some((item) => item.toLowerCase() === normalized) ?? false
  }) || null
}

// ============ User Preferences ============

export interface UserPreferences {
  purpose?: 'daily' | 'exam' | 'academic'
  level?: string
  configured?: boolean
}

export function getUserPreferences(): UserPreferences {
  try {
    const raw = Taro.getStorageSync<UserPreferences>(KEYS.USER_PREF)
    return raw || {}
  } catch (e) {
    console.error('[storage] getUserPreferences failed', e)
    return {}
  }
}

export function saveUserPreferences(pref: Partial<UserPreferences>): void {
  try {
    const current = getUserPreferences()
    Taro.setStorageSync(KEYS.USER_PREF, { ...current, ...pref })
  } catch (e) {
    console.error('[storage] saveUserPreferences failed', e)
  }
}

// ============ Record Identity Map ============

export interface RecordIdentityMap {
  [clientRecordId: string]: string
}

export function getRecordIdentityMap(): RecordIdentityMap {
  try {
    const raw = Taro.getStorageSync<RecordIdentityMap>(KEYS.RECORD_IDENTITY_MAP)
    return raw || {}
  } catch (e) {
    console.error('[storage] getRecordIdentityMap failed', e)
    return {}
  }
}

export function saveRecordIdentity(clientRecordId: string, cloudRecordId: string): void {
  try {
    const map = getRecordIdentityMap()
    map[clientRecordId] = cloudRecordId
    Taro.setStorageSync(KEYS.RECORD_IDENTITY_MAP, map)
  } catch (e) {
    console.error('[storage] saveRecordIdentity failed', e)
  }
}

export function resolveCloudIdFromMap(clientRecordId: string): string | null {
  const map = getRecordIdentityMap()
  return map[clientRecordId] || null
}

export function resolveClientIdFromMap(cloudRecordId: string): string | null {
  const map = getRecordIdentityMap()
  for (const [clientId, cloudId] of Object.entries(map)) {
    if (cloudId === cloudRecordId) return clientId
  }
  return null
}

// ============ Sync Queue ============

export interface SyncQueueItem {
  opId: string
  entityType: 'record' | 'favorite' | 'vocab'
  entityId: string
  action: string
  payload: Record<string, unknown>
  dependsOn?: string[]
  status: 'pending' | 'running' | 'failed' | 'done'
  retryCount: number
  nextRetryAt?: number
  lastError?: string | null
  createdAt: number
  updatedAt: number
}

export function getSyncQueue(): SyncQueueItem[] {
  try {
    const raw = Taro.getStorageSync<SyncQueueItem[]>(KEYS.SYNC_QUEUE)
    return raw || []
  } catch (e) {
    console.error('[storage] getSyncQueue failed', e)
    return []
  }
}

export function saveSyncQueue(queue: SyncQueueItem[]): void {
  try {
    Taro.setStorageSync(KEYS.SYNC_QUEUE, queue)
  } catch (e) {
    console.error('[storage] saveSyncQueue failed', e)
  }
}

export function enqueueSyncItem(item: SyncQueueItem): void {
  const queue = getSyncQueue()
  queue.push(item)
  saveSyncQueue(queue)
}

export function updateSyncQueueItem(opId: string, updates: Partial<SyncQueueItem>): void {
  const queue = getSyncQueue()
  const idx = queue.findIndex(item => item.opId === opId)
  if (idx > -1) {
    queue[idx] = { ...queue[idx], ...updates, updatedAt: Date.now() }
    saveSyncQueue(queue)
  }
}

export function removeSyncQueueItem(opId: string): void {
  const queue = getSyncQueue().filter(item => item.opId !== opId)
  saveSyncQueue(queue)
}

export function getPendingSyncItems(): SyncQueueItem[] {
  return getSyncQueue().filter(item => item.status === 'pending')
}

// ============ Storage Capacity ============

const STORAGE_LIMIT_MB = 10
const WARNING_THRESHOLD = 0.8

export interface StorageCapacityInfo {
  usedKB: number
  limitKB: number
  usageRatio: number
  isNearLimit: boolean
  keys: number
}

export function getStorageCapacity(): StorageCapacityInfo {
  try {
    const res = Taro.getStorageInfoSync()
    const usedKB = res.currentSize || 0
    const limitKB = res.limitSize || (STORAGE_LIMIT_MB * 1024)
    const usageRatio = limitKB > 0 ? usedKB / limitKB : 0
    return {
      usedKB,
      limitKB,
      usageRatio,
      isNearLimit: usageRatio >= WARNING_THRESHOLD,
      keys: res.keys?.length || 0,
    }
  } catch (e) {
    console.error('[storage] getStorageCapacity failed', e)
    return { usedKB: 0, limitKB: STORAGE_LIMIT_MB * 1024, usageRatio: 0, isNearLimit: false, keys: 0 }
  }
}

const MAX_RECORDS = 200

export function enforceRecordLimit(): void {
  const capacity = getStorageCapacity()
  if (!capacity.isNearLimit) return

  const ids = getRecordIds()
  if (ids.length <= MAX_RECORDS) return

  const toRemove = ids.slice(MAX_RECORDS)
  for (const id of toRemove) {
    deleteRecord(id)
  }
  console.warn(`[storage] cleaned ${toRemove.length} old records (storage near limit)`)
}
