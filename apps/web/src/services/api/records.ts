import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  RecordDeleteResponseDto,
  RecordListResponseDto,
  RecordResponseDto,
} from "@/types/api/records";

export interface ListRecordsParams {
  page?: number;
  limit?: number;
  includeRenderScene?: boolean;
}

export function listRecords(
  sessionToken: string,
  params: ListRecordsParams = {},
): Promise<UpstreamResult<RecordListResponseDto>> {
  const searchParams = new URLSearchParams();

  if (params.page !== undefined) {
    searchParams.set("page", String(params.page));
  }

  if (params.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }

  if (params.includeRenderScene === true) {
    searchParams.set("include_render_scene", "true");
  }

  const query = searchParams.toString();

  return fastApiFetch<RecordListResponseDto>(`/records${query ? `?${query}` : ""}`, {
    sessionToken,
  });
}

export function getUpstreamRecordById(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<RecordResponseDto>> {
  return fastApiFetch<RecordResponseDto>(`/records/${encodeURIComponent(recordId)}`, {
    sessionToken,
  });
}

export function getUpstreamRecordByClientId(
  clientRecordId: string,
  sessionToken: string,
): Promise<UpstreamResult<RecordResponseDto>> {
  return fastApiFetch<RecordResponseDto>(
    `/records/by-client-id/${encodeURIComponent(clientRecordId)}`,
    { sessionToken },
  );
}

export function deleteUpstreamRecord(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<RecordDeleteResponseDto>> {
  return fastApiFetch<RecordDeleteResponseDto>(`/records/${encodeURIComponent(recordId)}`, {
    method: "DELETE",
    sessionToken,
  });
}
