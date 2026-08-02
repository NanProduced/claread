/**
 * SourceRef Production Codec
 *
 * CUTOVER-MINI-LONG: vocabulary.cloud payload <-> VocabEntry SourceRef 的唯一
 * canonical 编解码实现。vocabulary.client 与 contract test 共用此模块，
 * 禁止在调用方复制实现。
 *
 * Canonical 字段契约（严格单轨）：
 *   - reading_record_id      <-> readingRecordId
 *   - daily_reader_article_id <-> dailyReaderArticleId
 *
 * 旧 client_record_id / cloud_record_id 已从读写路径完全移除。
 * API owner 需补 daily_reader_article_id schema + merge key。
 */

import type { SourceRef } from '../../types/view/vocabulary.vm'

/** 后端 DTO（snake_case）。client_record_id 不再读取。 */
export interface SourceRefDto {
  reading_record_id?: string | null
  daily_reader_article_id?: string | null
  source_sentence?: string | null
  source_context?: string | null
  source_sentence_id?: string | null
  source_anchor_text?: string | null
  source_occurrence?: number | null
  collected_at?: string | null
}

/**
 * 从云端 payload 解析 SourceRef（严格单轨，无 client_record_id fallback）。
 * 接受 unknown 以兼容 Record<string,unknown>（vocabulary.client）和
 * 强类型 payload（contract test）。
 */
export function parseSourceRefs(payload: unknown): SourceRef[] {
  if (!payload || typeof payload !== 'object') return []
  const obj = payload as { source_refs?: unknown }
  if (!obj.source_refs || !Array.isArray(obj.source_refs)) return []
  const refs = obj.source_refs as SourceRefDto[]
  return refs.map((ref) => ({
    readingRecordId: ref.reading_record_id || undefined,
    dailyReaderArticleId: ref.daily_reader_article_id || undefined,
    sourceSentence: ref.source_sentence || undefined,
    sourceContext: ref.source_context || undefined,
    sourceSentenceId: ref.source_sentence_id || undefined,
    sourceAnchorText: ref.source_anchor_text || undefined,
    sourceOccurrence: ref.source_occurrence || undefined,
    collectedAt: ref.collected_at || undefined,
  }))
}

/** 将 SourceRef 编码为云端写入 payload（严格单轨，不输出 client_record_id）。 */
export function emitSourceRefs(refs: SourceRef[]): SourceRefDto[] {
  return refs.map(ref => ({
    reading_record_id: ref.readingRecordId || null,
    daily_reader_article_id: ref.dailyReaderArticleId || null,
    source_sentence: ref.sourceSentence || null,
    source_context: ref.sourceContext || null,
    source_sentence_id: ref.sourceSentenceId || null,
    source_anchor_text: ref.sourceAnchorText || null,
    source_occurrence: ref.sourceOccurrence || null,
    collected_at: ref.collectedAt || null,
  }))
}