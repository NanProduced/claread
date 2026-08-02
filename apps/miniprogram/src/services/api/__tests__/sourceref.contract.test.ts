/**
 * SourceRef round-trip contract test
 *
 * CUTOVER-MINI-LONG: 锁定 vocabulary.cloud payload <-> VocabEntry SourceRef
 * 的 canonical 字段契约：
 *   - reading_record_id  <-> readingRecordId
 *   - daily_reader_article_id <-> dailyReaderArticleId
 *
 * 旧 client_record_id / cloud_record_id 字段已从客户端写入路径移除；
 * parseSourceRefs 仅保留对历史 cloud payload 的向后兼容读取。
 *
 * 注意：本文件为 contract 文档；小程序包当前无 test runner，
 * 由总策划在 evals 或 web 包中复用此契约时落地运行。
 * 字段命名锁定后，API owner 需补 daily_reader_article_id schema + merge key。
 */

import type { VocabEntry, SourceRef } from '../../../types/view/vocabulary.vm'

// Mirror of vocabulary.client.ts parseSourceRefs (kept in sync manually)
interface SourceRefDto {
  reading_record_id?: string | null
  client_record_id?: string
  daily_reader_article_id?: string | null
  source_sentence?: string | null
  source_context?: string | null
  source_sentence_id?: string | null
  source_anchor_text?: string | null
  source_occurrence?: number | null
  collected_at?: string | null
}

function parseSourceRefs(payload: { source_refs?: SourceRefDto[] } | undefined): SourceRef[] {
  if (!payload?.source_refs || !Array.isArray(payload.source_refs)) return []
  return payload.source_refs.map((ref) => ({
    readingRecordId: ref.reading_record_id || ref.client_record_id || undefined,
    dailyReaderArticleId: ref.daily_reader_article_id || undefined,
    sourceSentence: ref.source_sentence || undefined,
    sourceContext: ref.source_context || undefined,
    sourceSentenceId: ref.source_sentence_id || undefined,
    sourceAnchorText: ref.source_anchor_text || undefined,
    sourceOccurrence: ref.source_occurrence || undefined,
    collectedAt: ref.collected_at || undefined,
  }))
}

// Mirror of vocabulary.client.ts addVocabToCloud sourceRefs emission
function emitSourceRefs(refs: SourceRef[]): SourceRefDto[] {
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

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`ASSERT FAILED: ${msg}`)
}

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

function runRoundTrip(label: string, refs: SourceRef[]): void {
  const emitted = emitSourceRefs(refs)
  const payload = { source_refs: emitted }
  const parsed = parseSourceRefs(payload)
  assert(deepEqual(parsed, refs), `${label}: round-trip mismatch`)
  // Canonical key assertions
  for (const dto of emitted) {
    assert(!('client_record_id' in dto) || dto.client_record_id === undefined,
      `${label}: client_record_id must not be emitted`)
    assert(!('cloud_record_id' in dto),
      `${label}: cloud_record_id must not be emitted`)
  }
}

// ---------------------------------------------------------------------------
// Test cases
// ---------------------------------------------------------------------------

// 1. Daily Reader article source (canonical path after cutover)
runRoundTrip('daily-reader-source', [
  {
    dailyReaderArticleId: 'dr_2026_08_02_abc',
    sourceSentence: 'The committee adopted the new policy.',
    sourceSentenceId: 's_42',
    sourceAnchorText: 'adopted',
    sourceOccurrence: 1,
    collectedAt: '2026-08-02T10:00:00Z',
  },
])

// 2. Reading record source (legacy analysis record; link-only, no jump)
runRoundTrip('reading-record-source', [
  {
    readingRecordId: 'rec_001',
    sourceSentence: 'The policy was adopted.',
    collectedAt: '2026-07-31T08:00:00Z',
  },
])

// 3. Mixed sources
runRoundTrip('mixed-sources', [
  {
    dailyReaderArticleId: 'dr_2026_08_01_xyz',
    sourceSentence: 'Adopted from the original text.',
    sourceSentenceId: 's_7',
    sourceAnchorText: 'Adopted',
    sourceOccurrence: 1,
    collectedAt: '2026-08-01T12:00:00Z',
  },
  {
    readingRecordId: 'rec_legacy_002',
    sourceSentence: 'The adoption was swift.',
    collectedAt: '2026-07-15T09:30:00Z',
  },
])

// 4. Empty source
runRoundTrip('empty-source', [{}])

// 5. Backward-compat: legacy cloud payload with client_record_id still parses
{
  const legacyPayload = {
    source_refs: [
      {
        client_record_id: 'legacy_rec_003',
        daily_reader_article_id: 'dr_2026_07_30_old',
        source_sentence: 'Legacy sentence.',
        collected_at: '2026-07-30T00:00:00Z',
      },
    ],
  }
  const parsed = parseSourceRefs(legacyPayload)
  assert(parsed[0].readingRecordId === 'legacy_rec_003',
    'backward-compat: client_record_id must map to readingRecordId')
  assert(parsed[0].dailyReaderArticleId === 'dr_2026_07_30_old',
    'backward-compat: daily_reader_article_id must parse')
}

// 6. No source_refs
{
  const parsed = parseSourceRefs(undefined)
  assert(parsed.length === 0, 'undefined payload must yield empty array')
}

// Suppress unused import warning (VocabEntry kept for future expansion of contract tests)
export type _VocabEntryRef = VocabEntry

console.log('sourceref.contract.test.ts: all assertions passed')