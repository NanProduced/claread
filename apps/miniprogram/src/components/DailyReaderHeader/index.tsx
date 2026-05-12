import { View, Text, Image } from '@tarojs/components'
import { memo } from 'react'
import type { DailyReaderArticle, DailyReaderPreReadingGuide } from '../../types/view/daily-reader.vm'
import { getDailyReaderSourceDisplay } from '../../utils/daily-reader-source'
import defaultCover from '../../assets/covers/daily-reader-default.jpg'
import LucideIcon from '../LucideIcon'
import './index.scss'

interface Props {
  article: DailyReaderArticle
}

const DIFFICULTY_LABELS: Record<string, string> = {
  A2: 'A2 入门',
  B1: 'B1 中级',
  B2: 'B2 中高',
  C1: 'C1 高级',
}

const DailyReaderHeader = memo(function DailyReaderHeader({ article }: Props) {
  const hasCover = !!article.coverImageUrl
  const coverSrc = article.coverImageUrl || defaultCover
  const sourceDisplay = getDailyReaderSourceDisplay(article.source)

  const guide: DailyReaderPreReadingGuide | undefined = article.preReadingGuide

  return (
    <View className='daily-header'>
      <View className='daily-header__cover'>
        <Image
          className='daily-header__cover-img'
          src={coverSrc}
          mode='aspectFill'
          lazyLoad
        />
        <View className='daily-header__cover-overlay' />
        <View className='daily-header__cover-tag'>
          <LucideIcon name='Calendar' size={14} color='#FFFFFF' />
          <Text className='daily-header__cover-tag-text'>每日精读 · {article.publishDate}</Text>
        </View>
      </View>
      <View className='daily-header__content'>
        <View className='daily-header__meta'>
          <Text className='daily-header__source'>{sourceDisplay.primary}</Text>
          {sourceDisplay.localized && (
            <>
              <Text className='daily-header__dot'>·</Text>
              <Text className='daily-header__source-local'>{sourceDisplay.localized}</Text>
            </>
          )}
          <Text className='daily-header__dot'>·</Text>
          <Text className='daily-header__date'>{article.publishDate}</Text>
        </View>
        <Text className='daily-header__title'>{article.title}</Text>
        {article.subtitle && (
          <Text className='daily-header__subtitle'>{article.subtitle}</Text>
        )}
        <View className='daily-header__badges'>
          <View className='daily-header__badge daily-header__badge--difficulty'>
            <Text className='daily-header__badge-text'>
              {DIFFICULTY_LABELS[article.difficulty] || article.difficulty}
            </Text>
          </View>
          <View className='daily-header__badge daily-header__badge--time'>
            <Text className='daily-header__badge-text'>{article.readTimeMinutes} min</Text>
          </View>
          {article.tags.slice(0, 3).map((tag) => (
            <View key={tag} className='daily-header__badge daily-header__badge--tag'>
              <Text className='daily-header__badge-text'>{tag}</Text>
            </View>
          ))}
        </View>
        {guide && (
          <View className='daily-header__guide'>
            <View className='daily-header__guide-title-wrap'>
              <LucideIcon name='BookOpen' size={16} color='var(--dr-text-sub)' />
              <Text className='daily-header__guide-label'>读前提示</Text>
            </View>
            {guide.overview && (
              <Text className='daily-header__guide-overview'>{guide.overview}</Text>
            )}
            {guide.questions.length > 0 && (
              <View className='daily-header__guide-questions'>
                {guide.questions.map((q, idx) => (
                  <View key={idx} className='daily-header__guide-qitem'>
                    <Text className='daily-header__guide-qnum'>{idx + 1}</Text>
                    <Text className='daily-header__guide-qtext'>{q}</Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}
      </View>
    </View>
  )
})

export default DailyReaderHeader
