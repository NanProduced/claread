import "server-only";

import { dtoToDailyReaderArticle, dtoToDailyReaderListItem } from "@/adapters/daily-reader.adapter";
import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  DailyReaderArticleDto,
  DailyReaderListResponseDto,
  DailyReaderTodayResponseDto,
} from "@/types/api/daily-reader";
import type { DailyReaderArticle, DailyReaderListItem } from "@/types/view/DailyReaderVm";

export async function fetchDailyReaderToday(): Promise<UpstreamResult<DailyReaderArticle[]>> {
  const result = await fastApiFetch<DailyReaderTodayResponseDto>("/daily-reader/today");

  if (!result.ok) {
    return result;
  }

  return {
    ok: true,
    data: (result.data.articles ?? []).map(dtoToDailyReaderArticle),
  };
}

export async function fetchDailyReaderArticle(
  articleId: string,
): Promise<UpstreamResult<DailyReaderArticle>> {
  const result = await fastApiFetch<DailyReaderArticleDto>(
    `/daily-reader/${encodeURIComponent(articleId)}`,
  );

  if (!result.ok) {
    return result;
  }

  return { ok: true, data: dtoToDailyReaderArticle(result.data) };
}

export async function fetchDailyReaderList(input: {
  cursor?: string;
  limit?: number;
} = {}): Promise<UpstreamResult<{ items: DailyReaderListItem[]; cursor: string | null; hasMore: boolean }>> {
  const params = new URLSearchParams({ limit: String(input.limit ?? 10) });

  if (input.cursor) {
    params.set("cursor", input.cursor);
  }

  const result = await fastApiFetch<DailyReaderListResponseDto>(`/daily-reader?${params.toString()}`);

  if (!result.ok) {
    return result;
  }

  return {
    ok: true,
    data: {
      items: (result.data.items ?? []).map(dtoToDailyReaderListItem),
      cursor: result.data.cursor,
      hasMore: result.data.has_more,
    },
  };
}
