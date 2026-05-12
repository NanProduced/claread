export interface DailyReaderSourceDisplay {
  primary: string
  localized?: string
  shortName: string
  metaLabel: string
  sourceKey: string
}

const SOURCE_DISPLAY: Record<string, Omit<DailyReaderSourceDisplay, 'sourceKey' | 'metaLabel'>> = {
  guardian: {
    primary: 'The Guardian',
    localized: '卫报',
    shortName: 'Guardian',
  },
  bbc: {
    primary: 'BBC News',
    localized: '英国广播公司',
    shortName: 'BBC',
  },
  npr: {
    primary: 'NPR',
    localized: '美国公共广播电台',
    shortName: 'NPR',
  },
}

function normalizeSourceKey(source: string) {
  const value = source.trim().toLowerCase()
  if (value.includes('guardian')) return 'guardian'
  if (value.includes('bbc')) return 'bbc'
  if (value.includes('npr')) return 'npr'
  return value
}

export function getDailyReaderSourceDisplay(source: string | null | undefined): DailyReaderSourceDisplay {
  const raw = source?.trim() || 'Claread'
  const sourceKey = normalizeSourceKey(raw)
  const mapped = SOURCE_DISPLAY[sourceKey]
  const primary = mapped?.primary || raw
  const localized = mapped?.localized

  return {
    primary,
    localized,
    shortName: mapped?.shortName || primary,
    sourceKey,
    metaLabel: localized ? `${primary} · ${localized}` : primary,
  }
}
