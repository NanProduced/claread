/**
 * 存储服务
 *
 * 统一封装所有 Taro.setStorageSync/getStorageSync 调用
 * 禁止在其他地方直接调用 storage API
 *
 * CUTOVER-MINI-LONG: 旧 analysis 草稿/记录/identity-map/sync-queue/本地注记缓存
 * 随旧 analysis 主链下线已移除。仅保留 favorites + vocabulary 分片存储。
 */

import Taro from '@tarojs/taro'
import type { FavoriteRecord } from '../../types/view/favorites.vm'
import type { VocabEntry, SourceRef, SaveVocabResult } from '../../types/view/vocabulary.vm'

// ============ Key 定义 ============

const KEYS = {
  FAVORITES: 'favorite_records',
  VOCAB_IDS: 'vocab_ids',
  VOCAB_ENTRY: (id: string) => `vocab_entry_${id}`,
  VOCAB_LEMMA_INDEX: 'vocab_lemma_index',
  VOCAB_INSPECT_ENTRY: 'vocab_inspect_entry',
} as const

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
    const key = `${ref.dailyReaderArticleId || ref.readingRecordId || ''}|${ref.sourceSentenceId || ''}`
    map.set(key, ref)
  }
  for (const ref of incoming) {
    const key = `${ref.dailyReaderArticleId || ref.readingRecordId || ''}|${ref.sourceSentenceId || ''}`
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