/**
 * Cloud API: Favorites
 *
 * 对应后端 GET/POST/DELETE /favorites
 * 需要认证，自动附带 Authorization header
 */

import { request } from './client'
import type { FavoriteRecord } from '../../types/view/favorites.vm'

type ArticleFavoriteTargetType = 'analysis_record' | 'daily_reader_article'

// ---------------------------------------------------------------------------
// 后端 DTO（snake_case）
// ---------------------------------------------------------------------------

interface FavoriteResponseDto {
  id: string
  user_id: string
  target_type: ArticleFavoriteTargetType
  target_key: string
  analysis_record_id: string | null
  payload_json: Record<string, unknown>
  created_at: string
  updated_at: string
}

interface FavoriteListDto {
  items: FavoriteResponseDto[]
  total: number
}

function dtoToVm(dto: FavoriteResponseDto): FavoriteRecord {
  return {
    recordId: dto.target_key,
    cloudId: dto.analysis_record_id || undefined,
    createdAt: new Date(dto.created_at).getTime(),
  }
}

// ---------------------------------------------------------------------------
// API 调用
// ---------------------------------------------------------------------------

/**
 * 获取云端收藏列表
 */
export async function fetchCloudFavorites(): Promise<{ items: FavoriteRecord[]; total: number }> {
  const res = await request<FavoriteListDto>({
    url: '/favorites',
  })
  return {
    items: res.items.map(dtoToVm),
    total: res.total,
  }
}

/**
 * 添加收藏
 * @param cloudId 云端 UUID (analysis_record_id)
 * @param clientRecordId 本地记录 ID (target_key)
 */
export async function addFavoriteToCloud(
  cloudId: string | null,
  clientRecordId: string,
  targetType: ArticleFavoriteTargetType = 'analysis_record',
  payloadJson: Record<string, any> = {},
): Promise<{ id: string }> {
  return request<{ id: string; ok: boolean }>({
    url: '/favorites',
    method: 'POST',
    data: {
      target_type: 'analysis_record',
      target_key: clientRecordId,
      analysis_record_id: cloudId,
      payload_json: payloadJson,
    },
  }).then((r) => ({ id: r.id }))
}

/**
 * 移除收藏
 * @param cloudId 云端 UUID (analysis_record_id)
 */
export async function removeFavoriteFromCloud(
  targetKey: string,
  targetType: ArticleFavoriteTargetType = 'analysis_record',
): Promise<void> {
  await request<{ deleted: boolean }>({
    url: targetType === 'analysis_record'
      ? `/favorites/${targetKey}`
      : `/favorites/target?target_type=${encodeURIComponent(targetType)}&target_key=${encodeURIComponent(targetKey)}`,
    method: 'DELETE',
  })
}
