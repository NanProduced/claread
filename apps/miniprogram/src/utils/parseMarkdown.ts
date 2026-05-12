/**
 * 简单的 Markdown 解析工具
 * 支持：**粗体**、*斜体*、`行内代码`、- 列表项、> 引用块、## 标题、\n\n 段落分隔
 * 小程序环境使用，不引入外部库
 */

export interface ParsedSegment {
  text: string
  bold?: boolean
  italic?: boolean
  code?: boolean
  list?: boolean
  quote?: boolean
  header?: boolean
}

/**
 * 解析 Markdown 文本为带样式的片段数组
 */
export function parseMarkdown(text: string): ParsedSegment[] {
  const result: ParsedSegment[] = []
  const lines = text.split('\n')

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmedLine = line.trim()

    if (trimmedLine === '') {
      continue
    }

    let processedContent = trimmedLine
    let isList = false
    let isQuote = false
    let isHeader = false

    // 1. 块级语义匹配
    if (/^[-*]\s/.test(trimmedLine)) {
      processedContent = trimmedLine.replace(/^[-*]\s/, '')
      isList = true
    } else if (/^>\s/.test(trimmedLine)) {
      processedContent = trimmedLine.replace(/^>\s/, '')
      isQuote = true
    } else if (/^#{1,6}\s/.test(trimmedLine)) {
      processedContent = trimmedLine.replace(/^#{1,6}\s/, '')
      isHeader = true
    }

    // 2. 行内样式匹配
    const lineSegments = parseInlineTokens(processedContent)

    // 3. 将块级语义附加到所有子片段，并合并到结果
    if (isList) {
      // 列表首个片段加个符号
      if (lineSegments.length > 0) {
        lineSegments[0].text = '• ' + lineSegments[0].text
      }
    }

    lineSegments.forEach(seg => {
      if (isList) seg.list = true
      if (isQuote) seg.quote = true
      if (isHeader) seg.header = true
      result.push(seg)
    })

    // 在行末强制加一个换行占位（除最后一行外）
    if (i < lines.length - 1) {
      result.push({ text: '\n' })
    }
  }

  return result
}

/**
 * 解析一行的行内元素（代码、粗体、斜体）
 */
function parseInlineTokens(text: string): ParsedSegment[] {
  const segments: ParsedSegment[] = []
  let lastIndex = 0
  const codeRegex = /`([^`]+)`/g
  let codeMatch

  while ((codeMatch = codeRegex.exec(text)) !== null) {
    if (codeMatch.index > lastIndex) {
      parseBoldAndItalic(text.slice(lastIndex, codeMatch.index), segments)
    }
    segments.push({ text: codeMatch[1], code: true })
    lastIndex = codeMatch.index + codeMatch[0].length
  }

  if (lastIndex < text.length) {
    parseBoldAndItalic(text.slice(lastIndex), segments)
  }

  return segments
}

function parseBoldAndItalic(text: string, segments: ParsedSegment[]): void {
  const boldRegex = /\*\*([^*]+)\*\*/g
  let lastIndex = 0
  let match

  while ((match = boldRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parseItalic(text.slice(lastIndex, match.index), segments)
    }
    segments.push({ text: match[1], bold: true })
    lastIndex = match.index + match[0].length
  }

  if (lastIndex < text.length) {
    parseItalic(text.slice(lastIndex), segments)
  }
}

function parseItalic(text: string, segments: ParsedSegment[]): void {
  const italicRegex = /(?<!\*)\*([^*]+)\*(?!\*)/g
  let lastIndex = 0
  let match

  while ((match = italicRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index) })
    }
    segments.push({ text: match[1], italic: true })
    lastIndex = match.index + match[0].length
  }

  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex) })
  }
}
