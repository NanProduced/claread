const NAMED_ENTITIES: Record<string, string> = {
  amp: '&',
  apos: "'",
  gt: '>',
  lt: '<',
  nbsp: ' ',
  ndash: '–',
  mdash: '—',
  quot: '"',
  lsquo: '‘',
  rsquo: '’',
  ldquo: '“',
  rdquo: '”',
  hellip: '…',
}

function decodeEntity(entity: string): string {
  if (entity.startsWith('#x') || entity.startsWith('#X')) {
    const codePoint = Number.parseInt(entity.slice(2), 16)
    return isValidCodePoint(codePoint) ? String.fromCodePoint(codePoint) : `&${entity};`
  }
  if (entity.startsWith('#')) {
    const codePoint = Number.parseInt(entity.slice(1), 10)
    return isValidCodePoint(codePoint) ? String.fromCodePoint(codePoint) : `&${entity};`
  }
  return NAMED_ENTITIES[entity.toLowerCase()] ?? `&${entity};`
}

function isValidCodePoint(value: number): boolean {
  return Number.isFinite(value) && value >= 0 && value <= 0x10ffff
}

export function decodeHtmlEntities(value: string | null | undefined): string {
  if (!value) return ''

  let decoded = value.replace(/\u00A0/g, ' ')
  for (let i = 0; i < 2; i += 1) {
    const next = decoded.replace(/&(#x[0-9a-f]+|#\d+|[a-z][a-z0-9]+);/gi, (_, entity: string) => decodeEntity(entity))
    if (next === decoded) break
    decoded = next
  }
  return decoded.replace(/\u00A0/g, ' ')
}
