import { View, Text, ScrollView } from '@tarojs/components'
import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchDict, fetchDictEntry } from '../../services/api/client'
import { dictResponseDtoToVm } from '../../services/api/adapters/dict.adapter'
import type { VocabEntry, SourceRef } from '../../types/view/vocabulary.vm'
import type { DictionaryDisambiguationResult, DictionaryEntryPayload, DictionaryMeaning, DictionaryResult } from '../../types/view/render-scene.vm'
import LucideIcon from '../LucideIcon'
import './index.scss'

type StudyTab = 'context' | 'dictionary'

interface VocabStudyCardProps {
  entry: VocabEntry
  mode?: 'inspect' | 'review'
  onGoToOriginal?: (ref: SourceRef) => void
}

function pickCandidateEntryId(result: DictionaryDisambiguationResult, entry: VocabEntry): number | null {
  const targetWords = new Set([entry.lemma, entry.word, ...(entry.collectedForms || [])].map(item => item.toLowerCase()))
  const exact = result.candidates.find(candidate => targetWords.has(candidate.label.toLowerCase()))
  return exact?.entryId || result.candidates[0]?.entryId || null
}

async function resolveDictionaryEntry(entry: VocabEntry): Promise<DictionaryEntryPayload | null> {
  if (entry.dictEntryId) {
    const dto = await fetchDictEntry(entry.dictEntryId)
    const vm = dictResponseDtoToVm(dto)
    return vm.resultType === 'entry' ? vm.entry : null
  }

  const query = entry.lemma || entry.word
  const type = query.trim().includes(' ') ? 'phrase' : 'word'
  const contextSentence = entry.sourceRefs?.[0]?.sourceSentence || entry.sentence
  const occurrence = entry.sourceRefs?.[0]?.sourceOccurrence
  const lookupDto = await fetchDict(query, type, contextSentence, occurrence)
  const lookupVm: DictionaryResult = dictResponseDtoToVm(lookupDto)

  if (lookupVm.resultType === 'entry') return lookupVm.entry
  if (lookupVm.resultType !== 'disambiguation') return null

  const entryId = pickCandidateEntryId(lookupVm, entry)
  if (!entryId) return null
  const detailDto = await fetchDictEntry(entryId)
  const detailVm = dictResponseDtoToVm(detailDto)
  return detailVm.resultType === 'entry' ? detailVm.entry : null
}

export default function VocabStudyCard({
  entry,
  mode = 'review',
  onGoToOriginal,
}: VocabStudyCardProps) {
  const [currentTab, setCurrentTab] = useState<StudyTab>('context')
  const [dictEntry, setDictEntry] = useState<DictionaryEntryPayload | null>(null)
  const [dictLoading, setDictLoading] = useState(false)
  const touchStartXRef = useRef<number | null>(null)

  const loadDict = useCallback(async () => {
    setDictLoading(true)
    try {
      setDictEntry(await resolveDictionaryEntry(entry))
    } catch {
      setDictEntry(null)
    } finally {
      setDictLoading(false)
    }
  }, [entry])

  useEffect(() => {
    setCurrentTab('context')
    loadDict()
  }, [entry.id, loadDict])

  const handleTouchStart = (e: any) => {
    touchStartXRef.current = e.touches?.[0]?.clientX ?? null
  }

  const handleTouchEnd = (e: any) => {
    const startX = touchStartXRef.current
    touchStartXRef.current = null
    if (startX == null) return
    const endX = e.changedTouches?.[0]?.clientX
    if (typeof endX !== 'number') return
    const deltaX = endX - startX
    if (Math.abs(deltaX) < 48) return
    setCurrentTab(deltaX < 0 ? 'dictionary' : 'context')
  }

  const displayMeanings: DictionaryMeaning[] = dictEntry?.meanings || entry.detailMeanings || []
  const displayPhrases = dictEntry?.phrases || entry.detailPhrases || []
  const displayExamples = dictEntry?.examples || entry.detailExamples || []
  const sourceRefs = entry.sourceRefs || []
  const primaryRef = sourceRefs[0]
  const reviewCount = entry.reviewCount ? entry.reviewCount + 1 : 1

  return (
    <View className={`vocab-study-card ${mode}`} onTouchStart={handleTouchStart} onTouchEnd={handleTouchEnd}>
      <View className='study-tabs'>
        <View className={`study-tab ${currentTab === 'context' ? 'active' : ''}`} onClick={() => setCurrentTab('context')}>
          <Text>语境</Text>
        </View>
        <View className={`study-tab ${currentTab === 'dictionary' ? 'active' : ''}`} onClick={() => setCurrentTab('dictionary')}>
          <Text>词典</Text>
        </View>
      </View>

      <View className='word-header'>
        <Text className='word-text'>{entry.word}</Text>
        {entry.phonetic && <Text className='phonetic-text'>/{entry.phonetic}/</Text>}
      </View>

      <View className='state-tag'>
        <LucideIcon name='bookOpen' size={14} color='var(--text-sub)' />
        <Text>{entry.mastered ? '已掌握' : mode === 'review' ? `学习中 · 第 ${reviewCount} 次复习` : '生词笔记'}</Text>
      </View>

      <View className='divider' />

      <View className='study-content-shell'>
        <ScrollView className='study-content-scroll' scrollY showScrollbar={false}>
          {currentTab === 'context' ? (
            <View className='context-pane'>
              <View className='meaning-preview'>
                {entry.partOfSpeech && <Text className='pos-text'>{entry.partOfSpeech}</Text>}
                <Text className='meaning-text'>{entry.meaning}</Text>
              </View>

              <View className='section-title'>
                <LucideIcon name='fileText' size={16} color='var(--text-sub)' />
                <Text>收藏语境</Text>
              </View>

              {primaryRef?.sourceSentence || entry.sentence ? (
                <View className='context-note'>
                  <Text className='context-sentence'>"{primaryRef?.sourceSentence || entry.sentence}"</Text>
                  <View className='context-meta'>
                    <View>
                      <Text className='context-source'>《阅读文章》</Text>
                      {primaryRef?.collectedAt && (
                        <Text className='context-date'>{new Date(primaryRef.collectedAt).toLocaleDateString('zh-CN')}</Text>
                      )}
                    </View>
                    {primaryRef?.clientRecordId && onGoToOriginal && (
                      <View className='inline-source-link' onClick={() => onGoToOriginal(primaryRef)}>
                        <LucideIcon name='externalLink' size={14} color='var(--text-sub)' />
                        <Text>原文</Text>
                      </View>
                    )}
                  </View>
                </View>
              ) : (
                <Text className='empty-text'>暂无语境信息</Text>
              )}

              <View className='swipe-hint'>
                <Text>左滑查看词典</Text>
              </View>
            </View>
          ) : (
            <View className='dictionary-pane'>
              <View className='section-title'>
                <LucideIcon name='book' size={16} color='var(--text-sub)' />
                <Text>释义</Text>
              </View>

              {dictLoading ? (
                <Text className='loading-text'>加载词典中...</Text>
              ) : displayMeanings.length > 0 ? (
                <View className='meanings-list'>
                  {displayMeanings.map((m, idx) => (
                    <View key={idx} className='meaning-group'>
                      <Text className='pos-text'>{m.partOfSpeech}</Text>
                      <View className='definitions'>
                        {m.definitions.map((def, dIdx) => (
                          <View key={dIdx} className='def-item'>
                            <Text className='def-text'>
                              {m.definitions.length > 1 ? `${dIdx + 1}. ` : ''}{def.meaning}
                            </Text>
                          </View>
                        ))}
                      </View>
                    </View>
                  ))}
                </View>
              ) : (
                <View className='meaning-group'>
                  {entry.partOfSpeech && <Text className='pos-text'>{entry.partOfSpeech}</Text>}
                  <View className='definitions'>
                    <Text className='def-text'>{entry.meaning}</Text>
                  </View>
                </View>
              )}

              {displayExamples.length > 0 && (
                <View className='examples-list'>
                  <View className='section-title sub'>
                    <Text>例句</Text>
                  </View>
                  {displayExamples.slice(0, 2).map((ex, idx) => (
                    <View key={idx} className='example-item'>
                      <Text className='ex-en'>{ex.example}</Text>
                      {ex.exampleTranslation && <Text className='ex-zh'>{ex.exampleTranslation}</Text>}
                    </View>
                  ))}
                </View>
              )}

              {displayPhrases.length > 0 && (
                <View className='phrases-list'>
                  <View className='section-title sub'>
                    <Text>短语</Text>
                  </View>
                  {displayPhrases.slice(0, 3).map((p, idx) => (
                    <View key={idx} className='phrase-item'>
                      <Text className='phrase-en'>{p.phrase}</Text>
                      <Text className='phrase-zh'>{p.meaning}</Text>
                    </View>
                  ))}
                </View>
              )}

              <View className='swipe-hint'>
                <Text>右滑返回语境</Text>
              </View>
            </View>
          )}
        </ScrollView>
      </View>
    </View>
  )
}
