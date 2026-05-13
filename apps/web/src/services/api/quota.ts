import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type { QuotaResponseDto } from "@/types/api/quota";

export function getUpstreamQuota(
  sessionToken: string,
): Promise<UpstreamResult<QuotaResponseDto>> {
  return fastApiFetch<QuotaResponseDto>("/me/quota", {
    sessionToken,
  });
}
