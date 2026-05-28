import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type { QuotaResponseDto, LedgerListResponseDto } from "@/types/api/quota";

export function getUpstreamQuota(
  sessionToken: string,
): Promise<UpstreamResult<QuotaResponseDto>> {
  return fastApiFetch<QuotaResponseDto>("/me/quota", {
    sessionToken,
  });
}

export interface CreditLedgerParams {
  cursor?: string;
  limit?: number;
}

export function getUpstreamCreditLedger(
  sessionToken: string,
  params: CreditLedgerParams = {},
): Promise<UpstreamResult<LedgerListResponseDto>> {
  const searchParams = new URLSearchParams();
  if (params.cursor) searchParams.set("cursor", params.cursor);
  if (params.limit !== undefined) searchParams.set("limit", String(params.limit));
  const query = searchParams.toString();
  return fastApiFetch<LedgerListResponseDto>(
    `/me/credit/ledger${query ? `?${query}` : ""}`,
    { sessionToken },
  );
}
