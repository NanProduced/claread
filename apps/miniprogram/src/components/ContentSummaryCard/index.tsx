import { useState } from 'react'
import { View, Text } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import type { ContentSummaryModel } from '../../types/view/render-scene.vm'
import './index.scss'

interface ContentSummaryCardProps {
  summary: ContentSummaryModel
}

export default function ContentSummaryCard({ summary }: ContentSummaryCardProps) {
  const [expanded, setExpanded] = useState(false)

  const hasStructuredDetails = !!(summary.researchQuestion || summary.methodology)

  const coreFindings = summary.keyFindings?.slice(0, 3) ?? []
  const hasFindings = coreFindings.length > 0
  const completenessLabel: Record<string, string> = {
    full: '完整导读',
    partial: '部分导读',
    minimal: '简要导读',
  }

  return (
    <View className={`research-brief ${expanded ? 'is-expanded' : 'is-collapsed'}`}>
      <View className='brief-header' onClick={() => setExpanded(!expanded)}>
        <View className='brief-header-left'>
          <Text className='brief-label'>内容导读</Text>
          <Text className='brief-completeness'>{completenessLabel[summary.completeness] || '导读'}</Text>
        </View>
        <View className={`brief-chevron ${expanded ? 'is-open' : ''}`}>
          <LucideIcon name='chevron-right' size={14} color='var(--reader-subtle)' />
        </View>
      </View>

      <View className='brief-overview' onClick={() => setExpanded(!expanded)}>
        <Text className='overview-text' numberOfLines={expanded ? 8 : 2}>{summary.overview}</Text>
      </View>

      <View className={`brief-body ${expanded ? 'show' : 'hide'}`}>
        {hasStructuredDetails && (
          <View className='brief-detail-body'>
            {summary.researchQuestion && (
              <View className='detail-section'>
                <Text className='detail-key'>研究问题</Text>
                <Text className='detail-value'>{summary.researchQuestion}</Text>
              </View>
            )}
            {summary.methodology && (
              <View className='detail-section'>
                <Text className='detail-key'>研究方法</Text>
                <Text className='detail-value'>{summary.methodology}</Text>
              </View>
            )}
          </View>
        )}

        {hasFindings && (
          <View className='brief-findings'>
            <View className='findings-header'>
              <Text className='findings-label'>关键论点</Text>
              <Text className='findings-count'>{coreFindings.length}</Text>
            </View>
            <View className='findings-list'>
              {coreFindings.map((finding, idx) => (
                <View key={idx} className={`finding-item finding-item-${idx + 1}`}>
                  <Text className='finding-num'>{String(idx + 1).padStart(2, '0')}</Text>
                  <Text className='finding-text'>{finding}</Text>
                </View>
              ))}
            </View>
          </View>
        )}

        {summary.limitations && summary.limitations.length > 0 && (
          <View className='brief-detail-body limitations'>
            <View className='detail-section'>
              <Text className='detail-key'>研究局限</Text>
              <View className='detail-list'>
                {summary.limitations.map((item, idx) => (
                  <Text key={idx} className='detail-list-item'>· {item}</Text>
                ))}
              </View>
            </View>
          </View>
        )}
      </View>

      {(hasStructuredDetails || hasFindings || (summary.limitations && summary.limitations.length > 0)) && (
        <View className='brief-detail-toggle' onClick={() => setExpanded(!expanded)}>
          <Text className='toggle-text'>
            {expanded ? '收起导读' : '查看研究问题、论点与局限'}
          </Text>
          <View className={`toggle-right ${expanded ? 'is-open' : ''}`}>
            <LucideIcon name='chevron-down' size={13} color='var(--reader-subtle)' />
          </View>
        </View>
      )}
    </View>
  )
}
