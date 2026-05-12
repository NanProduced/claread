import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { memo, useCallback, useState } from 'react'
import type { DailyReaderFooterAnalysis as FooterAnalysisType } from '../../types/view/daily-reader.vm'
import { getDailyReaderSourceDisplay } from '../../utils/daily-reader-source'
import LucideIcon from '../LucideIcon'
import './index.scss'

interface Props {
  footerAnalysis: FooterAnalysisType
  sourceUrl: string
  source: string
}

const EMPTY_FOOTER: FooterAnalysisType = {
  summary: '',
  thesisAndIntent: { thesis: '', authorIntent: '' },
  structure: [],
  keyExpressions: [],
  misreadingPoints: [],
  fullArticleAnalysis: '',
  discussionQuestions: [],
}

function safeFooter(raw: FooterAnalysisType | null | undefined): FooterAnalysisType {
  if (!raw || typeof raw !== 'object') return EMPTY_FOOTER
  return {
    summary: raw.summary ?? '',
    thesisAndIntent: raw.thesisAndIntent
      ? { thesis: raw.thesisAndIntent.thesis ?? '', authorIntent: raw.thesisAndIntent.authorIntent ?? '' }
      : { thesis: '', authorIntent: '' },
    structure: Array.isArray(raw.structure) ? raw.structure : [],
    keyExpressions: Array.isArray(raw.keyExpressions) ? raw.keyExpressions : [],
    misreadingPoints: Array.isArray(raw.misreadingPoints) ? raw.misreadingPoints : [],
    fullArticleAnalysis: raw.fullArticleAnalysis ?? '',
    discussionQuestions: Array.isArray(raw.discussionQuestions) ? raw.discussionQuestions : [],
    articleTakeaway: raw.articleTakeaway,
    sentenceNotes: raw.sentenceNotes,
    writingMoves: raw.writingMoves,
  }
}

const DailyReaderFooterAnalysis = memo(function DailyReaderFooterAnalysis({
  footerAnalysis,
  sourceUrl,
  source,
}: Props) {
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({})
  const fa = safeFooter(footerAnalysis)
  const sourceDisplay = getDailyReaderSourceDisplay(source)

  const takeaway = fa.articleTakeaway || fa.summary
  const keyExpressions = fa.keyExpressions
  const sentenceNotes = fa.sentenceNotes ?? []
  const writingMoves = fa.writingMoves ?? []
  const questions = fa.discussionQuestions

  const hasContent = !!takeaway || keyExpressions.length > 0 || sentenceNotes.length > 0 || writingMoves.length > 0 || questions.length > 0
  const toggleSection = useCallback((key: string) => {
    setExpandedSections(prev => ({ ...prev, [key]: !prev[key] }))
  }, [])

  const renderSectionHeader = (
    key: string,
    title: string,
    icon: string,
    count?: number,
    collapsible = false,
  ) => {
    const expanded = !!expandedSections[key]
    return (
      <View
        className={`daily-footer__section-header ${collapsible ? 'daily-footer__section-header--action' : ''}`}
        onClick={collapsible ? () => toggleSection(key) : undefined}
      >
        <View className='daily-footer__section-kicker'>
          <LucideIcon name={icon} size={17} color='var(--dr-text-heading)' />
          <Text className='daily-footer__section-title'>{title}</Text>
        </View>
        {typeof count === 'number' && count > 0 && (
          <Text className='daily-footer__section-count'>{count}</Text>
        )}
        {collapsible && (
          <View className={`daily-footer__section-chevron ${expanded ? 'daily-footer__section-chevron--expanded' : ''}`}>
            <LucideIcon name='ChevronDown' size={16} color='var(--dr-text-muted)' />
          </View>
        )}
      </View>
    )
  }

  return (
    <View className='daily-footer'>
      <View className='daily-footer__divider'>
        <View className='daily-footer__divider-ornament' />
        <Text className='daily-footer__divider-label'>精读摘要</Text>
        <View className='daily-footer__divider-ornament' />
      </View>

      {!hasContent && (
        <View className='daily-footer__empty'>
          <Text className='daily-footer__empty-text'>精读摘要正在生成中，稍后回来阅读吧。</Text>
        </View>
      )}

      {hasContent && (
        <View className='daily-footer__content'>
          {takeaway && (
            <View className='daily-footer__section daily-footer__section--lead'>
              {renderSectionHeader('takeaway', '内容提要', 'Star')}
              <Text className='daily-footer__takeaway'>{takeaway}</Text>
            </View>
          )}

          {keyExpressions.length > 0 && (
            <View className='daily-footer__section daily-footer__section--expressions'>
              {renderSectionHeader('expressions', '表达学习', 'Bookmark', keyExpressions.length)}
              <View className='daily-footer__expressions'>
                {keyExpressions.map((expr, idx) => (
                  <View key={`${expr.expression}-${idx}`} className='daily-footer__expr-item'>
                    <View className='daily-footer__expr-head'>
                      <Text className='daily-footer__expr-en'>{expr.expression}</Text>
                      <Text className='daily-footer__expr-zh'>{expr.gloss}</Text>
                    </View>
                    <Text className='daily-footer__expr-context'>{expr.contextSentence}</Text>
                    {expr.usageNote && (
                      <Text className='daily-footer__expr-usage'>{expr.usageNote}</Text>
                    )}
                  </View>
                ))}
              </View>
            </View>
          )}

          {sentenceNotes.length > 0 && (
            <View className='daily-footer__section daily-footer__section--folded'>
              {renderSectionHeader('sentences', '句法拆解', 'Eye', sentenceNotes.length, true)}
              {expandedSections.sentences && (
                <View className='daily-footer__sentences'>
                  {sentenceNotes.map((sn, idx) => (
                    <View key={idx} className='daily-footer__sentence-item'>
                      <Text className='daily-footer__sn-en'>{sn.sentence}</Text>
                      <Text className='daily-footer__sn-zh'>{sn.translation}</Text>
                      {sn.breakdown && (
                        <View className='daily-footer__sn-note'>
                          <Text className='daily-footer__sn-label'>拆解</Text>
                          <Text className='daily-footer__sn-text'>{sn.breakdown}</Text>
                        </View>
                      )}
                      {sn.takeaway && (
                        <View className='daily-footer__sn-note'>
                          <Text className='daily-footer__sn-label'>可学点</Text>
                          <Text className='daily-footer__sn-text'>{sn.takeaway}</Text>
                        </View>
                      )}
                    </View>
                  ))}
                </View>
              )}
            </View>
          )}

          {writingMoves.length > 0 && (
            <View className='daily-footer__section daily-footer__section--folded'>
              {renderSectionHeader('moves', '写作观察', 'PenTool', writingMoves.length, true)}
              {expandedSections.moves && (
                <View className='daily-footer__moves'>
                  {writingMoves.map((move, idx) => (
                    <View key={idx} className='daily-footer__move-item'>
                      <Text className='daily-footer__move-anchor'>{move.anchor}</Text>
                      <Text className='daily-footer__move-desc'>{move.explanation}</Text>
                      {move.reusablePattern && (
                        <Text className='daily-footer__move-pattern'>{move.reusablePattern}</Text>
                      )}
                    </View>
                  ))}
                </View>
              )}
            </View>
          )}

          {questions.length > 0 && (
            <View className='daily-footer__section daily-footer__section--last daily-footer__section--folded'>
              {renderSectionHeader('questions', '延伸思考', 'MessageCircle', questions.length, true)}
              {expandedSections.questions && (
                <View className='daily-footer__questions'>
                  {questions.map((q, idx) => (
                    <View key={idx} className='daily-footer__question'>
                      <Text className='daily-footer__question-bullet'>{idx + 1}</Text>
                      <Text className='daily-footer__question-text'>{q}</Text>
                    </View>
                  ))}
                </View>
              )}
            </View>
          )}
        </View>
      )}

      <View className='daily-footer__source' onClick={() => {
        if (sourceUrl) {
          Taro.setClipboardData({ data: sourceUrl })
          Taro.showToast({ title: '链接已复制', icon: 'none', duration: 1500 })
        }
      }}>
        <Text className='daily-footer__source-label'>原文刊载于</Text>
        <Text className='daily-footer__source-name'>
          {sourceDisplay.localized ? `${sourceDisplay.primary}（${sourceDisplay.localized}）` : sourceDisplay.primary}
        </Text>
        {sourceUrl && <Text className='daily-footer__source-link'>阅读原文</Text>}
      </View>
    </View>
  )
})

export default DailyReaderFooterAnalysis
