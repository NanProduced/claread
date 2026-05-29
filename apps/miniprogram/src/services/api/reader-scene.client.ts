import { ApiError, request } from './client'
import { analyzeResponseDtoToVm } from './adapters/render-scene.adapter'
import type { AnalyzeRequest } from './client'
import type { AnyAnalyzeResponseDto } from '../../types/api/analyze-response.dto'
import type { AnalysisRecord } from '../../types/view/analysis-record.vm'

interface ReaderSceneRecordMetaDto {
  id: string
  client_record_id: string | null
  title: string | null
  source_type: string
  source_text: string
  request_payload_json: {
    reading_goal?: string
    reading_variant?: string
    source_type?: string
    [key: string]: unknown
  }
  reading_goal: string | null
  reading_variant: string | null
  analysis_status: string
  user_facing_state: string | null
  workflow_version: string | null
  schema_version: string | null
  created_at: string
  updated_at: string
}

interface ReaderSceneResponseDto {
  record_meta: ReaderSceneRecordMetaDto
  reader_scene: Record<string, unknown>
  view_meta: {
    data_source: 'render_scene_snapshot' | 'source_text_fallback'
    fallback_mode: 'none' | 'article_rebuilt_from_source_text' | 'scene_missing'
    supplements_merged: boolean
    view_version: string
  }
}

function derivePageState(dto: ReaderSceneRecordMetaDto): AnalysisRecord['pageState'] {
  if (dto.analysis_status === 'failed' || dto.analysis_status === 'cancelled') {
    return 'failed'
  }
  if (dto.analysis_status === 'queued' || dto.analysis_status === 'running' || dto.analysis_status === 'finalizing') {
    return 'loading'
  }
  if (dto.user_facing_state === 'degraded_light' || dto.user_facing_state === 'degraded_heavy') {
    return dto.user_facing_state
  }
  return 'normal'
}

function dtoToVm(dto: ReaderSceneResponseDto): AnalysisRecord {
  const meta = dto.record_meta
  const payload = meta.request_payload_json || {}

  return {
    recordId: meta.client_record_id || meta.id,
    cloudId: meta.id,
    title: meta.title,
    sourceText: meta.source_text,
    requestPayload: {
      reading_goal: (payload.reading_goal as AnalyzeRequest['reading_goal']) || (meta.reading_goal as AnalyzeRequest['reading_goal']),
      reading_variant: (payload.reading_variant as AnalyzeRequest['reading_variant']) || (meta.reading_variant as AnalyzeRequest['reading_variant']),
      source_type: (payload.source_type as AnalyzeRequest['source_type']) || (meta.source_type as AnalyzeRequest['source_type']) || 'user_input',
    },
    renderScene: analyzeResponseDtoToVm(dto.reader_scene as unknown as AnyAnalyzeResponseDto),
    pageState: derivePageState(meta),
    createdAt: new Date(meta.created_at).getTime(),
    updatedAt: new Date(meta.updated_at).getTime(),
    isFavorited: false,
  }
}

export async function fetchCloudReaderScene(recordId: string): Promise<AnalysisRecord | null> {
  try {
    const res = await request<ReaderSceneResponseDto>({
      url: `/reader/records/${recordId}/scene`,
    })
    return dtoToVm(res)
  } catch (err: unknown) {
    if (err instanceof ApiError && err.statusCode === 404) return null
    throw err
  }
}

export async function fetchCloudReaderSceneByClientId(clientRecordId: string): Promise<AnalysisRecord | null> {
  try {
    const res = await request<ReaderSceneResponseDto>({
      url: `/reader/records/by-client-id/${encodeURIComponent(clientRecordId)}/scene`,
    })
    return dtoToVm(res)
  } catch (err: unknown) {
    if (err instanceof ApiError && err.statusCode === 404) return null
    throw err
  }
}
