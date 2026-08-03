/**
 * SourceRef round-trip contract test
 *
 * CUTOVER-MINI-LONG: 直接导入 production codec (sourceref-codec.ts)，
 * 不复制实现。验证 vocabulary.cloud payload <-> VocabEntry SourceRef 的
 * canonical 字段契约：
 *   - reading_record_id      <-> readingRecordId
 *   - daily_reader_article_id <-> dailyReaderArticleId
 *
 * 旧 client_record_id 已从读写路径完全移除；本测试验证 codec 不再读取
 * 也不输出 client_record_id。
 *
 * 运行：pnpm test:sourceref （使用 npx tsx 执行）
 */

import { parseSourceRefs, emitSourceRefs, type SourceRefDto } from '../sourceref-codec'
import type { SourceRef } from '../../../types/view/vocabulary.vm'

function assert(cond: boolean, msg: string): void {
  if (!cond) {
    console.error(`FAIL: ${msg}`)
    process.exit(1)
  }
}

function assertEqual<T>(actual: T, expected: T, msg: string): void {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  if (a !== e) {
    console.error(`FAIL: ${msg}\n  actual:   ${a}\n  expected: ${e}`)
    process.exit(1)
  }
}

let passCount = 0
function pass(msg: string): void {
  passCount++
  console.log(`  ok - ${msg}`)
}

// ============ 1. parse: canonical reading_record_id ============

{
  const payload = {
    source_refs: [{
      reading_record_id: 'rec_001',
      daily_reader_article_id: 'dr_201',
      source_sentence: 'Adopted by the committee.',
      source_sentence_id: 's1',
      source_anchor_text: 'adopted',
      source_occurrence: 1,
      collected_at: '2026-01-01T00:00:00Z',
    }],
  }
  const refs = parseSourceRefs(payload)
  assert(refs.length === 1, 'parse: should return 1 ref')
  assertEqual(refs[0].readingRecordId, 'rec_001', 'parse: readingRecordId')
  assertEqual(refs[0].dailyReaderArticleId, 'dr_201', 'parse: dailyReaderArticleId')
  assertEqual(refs[0].sourceSentenceId, 's1', 'parse: sourceSentenceId')
  pass('parse canonical reading_record_id + daily_reader_article_id')
}

// ============ 2. emit: canonical fields only, no client_record_id ============

{
  const refs: SourceRef[] = [{
    readingRecordId: 'rec_002',
    dailyReaderArticleId: 'dr_202',
    sourceSentence: 'The policy was adopted.',
    sourceAnchorText: 'adopted',
    sourceOccurrence: 2,
    collectedAt: '2026-02-01T00:00:00Z',
  }]
  const dtos = emitSourceRefs(refs)
  assert(dtos.length === 1, 'emit: should return 1 dto')
  assertEqual(dtos[0].reading_record_id, 'rec_002', 'emit: reading_record_id')
  assertEqual(dtos[0].daily_reader_article_id, 'dr_202', 'emit: daily_reader_article_id')
  assert(!('client_record_id' in dtos[0]), 'emit: must not output client_record_id')
  pass('emit canonical fields only, no client_record_id')
}

// ============ 3. round-trip: emit -> parse is lossless ============

{
  const original: SourceRef[] = [
    { readingRecordId: 'rec_003', dailyReaderArticleId: 'dr_203', sourceSentenceId: 's3', collectedAt: '2026-03-01T00:00:00Z' },
    { dailyReaderArticleId: 'dr_204', sourceAnchorText: 'policy', collectedAt: '2026-03-02T00:00:00Z' },
  ]
  const emitted = emitSourceRefs(original)
  const roundTripped = parseSourceRefs({ source_refs: emitted })
  assertEqual(roundTripped, original, 'round-trip: emit -> parse lossless')
  pass('round-trip emit -> parse lossless')
}

// ============ 4. parse: empty / missing source_refs ============

{
  assertEqual(parseSourceRefs(undefined), [], 'parse undefined -> []')
  assertEqual(parseSourceRefs({}), [], 'parse {} -> []')
  assertEqual(parseSourceRefs({ source_refs: [] }), [], 'parse empty array -> []')
  pass('parse empty / missing source_refs')
}

// ============ 5. parse: client_record_id is NOT read (strict single-track) ============

{
  // Legacy payload with ONLY client_record_id (no reading_record_id).
  // Strict codec must NOT fall back to client_record_id.
  const legacyPayload = {
    source_refs: [{
      client_record_id: 'legacy_rec_004',
      daily_reader_article_id: 'dr_204',
    } as SourceRefDto],
  }
  const refs = parseSourceRefs(legacyPayload)
  assert(refs.length === 1, 'parse legacy: should return 1 ref')
  assertEqual(refs[0].readingRecordId, undefined, 'parse legacy: client_record_id must NOT map to readingRecordId')
  assertEqual(refs[0].dailyReaderArticleId, 'dr_204', 'parse legacy: dailyReaderArticleId still read')
  pass('parse: client_record_id NOT read (strict single-track)')
}

// ============ 6. emit: null/undefined fields -> null in DTO ============

{
  const refs: SourceRef[] = [{ dailyReaderArticleId: 'dr_205' }]
  const dtos = emitSourceRefs(refs)
  assertEqual(dtos[0].reading_record_id, null, 'emit: missing readingRecordId -> null')
  assertEqual(dtos[0].daily_reader_article_id, 'dr_205', 'emit: dailyReaderArticleId preserved')
  assertEqual(dtos[0].source_sentence, null, 'emit: missing sourceSentence -> null')
  pass('emit: null/undefined fields -> null in DTO')
}

console.log(`\nAll ${passCount} SourceRef codec assertions passed.`)