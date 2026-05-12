import { request } from './client'

interface FeedbackSubmitDto {
  id: string
  feedback_scope: string
  target_id: string
  sentiment: string
  feedback_type: string
  status: string
  created_at: string
}

interface FeedbackListItemDto {
  id: string
  feedback_scope: string
  feedback_type: string
  sentiment: string
  content: string | null
  status: string
  reward_points: number
  created_at: string
}

interface FeedbackListDto {
  items: FeedbackListItemDto[]
  cursor: string | null
  has_more: boolean
}

export interface FeedbackSubmitResult {
  id: string
  feedbackScope: string
  targetId: string
  sentiment: string
  feedbackType: string
  status: string
  createdAt: string
}

export interface FeedbackListItem {
  id: string
  feedbackScope: string
  feedbackType: string
  sentiment: string
  content: string | null
  status: string
  rewardPoints: number
  createdAt: string
}

export interface FeedbackListResult {
  items: FeedbackListItem[]
  cursor: string | null
  hasMore: boolean
}

function dtoToFeedbackListItem(dto: FeedbackListItemDto): FeedbackListItem {
  return {
    id: dto.id,
    feedbackScope: dto.feedback_scope,
    feedbackType: dto.feedback_type,
    sentiment: dto.sentiment,
    content: dto.content,
    status: dto.status,
    rewardPoints: dto.reward_points,
    createdAt: dto.created_at,
  }
}

export async function submitFeedback(params: {
  feedbackScope: string
  targetId: string
  analysisRecordId?: string
  sentiment: string
  feedbackType: string
  annotationType?: string
  content?: string
  contextJson?: Record<string, unknown>
  appVersion?: string
}): Promise<FeedbackSubmitResult> {
  const dto = await request<FeedbackSubmitDto>({
    url: '/feedback',
    method: 'POST',
    data: {
      feedback_scope: params.feedbackScope,
      target_id: params.targetId,
      analysis_record_id: params.analysisRecordId,
      sentiment: params.sentiment,
      feedback_type: params.feedbackType,
      annotation_type: params.annotationType,
      content: params.content,
      context_json: params.contextJson || {},
      app_version: params.appVersion,
    },
  })
  return {
    id: dto.id,
    feedbackScope: dto.feedback_scope,
    targetId: dto.target_id,
    sentiment: dto.sentiment,
    feedbackType: dto.feedback_type,
    status: dto.status,
    createdAt: dto.created_at,
  }
}

export async function fetchFeedbackList(params: {
  cursor?: string
  limit?: number
  feedbackScope?: string
}): Promise<FeedbackListResult> {
  const query: Record<string, string> = {}
  if (params.cursor) query.cursor = params.cursor
  if (params.limit) query.limit = String(params.limit)
  if (params.feedbackScope) query.feedback_scope = params.feedbackScope

  const qs = Object.entries(query).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
  const url = `/feedback${qs ? `?${qs}` : ''}`

  const dto = await request<FeedbackListDto>({ url })
  return {
    items: dto.items.map(dtoToFeedbackListItem),
    cursor: dto.cursor,
    hasMore: dto.has_more,
  }
}

export async function deleteFeedback(feedbackId: string): Promise<void> {
  await request<void>({
    url: `/feedback/${feedbackId}`,
    method: 'DELETE',
  })
}
