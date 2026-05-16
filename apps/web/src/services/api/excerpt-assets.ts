import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ExcerptAnchorTypeDto,
  ExcerptAssetsResponseDto,
  ExcerptAssetStateDto,
} from "@/types/api/excerpt-assets";

export interface ListExcerptAssetsParams {
  page?: number;
  limit?: number;
  recordId?: string;
  assetState?: ExcerptAssetStateDto;
  anchorType?: ExcerptAnchorTypeDto;
}

export function listExcerptAssets(
  sessionToken: string,
  params: ListExcerptAssetsParams = {},
): Promise<UpstreamResult<ExcerptAssetsResponseDto>> {
  const searchParams = new URLSearchParams();

  if (params.page !== undefined) {
    searchParams.set("page", String(params.page));
  }
  if (params.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }
  if (params.recordId) {
    searchParams.set("record_id", params.recordId);
  }
  if (params.assetState) {
    searchParams.set("asset_state", params.assetState);
  }
  if (params.anchorType) {
    searchParams.set("anchor_type", params.anchorType);
  }

  const query = searchParams.toString();
  return fastApiFetch<ExcerptAssetsResponseDto>(
    `/excerpt-assets${query ? `?${query}` : ""}`,
    { sessionToken },
  );
}
