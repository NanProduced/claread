import "server-only";

import { listExcerptAssets } from "@/services/api/excerpt-assets";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type {
  ExcerptAnchorTypeDto,
  ExcerptAssetStateDto,
  ExcerptInsightDto,
} from "@/types/api/excerpt-assets";

export type ExcerptAssetsBffStatus =
  | "ready"
  | "unauthenticated"
  | "mock_session"
  | "upstream_unavailable"
  | "upstream_error";

export interface ExcerptAssetItemVm {
  key: string;
  recordId: string;
  cloudRecordId: string;
  sentenceId: string | null;
  anchorType: ExcerptAnchorTypeDto;
  selectedText: string;
  translation: string | null;
  note: string | null;
  color: string | null;
  isFavorited: boolean;
  isHighlighted: boolean;
  isNoted: boolean;
  updatedAt: string;
  startOffset: number | null;
  endOffset: number | null;
  textHash: string | null;
  segments: Array<{
    paragraphId: string | null;
    sentenceId: string;
    selectedText: string;
    startOffset: number;
    endOffset: number;
    textHash: string;
  }>;
  insights: ExcerptInsightDto[];
}

export interface ExcerptAssetArticleVm {
  key: string;
  recordId: string;
  cloudRecordId: string;
  clientRecordId: string | null;
  title: string;
  subtitle: string | null;
  updatedAt: string;
  assetCount: number;
  items: ExcerptAssetItemVm[];
}

export interface ExcerptAssetsBffResult {
  status: ExcerptAssetsBffStatus;
  articles: ExcerptAssetArticleVm[];
  totalAssets: number;
  totalGroups: number;
  session: WebSession;
  message?: string;
}

export interface GetExcerptAssetsParams {
  page?: number;
  limit?: number;
  recordId?: string;
  assetState?: ExcerptAssetStateDto;
  anchorType?: ExcerptAnchorTypeDto;
}

function emptyResult(
  session: WebSession,
  status: ExcerptAssetsBffStatus,
  message?: string,
): ExcerptAssetsBffResult {
  return {
    status,
    articles: [],
    totalAssets: 0,
    totalGroups: 0,
    session,
    message,
  };
}

export async function getExcerptAssets(
  params: GetExcerptAssetsParams = {},
): Promise<ExcerptAssetsBffResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return emptyResult(
      session,
      session.kind === "mock_phone" ? "mock_session" : "unauthenticated",
      session.kind === "mock_phone"
        ? "当前登录态不能访问真实摘录，请使用真实登录会话。"
        : "当前会话已过期，请重新登录。",
    );
  }

  const upstreamResult = await listExcerptAssets(session.sessionToken, {
    page: params.page ?? 1,
    limit: params.limit ?? 50,
    recordId: params.recordId,
    assetState: params.assetState,
    anchorType: params.anchorType,
  });

  if (!upstreamResult.ok) {
    const unavailable = upstreamResult.status === 0 || upstreamResult.status >= 500;
    return emptyResult(
      session,
      unavailable ? "upstream_unavailable" : "upstream_error",
      unavailable ? "摘录服务暂时不可用，请稍后重试。" : upstreamResult.message,
    );
  }

  return {
    status: "ready",
    articles: upstreamResult.data.groups.map((group) => ({
      key: group.client_record_id ?? group.record_id,
      recordId: group.client_record_id ?? group.record_id,
      cloudRecordId: group.record_id,
      clientRecordId: group.client_record_id ?? null,
      title: group.title,
      subtitle: group.subtitle ?? null,
      updatedAt: group.updated_at,
      assetCount: group.asset_count,
      items: group.items.map((item) => ({
        key: item.target_key,
        recordId: group.client_record_id ?? group.record_id,
        cloudRecordId: group.record_id,
        sentenceId: item.sentence_id ?? null,
        anchorType: item.anchor_type,
        selectedText: item.selected_text,
        translation: item.translation ?? null,
        note: item.note ?? null,
        color: item.annotation_color ?? null,
        isFavorited: item.is_favorited,
        isHighlighted: item.is_highlighted,
        isNoted: item.is_noted,
        updatedAt: item.updated_at,
        startOffset: item.start_offset ?? null,
        endOffset: item.end_offset ?? null,
        textHash: item.text_hash ?? null,
        segments: item.segments.map((segment) => ({
          paragraphId: segment.paragraph_id ?? null,
          sentenceId: segment.sentence_id,
          selectedText: segment.selected_text,
          startOffset: segment.start_offset,
          endOffset: segment.end_offset,
          textHash: segment.text_hash,
        })),
        insights: item.insights,
      })),
    })),
    totalAssets: upstreamResult.data.total_assets,
    totalGroups: upstreamResult.data.total_groups,
    session,
  };
}
