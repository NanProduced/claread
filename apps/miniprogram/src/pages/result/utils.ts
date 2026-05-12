export function getSimpleLemmaCandidates(word: string): string[] {
  const candidates: string[] = []

  if (word.endsWith('ying')) {
    candidates.push(word.slice(0, -4) + 'ie')
  }
  if (word.endsWith('ing')) {
    candidates.push(word.slice(0, -3))
    if (word.length > 5 && word[word.length - 4] === word[word.length - 5]) {
      candidates.push(word.slice(0, -4))
    }
    candidates.push(word.slice(0, -3) + 'e')
  }

  if (word.endsWith('ied')) {
    candidates.push(word.slice(0, -3) + 'y')
  }
  if (word.endsWith('ed')) {
    candidates.push(word.slice(0, -2))
    candidates.push(word.slice(0, -1))
    if (word.length > 4 && word[word.length - 3] === word[word.length - 4]) {
      candidates.push(word.slice(0, -3))
    }
  }

  if (word.endsWith('ies')) {
    candidates.push(word.slice(0, -3) + 'y')
  }
  if (word.endsWith('ves') && word.length > 4) {
    candidates.push(word.slice(0, -3) + 'f')
    candidates.push(word.slice(0, -3) + 'fe')
  }
  if (word.endsWith('es')) {
    candidates.push(word.slice(0, -2))
    candidates.push(word.slice(0, -1))
  }
  if (word.endsWith('s') && !word.endsWith('ss') && !word.endsWith('us')) {
    candidates.push(word.slice(0, -1))
  }

  if (word.endsWith('er')) {
    candidates.push(word.slice(0, -2))
    candidates.push(word.slice(0, -1))
    if (word.length > 4 && word[word.length - 3] === word[word.length - 4]) {
      candidates.push(word.slice(0, -3))
    }
    candidates.push(word.slice(0, -2) + 'e')
  }

  if (word.endsWith('est')) {
    candidates.push(word.slice(0, -3))
    candidates.push(word.slice(0, -2))
    if (word.length > 5 && word[word.length - 4] === word[word.length - 5]) {
      candidates.push(word.slice(0, -4))
    }
  }

  if (word.endsWith('ly') && word.length > 4) {
    candidates.push(word.slice(0, -2))
    if (word.endsWith('ily') && word.length > 5) {
      candidates.push(word.slice(0, -3) + 'y')
    }
    if (word.endsWith('ally') && word.length > 6) {
      candidates.push(word.slice(0, -4) + 'al')
    }
  }

  return [...new Set(candidates)].filter(c => c.length >= 2)
}

export const PAGE_MODE_OPTIONS = [
  { value: 'immersive', label: '原文' },
  { value: 'intensive', label: '精读' },
] as const

export const PAGE_STATE_MESSAGES: Record<import('../../types/view/render-scene.vm').ResultPageState, { title: string; subtitle: string } | null> = {
  loading: null,
  normal: null,
  degraded_light: null,
  degraded_heavy: null,
  empty: {
    title: '未能解析出有效内容',
    subtitle: '请输入至少一段完整的英文句子（建议 3 句以上），支持常见文章格式。',
  },
  failed: {
    title: '分析失败',
    subtitle: '请稍后重试',
  },
  timeout: {
    title: '分析超时',
    subtitle: '内容较长时需要更多处理时间，请稍后重试。',
  },
  network_fail: {
    title: '网络不给力',
    subtitle: '请检查网络后重新尝试。',
  },
}

export function hasRenderableScene(scene: import('../../types/view/render-scene.vm').AnyRenderSceneVm | null): boolean {
  if (!scene) return false
  if (scene.article?.paragraphs?.length) return true
  return (scene.article?.sentences ?? []).some((sentence) => !!sentence.text?.trim())
}

export function splitSourceParagraphs(text: string): string[] {
  return text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
}
