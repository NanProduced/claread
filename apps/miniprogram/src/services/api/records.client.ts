/**
 * Cloud API: Analysis Records
 *
 * 对应后端 GET/POST/PATCH/DELETE /records
 * 需要认证，自动附带 Authorization header
 */

import { request, ApiError } from './client'
import type { AnalysisRecord } from '../../types/view/analysis-record.vm'
import type { AnalyzeRequest } from './client'
import { analyzeResponseDtoToVm, vmToAnalyzeResponseDto } from './adapters/render-scene.adapter'
import type { AnyAnalyzeResponseDto } from '../../types/api/analyze-response.dto'

// ---------------------------------------------------------------------------
// 后端 DTO（snake_case）
// ---------------------------------------------------------------------------

interface RecordResponseDto {
  id: string
  user_id: string
  client_record_id: string
  source_type: string
  title: string | null
  source_text: string
  source_text_hash: string
  request_payload_json: {
    reading_goal?: string
    reading_variant?: string
    source_type?: string
  }
  render_scene_json: Record<string, unknown> | null
  page_state_json: Record<string, unknown> | null
  reading_goal: string | null
  reading_variant: string | null
  extended: boolean
  user_facing_state: string | null
  workflow_version: string | null
  schema_version: string | null
  analysis_status: string
  last_opened_at: string | null
  created_at: string
  updated_at: string
}

interface RecordListDto {
  items: RecordResponseDto[]
  total: number
  page: number
  limit: number
}

interface RecordUpsertDto {
  id: string
  client_record_id: string
  created: boolean
  updated_at: string
}

// ---------------------------------------------------------------------------
// 字段映射：后端 DTO → 前端 VM
// ---------------------------------------------------------------------------

function dtoToVm(dto: RecordResponseDto): AnalysisRecord {
  const payload = dto.request_payload_json || {}

  let renderSceneVm = null
  if (dto.render_scene_json) {
    const rawScene = dto.render_scene_json as Record<string, unknown>
    const isObject = typeof rawScene === 'object' && rawScene !== null && !Array.isArray(rawScene)
    const isEmptyObject = isObject && Object.keys(rawScene).length === 0

    if (!isEmptyObject) {
      // 启发式判断：如果存在 schema_version 或者存在典型的 snake_case 字段且不存在典型的 camelCase 字段
      const isSnakeCase =
        'schema_version' in rawScene ||
        ('user_facing_state' in rawScene && !('userFacingState' in rawScene)) ||
        ('inline_marks' in rawScene && !('inlineMarks' in rawScene))
      const looksLikeVm = isObject && 'article' in rawScene && 'request' in rawScene

      try {
        if (isSnakeCase) {
          renderSceneVm = analyzeResponseDtoToVm(rawScene as unknown as AnyAnalyzeResponseDto)
        } else if (looksLikeVm) {
          renderSceneVm = rawScene as unknown as AnalysisRecord['renderScene']
        }
      } catch {
        renderSceneVm = null
      }
    }
  }

  const VALID_PAGE_STATES = new Set<string>(['loading', 'normal', 'degraded_light', 'degraded_heavy', 'empty', 'failed', 'timeout', 'network_fail'])

  let pageState: AnalysisRecord['pageState'] = 'normal'
  if (dto.analysis_status === 'failed' || dto.analysis_status === 'cancelled') {
    pageState = 'failed'
  } else if (dto.analysis_status === 'queued' || dto.analysis_status === 'running' || dto.analysis_status === 'finalizing') {
    pageState = 'loading'
  } else if (dto.page_state_json && typeof dto.page_state_json === 'object') {
    const raw = (dto.page_state_json as Record<string, unknown>).pageState
    if (typeof raw === 'string' && VALID_PAGE_STATES.has(raw)) {
      pageState = raw as AnalysisRecord['pageState']
    }
  }

  return {
    recordId: dto.client_record_id,
    cloudId: dto.id,
    title: dto.title,
    sourceText: dto.source_text,
    requestPayload: {
      reading_goal: payload.reading_goal as AnalyzeRequest['reading_goal'],
      reading_variant: payload.reading_variant as AnalyzeRequest['reading_variant'],
      source_type: (payload.source_type as AnalyzeRequest['source_type']) || 'user_input',
    },
    renderScene: renderSceneVm,
    pageState,
    createdAt: new Date(dto.created_at).getTime(),
    updatedAt: new Date(dto.updated_at).getTime(),
    isFavorited: false, // 云端不存这个，前端本地维护
  }
}

// ---------------------------------------------------------------------------
// API 调用
// ---------------------------------------------------------------------------

export interface SaveRecordParams {
  clientRecordId: string
  title?: string | null
  sourceText: string
  sourceTextHash: string
  requestPayload: {
    reading_goal: AnalyzeRequest['reading_goal']
    reading_variant: AnalyzeRequest['reading_variant']
    source_type: AnalyzeRequest['source_type']
  }
  renderScene: AnalysisRecord['renderScene']
  pageState: string
  userFacingState?: string | null
  workflowVersion?: string | null
  schemaVersion?: string | null
}

/**
 * 保存分析记录到云端（upsert by clientRecordId）
 */
export async function saveRecordToCloud(
  params: SaveRecordParams
): Promise<{ id: string; created: boolean }> {
  const res = await request<RecordUpsertDto>({
    url: '/records',
    method: 'POST',
    data: {
      client_record_id: params.clientRecordId,
      source_type: params.requestPayload.source_type,
      title: params.title ?? null,
      source_text: params.sourceText,
      source_text_hash: params.sourceTextHash,
      request_payload_json: params.requestPayload,
      render_scene_json: params.renderScene ? vmToAnalyzeResponseDto(params.renderScene) : {},
      page_state_json: { pageState: params.pageState },
      reading_goal: params.requestPayload.reading_goal,
      reading_variant: params.requestPayload.reading_variant,
      user_facing_state: params.userFacingState,
      workflow_version: params.workflowVersion,
      schema_version: params.schemaVersion,
      analysis_status: params.renderScene ? 'ready' : 'failed',
    },
  })
  return { id: res.id, created: res.created }
}

/**
 * 获取云端记录列表
 */
export async function fetchCloudRecords(
  page = 1,
  limit = 20
): Promise<{ items: AnalysisRecord[]; total: number }> {
  const res = await request<RecordListDto>({
    url: `/records?page=${page}&limit=${limit}`,
  })
  return {
    items: res.items.map(dtoToVm),
    total: res.total,
  }
}

/**
 * 获取单条云端记录
 */
export async function fetchCloudRecord(recordId: string): Promise<AnalysisRecord | null> {
  try {
    const res = await request<RecordResponseDto>({
      url: `/records/${recordId}`,
    })
    return dtoToVm(res)
  } catch (err: unknown) {
    if (err instanceof ApiError && err.statusCode === 404) return null
    throw err
  }
}

/**
 * 按 client_record_id 获取云端记录
 */
export async function fetchCloudRecordByClientId(clientRecordId: string): Promise<AnalysisRecord | null> {
  try {
    const res = await request<RecordResponseDto>({
      url: `/records/by-client-id/${encodeURIComponent(clientRecordId)}`,
    })
    return dtoToVm(res)
  } catch (err: unknown) {
    if (err instanceof ApiError && err.statusCode === 404) return null
    throw err
  }
}

/**
 * 更新云端记录
 */
export async function updateCloudRecord(
  recordId: string,
  patch: {
    title?: string
    render_scene_json?: Record<string, unknown>
    page_state_json?: Record<string, unknown>
    user_facing_state?: string
    analysis_status?: string
    last_opened_at?: string
  }
): Promise<void> {
  await request<void>({
    url: `/records/${recordId}`,
    method: 'PATCH',
    data: patch,
  })
}

/**
 * 删除云端记录
 */
export async function deleteCloudRecord(recordId: string): Promise<void> {
  await request<void>({
    url: `/records/${recordId}`,
    method: 'DELETE',
  })
}
