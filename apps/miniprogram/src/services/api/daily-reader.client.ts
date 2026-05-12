import { request } from './client'
import type {
  DailyReaderTodayResponseDto,
  DailyReaderArticleDto,
  DailyReaderListResponseDto,
} from '../../types/api/daily-reader.dto'
import type { DailyReaderArticle, DailyReaderListItem } from '../../types/view/daily-reader.vm'
import { dtoToDailyReaderArticle, dtoToDailyReaderListItem } from './adapters/daily-reader.adapter'

export async function fetchTodayArticles(): Promise<DailyReaderArticle[]> {
  const data = await request<DailyReaderTodayResponseDto>({
    url: '/daily-reader/today',
    method: 'GET',
  })
  return (data.articles ?? []).map(dtoToDailyReaderArticle)
}

export async function fetchArticleById(id: string): Promise<DailyReaderArticle> {
  const dto = await request<DailyReaderArticleDto>({
    url: `/daily-reader/${id}`,
    method: 'GET',
  })
  return dtoToDailyReaderArticle(dto)
}

export async function fetchArticleList(
  cursor?: string,
  limit: number = 10,
): Promise<{ items: DailyReaderListItem[]; hasMore: boolean; cursor: string | null }> {
  const params: Record<string, string | number> = { limit }
  if (cursor) params.cursor = cursor

  const data = await request<DailyReaderListResponseDto>({
    url: '/daily-reader',
    method: 'GET',
    data: params,
  })

  return {
    items: (data.items ?? []).map(dtoToDailyReaderListItem),
    hasMore: data.has_more ?? false,
    cursor: data.cursor,
  }
}
