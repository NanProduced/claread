/**
 * 词典查询内存缓存
 *
 * 纯内存 Map 缓存词典查询结果，不占用 Taro Storage 空间。
 * TTL 5 分钟，最大 200 条，LRU 淘汰。
 * 仅缓存 entry 类型结果（disambiguation 结果较小，不缓存）。
 */

import type { DictionaryResult } from '../types/view/render-scene.vm'

const TTL_MS = 5 * 60 * 1000
const MAX_SIZE = 200

interface CacheEntry {
  data: DictionaryResult
  expiry: number
}

const _cache = new Map<string, CacheEntry>()

function touchEntry(key: string, entry: CacheEntry): void {
  _cache.delete(key)
  _cache.set(key, entry)
}

function evictIfNeeded(): void {
  if (_cache.size < MAX_SIZE) return

  const now = Date.now()
  const expired: string[] = []
  for (const [k, v] of _cache) {
    if (now > v.expiry) expired.push(k)
  }
  for (const k of expired) _cache.delete(k)

  if (_cache.size >= MAX_SIZE) {
    const oldest = _cache.keys().next().value
    if (oldest !== undefined) _cache.delete(oldest)
  }
}

function buildKey(
  text: string,
  type: 'word' | 'phrase',
  contextSentence?: string,
  occurrence?: number,
): string {
  return `${text}:${type}:${contextSentence || ''}:${occurrence || 0}`
}

export function getDictCache(
  text: string,
  type: 'word' | 'phrase',
  contextSentence?: string,
  occurrence?: number,
): DictionaryResult | null {
  const key = buildKey(text, type, contextSentence, occurrence)
  const entry = _cache.get(key)
  if (!entry) return null
  if (Date.now() > entry.expiry) {
    _cache.delete(key)
    return null
  }
  touchEntry(key, entry)
  return entry.data
}

export function setDictCache(
  text: string,
  type: 'word' | 'phrase',
  data: DictionaryResult,
  contextSentence?: string,
  occurrence?: number,
): void {
  if (data.resultType !== 'entry') return

  const key = buildKey(text, type, contextSentence, occurrence)
  evictIfNeeded()
  _cache.set(key, { data, expiry: Date.now() + TTL_MS })
}

export function getEntryCache(entryId: number): DictionaryResult | null {
  const key = `__entry__:${entryId}`
  const entry = _cache.get(key)
  if (!entry) return null
  if (Date.now() > entry.expiry) {
    _cache.delete(key)
    return null
  }
  touchEntry(key, entry)
  return entry.data
}

export function setEntryCache(entryId: number, data: DictionaryResult): void {
  if (data.resultType !== 'entry') return

  const key = `__entry__:${entryId}`
  evictIfNeeded()
  _cache.set(key, { data, expiry: Date.now() + TTL_MS })
}

export function clearDictCache(): void {
  _cache.clear()
}

export function getDictCacheStats(): { size: number; maxSize: number } {
  return { size: _cache.size, maxSize: MAX_SIZE }
}
