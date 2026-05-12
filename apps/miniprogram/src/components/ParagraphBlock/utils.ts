/**
 * 英文阅读 UI 的轻量 lexer。
 *
 * 目标不是做通用 NLP tokenization，而是稳定切出“可点英文词”：
 * - 普通单词：word
 * - 带撇号 / 连字符：don't / world's / state-owned
 * - 缩写：U.S. / U.K. / Ph.D. / e.g.
 * - 其他（空格、中文、数字、标点等）：plain
 *
 * 这样可以保证结果页的点击热区稳定，不依赖复杂 tokenizer 框架。
 */

export interface TextToken {
  type: 'word' | 'plain'
  text: string
  start: number
  end: number
}

type ScanResult = {
  end: number
  text: string
}

function isAsciiLetter(ch: string | undefined): boolean {
  if (!ch) return false
  const code = ch.charCodeAt(0)
  return (code >= 65 && code <= 90) || (code >= 97 && code <= 122)
}

function isWordConnector(ch: string | undefined): boolean {
  return ch === '\'' || ch === '’' || ch === '-'
}

function scanLetters(text: string, start: number): number {
  let i = start
  while (i < text.length && isAsciiLetter(text[i])) i++
  return i
}

/**
 * 匹配类似 U.S. / U.K. / Ph.D. / e.g. 的缩写。
 * 规则：至少两个「letters + dot」片段才视为缩写。
 */
function scanAbbreviation(text: string, start: number): ScanResult | null {
  let i = start
  let dottedSegments = 0

  while (i < text.length) {
    const lettersEnd = scanLetters(text, i)
    if (lettersEnd === i || text[lettersEnd] !== '.') break

    dottedSegments += 1
    i = lettersEnd + 1

    if (!isAsciiLetter(text[i])) break
  }

  if (dottedSegments < 2) return null
  return { end: i, text: text.slice(start, i) }
}

function scanPlainWord(text: string, start: number): ScanResult | null {
  let i = scanLetters(text, start)
  if (i === start) return null

  while (i < text.length) {
    const connector = text[i]
    if (!isWordConnector(connector)) break

    const next = text[i + 1]
    if (connector === '-') {
      if (!isAsciiLetter(next)) break
      i = scanLetters(text, i + 1)
      continue
    }

    if (connector === '\'' || connector === '’') {
      if (!isAsciiLetter(next)) {
        // students' 这种尾部所有格
        i += 1
        break
      }
      i = scanLetters(text, i + 1)
      continue
    }
  }

  return { end: i, text: text.slice(start, i) }
}

function scanWordLike(text: string, start: number): ScanResult | null {
  const abbreviation = scanAbbreviation(text, start)
  if (abbreviation) {
    let i = abbreviation.end

    while (text[i] === '-') {
      const nextStart = i + 1
      const nextToken = scanAbbreviation(text, nextStart) ?? scanPlainWord(text, nextStart)
      if (!nextToken) break
      i = nextToken.end
    }

    return { end: i, text: text.slice(start, i) }
  }

  return scanPlainWord(text, start)
}

/**
 * 将文本拆分为交替的 token 序列，并合并连续 plain token。
 */
export function tokenizeText(text: string): TextToken[] {
  const rawTokens: TextToken[] = []
  let i = 0

  while (i < text.length) {
    const wordLike = scanWordLike(text, i)
    if (wordLike) {
      rawTokens.push({ type: 'word', text: wordLike.text, start: i, end: wordLike.end })
      i = wordLike.end
      continue
    }

    let plainEnd = i + 1
    while (plainEnd < text.length && !scanWordLike(text, plainEnd)) {
      plainEnd += 1
    }

    rawTokens.push({ type: 'plain', text: text.slice(i, plainEnd), start: i, end: plainEnd })
    i = plainEnd
  }

  const merged: TextToken[] = []
  for (const token of rawTokens) {
    const last = merged[merged.length - 1]
    if (last && last.type === token.type) {
      last.text += token.text
      last.end = token.end
    } else {
      merged.push({ ...token })
    }
  }

  return merged
}

export interface AnalysisChunk {
  order: string
  label: string
  text: string
}

/**
 * 解析 sentence_analysis 的 content
 * 格式：
 * 前半段为整句说明
 * 后半段为：- **1. 主语**：`The article`
 */
export function parseSentenceAnalysis(content: string): { summary: string; chunks: AnalysisChunk[] } {
  const lines = content.split('\n')
  const summaryLines: string[] = []
  const chunks: AnalysisChunk[] = []

  // 匹配正则：- **1. 主语**：`...` 或 - **主语**：`...`
  // 支持有数字和没数字的情况
  const chunkRegex = /^-\s*\*\*(?:(\d+)\.\s*)?([^*]+)\*\*[：:]\s*[`'"](.+)[`'"]$/

  lines.forEach(line => {
    const trimmed = line.trim()
    if (!trimmed) return

    const match = trimmed.match(chunkRegex)
    if (match) {
      chunks.push({
        order: match[1] || '',
        label: match[2].trim(),
        text: match[3].trim(),
      })
    } else {
      // 只有在还没开始匹配到 chunks 时，才把行加入 summary
      if (chunks.length === 0) {
        summaryLines.push(trimmed)
      }
    }
  })

  return {
    summary: summaryLines.join('\n'),
    chunks: chunks.sort((a, b) => {
      if (!a.order || !b.order || a.order === b.order) return 0
      return parseInt(a.order) - parseInt(b.order)
    }),
  }
}

/**
 * 鲁棒的模糊匹配逻辑
 * 在 fullText 中寻找 subText 的物理起止坐标，忽略标点、大小写及空白差异。
 */
export function findFuzzyMatch(fullText: string, subText: string, fromIndex: number = 0): { start: number; length: number } | null {
  // 标准化词序列：只保留英文字符和数字
  const toWords = (s: string) => s.toLowerCase().match(/[a-z0-9]+/g) || []
  const subWords = toWords(subText)
  if (subWords.length === 0) return null

  // 在 fullText 中按顺序寻找单词序列
  let currentPos = fromIndex
  let matchStart = -1
  let matchEnd = -1

  for (let i = 0; i < subWords.length; i++) {
    const word = subWords[i]
    // 寻找下一个单词的起始位置
    // 正则：忽略大小写，且要求是完整边界单词或部分字符（视分词结果）
    const regex = new RegExp(word, 'i')
    const textToSearch = fullText.slice(currentPos)
    const match = textToSearch.match(regex)

    if (!match || match.index === undefined) {
      return null // 序列中断，匹配失败
    }

    const absolutePos = currentPos + match.index
    if (i === 0) matchStart = absolutePos
    matchEnd = absolutePos + match[0].length
    currentPos = matchEnd
  }

  return {
    start: matchStart,
    length: matchEnd - matchStart
  }
}

export interface TextAtom {
  text: string
  chunkId?: string
  chunkLabel?: string
  isFirstInChunk?: boolean
}

/**
 * 将句子拆解为原子化片段，分配成分归属信息
 */
export function tokenizeSentenceWithAnalysis(text: string, chunks: { text: string; label: string }[]): TextAtom[] {
  const resultRanges: { start: number; end: number; label: string; id: string }[] = []
  let lastIndex = 0

  // 1. 寻找所有匹配区间
  chunks.forEach((chunk, idx) => {
    const match = findFuzzyMatch(text, chunk.text, lastIndex)
    if (match) {
      resultRanges.push({
        start: match.start,
        end: match.start + match.length,
        label: chunk.label,
        id: `chunk-${idx}`
      })
      lastIndex = match.start + match.length
    }
  })

  // 2. 按照区间边界切割原始文本
  const boundaries = new Set([0, text.length])
  resultRanges.forEach(r => {
    boundaries.add(r.start)
    boundaries.add(r.end)
  })
  const sortedBoundaries = Array.from(boundaries).sort((a, b) => a - b)

  const atoms: TextAtom[] = []
  for (let i = 0; i < sortedBoundaries.length - 1; i++) {
    const start = sortedBoundaries[i]
    const end = sortedBoundaries[i + 1]
    const content = text.slice(start, end)
    if (!content) continue

    const range = resultRanges.find(r => start >= r.start && end <= r.end)
    atoms.push({
      text: content,
      chunkId: range?.id,
      chunkLabel: range?.label,
      isFirstInChunk: range && start === range.start
    })
  }

  return atoms
}

export interface TokenRange {
  sentenceId: string
  anchorOffset: number
  extentOffset: number
}

export function findTokenOffset(sentenceText: string, tokenText: string, fromIndex?: number): { start: number; end: number } | null {
  const searchFrom = fromIndex ?? 0
  const idx = sentenceText.indexOf(tokenText, searchFrom)
  if (idx === -1) return null
  return { start: idx, end: idx + tokenText.length }
}

export function computeSelectionFromOffsets(
  sentenceText: string,
  startOffset: number,
  endOffset: number,
): { selectedText: string; anchorType: 'sentence' | 'text_range' } {
  const selectedText = sentenceText.slice(startOffset, endOffset)
  const isWholeSentence = startOffset === 0 && endOffset >= sentenceText.length
  return {
    selectedText,
    anchorType: isWholeSentence ? 'sentence' : 'text_range',
  }
}
