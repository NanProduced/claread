import { request } from './client'
import type { UserAnnotationAnchorType, UserAnnotationColor } from '@claread/contracts'

export interface UserAnchorSegmentDto {
  paragraph_id?: string
  sentence_id: string
  selected_text: string
  start_offset: number
  end_offset: number
  text_hash: string
}

export interface UserAnnotationCreateDto {
  analysis_record_id?: string
  anchor_type?: UserAnnotationAnchorType
  target_key?: string
  paragraph_id?: string
  sentence_id: string
  selected_text: string
  start_offset?: number
  end_offset?: number
  text_hash?: string
  segments?: UserAnchorSegmentDto[]
  color?: UserAnnotationColor
  payload_json?: Record<string, unknown>
}

export interface UserAnnotationUpdateDto {
  color?: string
}

export interface UserAnnotationDto {
  id: string
  analysis_record_id?: string
  anchor_type: UserAnnotationAnchorType
  target_key: string
  paragraph_id?: string
  sentence_id: string
  selected_text: string
  start_offset?: number
  end_offset?: number
  text_hash?: string
  segments?: UserAnchorSegmentDto[]
  color: UserAnnotationColor
  payload_json: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface UserAnnotationListDto {
  items: UserAnnotationDto[]
}

export async function createUserAnnotation(data: UserAnnotationCreateDto): Promise<UserAnnotationDto> {
  return request<UserAnnotationDto>({
    url: '/user-annotations',
    method: 'POST',
    data,
  })
}

export async function listUserAnnotations(recordId?: string, limit = 200): Promise<UserAnnotationDto[]> {
  const params = new URLSearchParams()
  if (recordId) params.set('analysis_record_id', recordId)
  params.set('limit', String(limit))
  const query = params.toString()
  const url = query ? `/user-annotations?${query}` : '/user-annotations'
  const res = await request<UserAnnotationListDto>({
    url,
    method: 'GET',
  })
  return res.items
}

export async function updateUserAnnotation(id: string, data: UserAnnotationUpdateDto): Promise<UserAnnotationDto> {
  return request<UserAnnotationDto>({
    url: `/user-annotations/${id}`,
    method: 'PATCH',
    data,
  })
}

export async function deleteUserAnnotation(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>({
    url: `/user-annotations/${id}`,
    method: 'DELETE',
  })
}
