import "server-only";

import { getUpstreamCreditLedger, type CreditLedgerParams } from "@/services/api/quota";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type { LedgerEntryResponseDto } from "@/types/api/quota";
import type { LedgerEntryVm, LedgerListVm } from "@/types/view/LedgerEntryVm";

export type CreditLedgerBffStatus =
  | "ready"
  | "unauthenticated"
  | "limited_debug"
  | "upstream_unavailable"
  | "upstream_error";

export interface CreditLedgerBffResult {
  status: CreditLedgerBffStatus;
  data: LedgerListVm | null;
  httpStatus: number;
  message?: string;
}

function upstreamStatus(status: number): CreditLedgerBffStatus {
  return status === 0 || status >= 500 ? "upstream_unavailable" : "upstream_error";
}

function projectLedgerEntry(dto: LedgerEntryResponseDto): LedgerEntryVm {
  return {
    id: dto.id,
    entryType: dto.entry_type,
    points: dto.points,
    bucketType: dto.bucket_type,
    balanceAfter: dto.balance_after,
    description: dto.description,
    articleTitle: dto.article_title,
    metadata: dto.metadata ?? {},
    taskId: dto.task_id,
    createdAt: dto.created_at,
  };
}

function unauthenticatedResult(session: WebSession): CreditLedgerBffResult {
  return {
    status: session.kind === "mock_phone" ? "limited_debug" : "unauthenticated",
    data: null,
    httpStatus: 401,
    message:
      session.kind === "mock_phone"
        ? "当前登录态不能访问真实积分记录，请使用真实登录会话后查看。"
        : "当前会话已过期，请重新登录。",
  };
}

export async function getCreditLedger(
  params: CreditLedgerParams = {},
): Promise<CreditLedgerBffResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return unauthenticatedResult(session);
  }

  const upstreamResult = await getUpstreamCreditLedger(
    session.sessionToken,
    params,
  );

  if (!upstreamResult.ok) {
    return {
      status: upstreamStatus(upstreamResult.status),
      data: null,
      httpStatus: upstreamResult.status === 0 ? 503 : upstreamResult.status,
      message:
        upstreamResult.status === 0 || upstreamResult.status >= 500
          ? "积分记录服务暂时不可用，请稍后重试。"
          : upstreamResult.message,
    };
  }

  return {
    status: "ready",
    data: {
      items: upstreamResult.data.items.map(projectLedgerEntry),
      cursor: upstreamResult.data.cursor,
      hasMore: upstreamResult.data.has_more,
    },
    httpStatus: 200,
  };
}
