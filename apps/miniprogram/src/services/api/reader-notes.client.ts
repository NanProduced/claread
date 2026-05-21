import { request } from './client'

export interface ReaderNoteSegmentDto {
  paragraph_id?: string
  sentence_id: string
  selected_text: string
  start_offset: number
  end_offset: number
  text_hash: string
}

export interface ReaderNoteDto {
  id: string
  analysis_record_id: string
  anchor_sentence_id: string
  quote_mode: 'sentence' | 'text_range' | 'multi_text'
  target_key: string
  paragraph_id?: string | null
  sentence_id?: string | null
  selected_text: string
  start_offset?: number | null
  end_offset?: number | null
  text_hash?: string | null
  segments: ReaderNoteSegmentDto[]
  note_text: string
  payload_json: Record<string, unknown>
  created_at: string
  updated_at: string
}

interface ReaderNoteListDto {
  items: ReaderNoteDto[]
}

export async function listReaderNotes(recordId: string): Promise<ReaderNoteDto[]> {
  const params = new URLSearchParams()
  params.set('record_id', recordId)
  const res = await request<ReaderNoteListDto>({
    url: `/reader-notes?${params.toString()}`,
    method: 'GET',
  })
  return res.items
}
