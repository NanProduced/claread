import type { SourceRef } from '../../types/view/vocabulary.vm'

export type LookupSaveState =
  | 'not_saved'
  | 'same_lemma_new_context'
  | 'already_saved_here'
  | 'multiple_contexts'
  | 'mastered'

export function getLookupSaveState(
  word: string,
  isCurrentlySaved: boolean,
  currentSentenceId?: string,
  savedSourceRefs?: SourceRef[],
  isMastered?: boolean,
): LookupSaveState {
  if (!isCurrentlySaved) {
    return 'not_saved'
  }

  if (isMastered) return 'mastered'

  if (savedSourceRefs && savedSourceRefs.length > 0) {
    if (savedSourceRefs.length > 1) return 'multiple_contexts'

    if (currentSentenceId && savedSourceRefs[0].sourceSentenceId !== currentSentenceId) {
      return 'same_lemma_new_context'
    }
  }

  return 'already_saved_here'
}

export function getSaveActionCopy(state: LookupSaveState, contextCount?: number, defaultCopy = '记入生词本'): string {
  switch (state) {
    case 'not_saved': return '记入生词本'
    case 'same_lemma_new_context': return '加入当前语境'
    case 'already_saved_here': return '已记入'
    case 'multiple_contexts':
      return contextCount && contextCount > 1 ? `已记入 · ${contextCount}个语境` : '已记入生词本'
    case 'mastered': return '已掌握'
    default: return defaultCopy
  }
}
