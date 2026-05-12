import type { DailyReaderHighlight } from '../../../types/view/daily-reader.vm'
import type { InlineMarkModel, TextAnchor, InlineGlossary } from '../../../types/view/render-scene.vm'

const TONE_MAP: Record<string, InlineMarkModel['visualTone']> = {
  vocab_highlight: 'vocab',
  phrase_gloss: 'phrase',
  context_gloss: 'context',
}

const LOOKUP_KIND_MAP: Record<string, InlineMarkModel['lookupKind']> = {
  vocab_highlight: 'word',
  phrase_gloss: 'phrase',
  context_gloss: 'word',
}

export function highlightToInlineMark(hl: DailyReaderHighlight): InlineMarkModel {
  const anchor: TextAnchor = {
    kind: 'text',
    sentenceId: hl.paragraphId,
    anchorText: hl.text,
  }

  const glossary: InlineGlossary = {
    zh: hl.gloss,
    gloss: hl.gloss,
    reason: hl.detail?.contextExplanation,
    phraseType: hl.type === 'phrase_gloss' ? 'collocation' : undefined,
  }

  return {
    id: hl.id,
    annotationType: 'vocab_highlight',
    anchor,
    renderType: 'background',
    visualTone: TONE_MAP[hl.type] || 'vocab',
    clickable: true,
    lookupText: hl.text,
    lookupKind: LOOKUP_KIND_MAP[hl.type],
    glossary,
  }
}
