import { View, Text } from '@tarojs/components'
import { memo } from 'react'
import {
  useReadingPreferencesStore,
  FontSize,
  Spacing,
  TranslationDisplay,
  PaperTheme
} from '../../stores/reading-preferences'
import './index.scss'

interface Props {
  visible: boolean
  onClose: () => void
}

const ReadingSettingsSheet = memo(function ReadingSettingsSheet({ visible, onClose }: Props) {
  const { preferences, updatePreferences } = useReadingPreferencesStore()

  if (!visible) return null

  const themes: { value: PaperTheme; label: string; color: string; edge: string }[] = [
    { value: 'paper', label: '纸张', color: '#F9F5EC', edge: '#DED3BF' },
    { value: 'white', label: '纯白', color: '#FFFFFF', edge: '#D8D6D0' },
    { value: 'sage', label: '护眼', color: '#F0F4F0', edge: '#B7C9BE' }
  ]

  const fontSizes: { value: FontSize; aSize: string }[] = [
    { value: 'small', aSize: '26rpx' },
    { value: 'standard', aSize: '31rpx' },
    { value: 'large', aSize: '36rpx' },
    { value: 'xlarge', aSize: '42rpx' }
  ]

  const lineSpacings: { value: Spacing; label: string }[] = [
    { value: 'compact', label: '紧凑' },
    { value: 'standard', label: '标准' },
    { value: 'loose', label: '宽松' }
  ]

  const translations: { value: TranslationDisplay; label: string }[] = [
    { value: 'hidden', label: '隐藏' },
    { value: 'muted', label: '淡显' },
    { value: 'standard', label: '显示' }
  ]

  return (
    <View className='rs-overlay' onClick={onClose}>
      <View className='rs-sheet' onClick={(e) => e.stopPropagation()}>
        <View className='rs-handle' />

        <View className='rs-stack'>
          <View className='rs-paper-strip'>
            {themes.map(t => (
              <View
                key={t.value}
                className={`rs-paper-card ${preferences.paper_theme === t.value ? 'is-active' : ''}`}
                onClick={() => updatePreferences({ paper_theme: t.value })}
              >
                <View className='rs-paper-swatch' style={{ backgroundColor: t.color, borderColor: t.edge }} />
                <Text className='rs-option-label'>{t.label}</Text>
              </View>
            ))}
          </View>

          <View className='rs-panel-card rs-panel-card--size'>
            <View className='rs-size-rail'>
              {fontSizes.map(f => (
                <View
                  key={f.value}
                  className={`rs-size-option ${preferences.font_size === f.value ? 'is-active' : ''}`}
                  onClick={() => updatePreferences({ font_size: f.value })}
                >
                  <Text className='rs-size-a' style={{ fontSize: f.aSize }}>A</Text>
                </View>
              ))}
            </View>
          </View>

          <View className='rs-duo'>
            <View className='rs-mini-card'>
              <View className='rs-card-mark' aria-label='行距'>
                <View className='rs-lines-mark'>
                  <View className='rs-lines-mark-line' />
                  <View className='rs-lines-mark-line' />
                  <View className='rs-lines-mark-line' />
                </View>
              </View>
              <View className='rs-mini-segment'>
                {lineSpacings.map(s => (
                  <View
                    key={s.value}
                    className={`rs-segment-option ${preferences.line_height === s.value ? 'is-active' : ''}`}
                    onClick={() => updatePreferences({ line_height: s.value })}
                  >
                    <Text className='rs-option-label'>{s.label}</Text>
                  </View>
                ))}
              </View>
            </View>

            <View className='rs-mini-card'>
              <View className='rs-card-mark' aria-label='译文'>
                <View className='rs-lang-mark'>
                  <Text className='rs-lang-cn'>文</Text>
                  <Text className='rs-lang-en'>A</Text>
                </View>
              </View>
              <View className='rs-mini-segment'>
                {translations.map(t => (
                  <View
                    key={t.value}
                    className={`rs-segment-option ${preferences.translation_display === t.value ? 'is-active' : ''}`}
                    onClick={() => updatePreferences({ translation_display: t.value })}
                  >
                    <Text className='rs-option-label'>{t.label}</Text>
                  </View>
                ))}
              </View>
            </View>
          </View>
        </View>
      </View>
    </View>
  )
})

export default ReadingSettingsSheet
