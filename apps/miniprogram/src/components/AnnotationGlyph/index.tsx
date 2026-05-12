import { View } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import './index.scss'

export type AnnotationGlyphType =
  | 'vocab'
  | 'phrase'
  | 'context'
  | 'grammar_note'
  | 'sentence_analysis'
  | 'saved_vocab'
  | 'merged_note'
  | 'feedback'
  | 'term'
  | 'logic'
  | 'interpretation'

interface AnnotationGlyphProps {
  /**
   * Icon variant type. Supporting both 'type' and 'variant' for backward compatibility.
   */
  type?: AnnotationGlyphType
  variant?: AnnotationGlyphType
  /**
   * Size in rpx or preset. Default is 36.
   */
  size?: number | 'sm' | 'md' | 'lg'
  state?: 'default' | 'active' | 'disabled'
  className?: string
}

/**
 * Mapping from semantic annotation types to stable Lucide icons.
 * Ref: client/README.md and icon-spec.md
 */
const GLYPH_MAP: Record<AnnotationGlyphType, { name: string; color: string }> = {
  vocab: { name: 'bookmark', color: 'var(--annotation-vocab)' },
  phrase: { name: 'languages', color: 'var(--annotation-phrase)' },
  context: { name: 'message-square-text', color: 'var(--annotation-context)' },
  grammar_note: { name: 'network', color: 'var(--annotation-grammar)' },
  sentence_analysis: { name: 'sliders-horizontal', color: 'var(--annotation-analysis)' },
  feedback: { name: 'messageSquare', color: 'var(--reader-muted)' },
  saved_vocab: { name: 'bookmark', color: 'var(--reader-ink)' },
  merged_note: { name: 'clipboard', color: 'var(--annotation-merged)' },
  term: { name: 'flaskConical', color: 'var(--term-accent)' },
  logic: { name: 'gitBranch', color: 'var(--logic-accent)' },
  interpretation: { name: 'messageSquareText', color: 'var(--interpretation-accent)' },
}

const SIZE_PRESETS: Record<string, number> = {
  sm: 28,
  md: 32,
  lg: 36,
}

export default function AnnotationGlyph({
  type,
  variant,
  size = 36,
  state = 'default',
  className = ''
}: AnnotationGlyphProps) {
  const glyphType = variant || type || 'vocab'
  const config = GLYPH_MAP[glyphType]

  const pixelSize = typeof size === 'number' ? size : (SIZE_PRESETS[size] || 36)
  const stateClass = state !== 'default' ? `glyph-state-${state}` : ''

  return (
    <View className={`annotation-glyph glyph-${glyphType} ${stateClass} ${className}`}>
      <LucideIcon
        name={config.name}
        size={pixelSize}
        color={config.color}
        strokeWidth={1.8}
      />
    </View>
  )
}
