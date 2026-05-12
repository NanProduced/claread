import type {
  DailyReaderArticleDto,
  DailyReaderTodayResponseDto,
  DailyReaderListItemDto,
  DailyReaderListResponseDto,
  DailyReaderParagraphNoteDto,
  DailyReaderParagraphNotesDto,
  DailyReaderTakeawaysDto,
} from '../../../types/api/daily-reader.dto'
import type {
  DailyReaderArticle,
  DailyReaderHighlight,
  DailyReaderListItem,
  DailyReaderFooterAnalysis,
} from '../../../types/view/daily-reader.vm'
import { decodeHtmlEntities } from '../../../utils/html-entities'

function stripHtml(value: string | null): string | null {
  if (!value) return value
  return decodeHtmlEntities(value.replace(/<[^>]+>/g, '').trim())
}

function cleanText(value: string | null | undefined): string {
  return decodeHtmlEntities(value ?? '').trim()
}

export function dtoToDailyReaderArticle(dto: DailyReaderArticleDto): DailyReaderArticle {
  const paragraphNotes = dto.paragraph_notes ?? null
  const noteMap = buildParagraphNoteMap(paragraphNotes)

  return {
    id: dto.id,
    title: cleanText(dto.title),
    subtitle: stripHtml(dto.subtitle),
    source: dto.source,
    sourceUrl: dto.source_url,
    publishDate: dto.publish_date,
    difficulty: dto.difficulty,
    readTimeMinutes: dto.read_time_minutes,
    tags: Array.isArray(dto.tags) ? dto.tags : [],
    coverImageUrl: dto.cover_image_url,
    coverTheme: dto.cover_theme,
    preReadingGuide: dtoToPreReadingGuide(paragraphNotes),
    body: {
      paragraphs: Array.isArray(dto.body?.paragraphs)
        ? dto.body.paragraphs.map((p) => {
            const note = p.reading_note ?? noteMap[p.id]
            return {
              id: p.id,
              text: p.text,
              highlights: Array.isArray(p.highlights) ? p.highlights.map(dtoToHighlight) : [],
              readingNote: note
                ? {
                    focusQuestion: cleanText(note.focus_question),
                    microSummary: cleanText(note.micro_summary),
                  }
                : undefined,
              translation: note?.translation ? cleanText(note.translation) : undefined,
            }
          })
        : [],
    },
    highlights: Array.isArray(dto.highlights) ? dto.highlights.map(dtoToHighlight) : [],
    footerAnalysis: dtoToFooterAnalysis(dto.footer_analysis, dto.takeaways),
  }
}

export function dtoToDailyReaderListItem(dto: DailyReaderListItemDto): DailyReaderListItem {
  return {
    id: dto.id,
    title: cleanText(dto.title),
    subtitle: stripHtml(dto.subtitle),
    source: dto.source,
    publishDate: dto.publish_date,
    difficulty: dto.difficulty,
    readTimeMinutes: dto.read_time_minutes,
    tags: dto.tags,
    coverImageUrl: dto.cover_image_url,
    coverTheme: dto.cover_theme,
  }
}

function dtoToHighlight(dto: DailyReaderArticleDto['highlights'][0]): DailyReaderHighlight {
  return {
    id: dto.id,
    type: dto.type,
    text: cleanText(dto.text),
    gloss: cleanText(dto.gloss),
    paragraphId: dto.paragraph_id,
    start: dto.start,
    end: dto.end,
    detail: dto.detail
      ? {
          phonetic: cleanText(dto.detail.phonetic),
          pos: cleanText(dto.detail.pos),
          contextExplanation: cleanText(dto.detail.context_explanation),
        }
      : null,
  }
}

function buildParagraphNoteMap(dto: DailyReaderParagraphNotesDto | null): Record<string, DailyReaderParagraphNoteDto> {
  if (!dto || !Array.isArray(dto.notes)) return {}
  return dto.notes.reduce<Record<string, DailyReaderParagraphNoteDto>>((acc, note) => {
    if (note?.paragraph_id) acc[note.paragraph_id] = note
    return acc
  }, {})
}

function dtoToPreReadingGuide(dto: DailyReaderParagraphNotesDto | null): DailyReaderArticle['preReadingGuide'] {
  if (!dto) return undefined

  const overview = cleanText(dto.article_summary)
  const questions = normalizeReadingFocus(dto.reading_focus)
  if (!overview && questions.length === 0) return undefined

  return {
    overview,
    questions,
  }
}

function normalizeReadingFocus(value: string[] | string | undefined): string[] {
  if (Array.isArray(value)) return value.map((item) => cleanText(item)).filter(Boolean).slice(0, 2)
  if (!value) return []
  return value
    .split(/\n|[；;]|(?:\d+[.、]\s*)/)
    .map((item) => cleanText(item))
    .filter(Boolean)
    .slice(0, 2)
}

function dtoToFooterAnalysis(
  dto: DailyReaderArticleDto['footer_analysis'],
  takeaways?: DailyReaderTakeawaysDto | null,
): DailyReaderFooterAnalysis {
  const thesisAndIntent = dto?.thesis_and_intent
  return {
    summary: cleanText(dto?.summary),
    thesisAndIntent: {
      thesis: cleanText(thesisAndIntent?.thesis),
      authorIntent: cleanText(thesisAndIntent?.author_intent),
    },
    structure: Array.isArray(dto?.structure) ? dto.structure.map((s) => ({
      label: cleanText(s.label),
      title: cleanText(s.title),
      summary: cleanText(s.summary),
    })) : [],
    keyExpressions: Array.isArray(takeaways?.key_expressions) ? takeaways.key_expressions.map((e) => ({
      expression: cleanText(e.expression),
      gloss: cleanText(e.gloss),
      contextSentence: cleanText(e.context_sentence),
      paragraphId: e.paragraph_id,
      usageNote: cleanText(e.usage_note),
    })) : Array.isArray(dto?.key_expressions) ? dto.key_expressions.map((e) => ({
      expression: cleanText(e.expression),
      gloss: cleanText(e.gloss),
      contextSentence: cleanText(e.context_sentence),
    })) : [],
    misreadingPoints: Array.isArray(dto?.misreading_points) ? dto.misreading_points.map((m) => ({
      point: cleanText(m.point),
      clarification: cleanText(m.clarification),
    })) : [],
    fullArticleAnalysis: cleanText(dto?.full_article_analysis),
    discussionQuestions: Array.isArray(takeaways?.discussion_questions)
      ? takeaways.discussion_questions.map((q) => cleanText(q)).filter(Boolean)
      : Array.isArray(dto?.discussion_questions) ? dto.discussion_questions.map((q) => cleanText(q)).filter(Boolean) : [],
    articleTakeaway: takeaways?.article_takeaway ? cleanText(takeaways.article_takeaway) : undefined,
    sentenceNotes: Array.isArray(takeaways?.sentence_notes) ? takeaways.sentence_notes.map((note) => ({
      sentence: cleanText(note.sentence),
      paragraphId: note.paragraph_id,
      translation: cleanText(note.translation),
      breakdown: cleanText(note.breakdown),
      takeaway: cleanText(note.takeaway),
    })) : undefined,
    writingMoves: Array.isArray(takeaways?.writing_moves) ? takeaways.writing_moves.map((move) => ({
      anchor: cleanText(move.anchor),
      paragraphId: move.paragraph_id,
      moveType: cleanText(move.move_type),
      explanation: cleanText(move.explanation),
      reusablePattern: cleanText(move.reusable_pattern),
    })) : undefined,
  }
}
