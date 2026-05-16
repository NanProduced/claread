import "server-only";

import { listUserAnnotations } from "@/services/api/annotations";
import { listFavorites } from "@/services/api/favorites";
import { listRecords } from "@/services/api/records";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type { FavoriteResponseDto } from "@/types/api/favorites";
import type { RecordResponseDto } from "@/types/api/records";
import type { UserAnchorSegmentDto, UserAnnotationResponseDto } from "@/types/api/annotations";

export type LearningAssetsBffStatus =
  | "ready"
  | "unauthenticated"
  | "mock_session"
  | "upstream_unavailable"
  | "upstream_error";

export interface LearningAssetItemVm {
  key: string;
  recordId: string;
  sentenceId: string | null;
  anchorType: "sentence" | "text_range" | "multi_text";
  text: string;
  translation: string | null;
  note: string | null;
  color: string | null;
  isFavorited: boolean;
  isHighlighted: boolean;
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
}

export interface LearningAssetArticleVm {
  recordId: string;
  title: string;
  subtitle: string | null;
  updatedAt: string;
  items: LearningAssetItemVm[];
}

export interface LearningAssetsBffResult {
  status: LearningAssetsBffStatus;
  articles: LearningAssetArticleVm[];
  total: number;
  session: WebSession;
  message?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function titleFromRecord(record?: RecordResponseDto): string {
  if (record?.title?.trim()) {
    return record.title.trim();
  }
  const firstLine = record?.source_text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
  if (!firstLine) {
    return "未命名文章";
  }
  return firstLine.length > 72 ? `${firstLine.slice(0, 72)}...` : firstLine;
}

function subtitleFromRecord(record?: RecordResponseDto): string | null {
  const sourceText = record?.source_text.trim();
  if (!sourceText) {
    return null;
  }
  const normalized = sourceText.replace(/\s+/g, " ");
  return normalized.length > 96 ? `${normalized.slice(0, 96)}...` : normalized;
}

function getTranslation(record: RecordResponseDto | undefined, sentenceId: string | null): string | null {
  if (!record || !sentenceId) {
    return null;
  }
  const translations = record.render_scene_json?.translations;
  if (!Array.isArray(translations)) {
    return null;
  }
  const match = translations.find((item) => (
    isRecord(item) &&
    item.sentence_id === sentenceId &&
    typeof item.translation_zh === "string"
  ));
  return isRecord(match) ? readString(match.translation_zh) : null;
}

function getRecordKey(value?: string | null) {
  return value?.trim() || "";
}

function buildRecordMap(records: RecordResponseDto[]) {
  const map = new Map<string, RecordResponseDto>();
  records.forEach((record) => {
    map.set(record.id, record);
    if (record.client_record_id) {
      map.set(record.client_record_id, record);
    }
  });
  return map;
}

function readSegments(value: unknown): LearningAssetItemVm["segments"] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((segment) => {
      if (!segment || typeof segment !== "object" || Array.isArray(segment)) {
        return null;
      }
      const item = segment as UserAnchorSegmentDto;
      return item.sentence_id && item.selected_text
        ? {
            paragraphId: item.paragraph_id ?? null,
            sentenceId: item.sentence_id,
            selectedText: item.selected_text,
            startOffset: item.start_offset,
            endOffset: item.end_offset,
            textHash: item.text_hash,
          }
        : null;
    })
    .filter((segment): segment is LearningAssetItemVm["segments"][number] => Boolean(segment));
}

function mergeAsset(
  map: Map<string, LearningAssetItemVm>,
  patch: LearningAssetItemVm,
) {
  const current = map.get(patch.key);
  if (!current) {
    map.set(patch.key, patch);
    return;
  }
  map.set(patch.key, {
    ...current,
    sentenceId: current.sentenceId ?? patch.sentenceId,
    startOffset: current.startOffset ?? patch.startOffset,
    endOffset: current.endOffset ?? patch.endOffset,
    textHash: current.textHash ?? patch.textHash,
    segments: current.segments.length > 0 ? current.segments : patch.segments,
    text: patch.text || current.text,
    translation: patch.translation ?? current.translation,
    note: patch.note ?? current.note,
    color: patch.color ?? current.color,
    isFavorited: current.isFavorited || patch.isFavorited,
    isHighlighted: current.isHighlighted || patch.isHighlighted,
    updatedAt: new Date(patch.updatedAt).getTime() > new Date(current.updatedAt).getTime()
      ? patch.updatedAt
      : current.updatedAt,
  });
}

function assetFromFavorite(
  favorite: FavoriteResponseDto,
  recordsById: Map<string, RecordResponseDto>,
): LearningAssetItemVm | null {
  if (favorite.target_type !== "sentence" && favorite.target_type !== "text_range" && favorite.target_type !== "multi_text") {
    return null;
  }
  const payload = favorite.payload_json;
  const anchorType =
    favorite.target_type === "text_range"
      ? "text_range"
      : favorite.target_type === "multi_text"
        ? "multi_text"
        : "sentence";
  const recordId = getRecordKey(favorite.analysis_record_id) || getRecordKey(readString(payload.client_record_id));
  const segments = readSegments(payload.segments);
  const sentenceId = readString(payload.sentence_id) ?? segments[0]?.sentenceId ?? null;
  const text = readString(anchorType === "text_range" ? payload.selected_text : payload.text)
    ?? readString(payload.text)
    ?? readString(payload.selected_text);
  const displayText = text ?? (segments.length > 0 ? segments.map((segment) => segment.selectedText).join(" ... ") : null);
  if (!recordId || !displayText) {
    return null;
  }

  const record = recordsById.get(recordId);
  return {
    key: favorite.target_key,
    recordId: record?.id ?? recordId,
    sentenceId,
    anchorType,
    text: displayText,
    translation: readString(payload.translation) ?? getTranslation(record, sentenceId),
    note: readString(favorite.note),
    color: null,
    isFavorited: true,
    isHighlighted: false,
    updatedAt: favorite.updated_at,
    startOffset: anchorType === "text_range" ? readNumber(payload.start_offset) : null,
    endOffset: anchorType === "text_range" ? readNumber(payload.end_offset) : null,
    textHash: anchorType === "text_range" ? readString(payload.text_hash) : null,
    segments,
  };
}

function assetFromAnnotation(
  annotation: UserAnnotationResponseDto,
  recordsById: Map<string, RecordResponseDto>,
): LearningAssetItemVm | null {
  if (annotation.anchor_type !== "sentence" && annotation.anchor_type !== "text_range" && annotation.anchor_type !== "multi_text") {
    return null;
  }
  const recordId = getRecordKey(annotation.analysis_record_id);
  if (!recordId || !annotation.selected_text.trim()) {
    return null;
  }

  const record = recordsById.get(recordId);
  const segments = readSegments(annotation.segments);
  return {
    key: annotation.target_key,
    recordId: record?.id ?? recordId,
    sentenceId: annotation.sentence_id ?? segments[0]?.sentenceId ?? null,
    anchorType:
      annotation.anchor_type === "text_range"
        ? "text_range"
        : annotation.anchor_type === "multi_text"
          ? "multi_text"
          : "sentence",
    text: annotation.selected_text,
    translation: readString(annotation.payload_json.translation) ?? getTranslation(record, annotation.sentence_id),
    note: readString(annotation.note),
    color: annotation.color,
    isFavorited: false,
    isHighlighted: true,
    updatedAt: annotation.updated_at,
    startOffset: annotation.start_offset,
    endOffset: annotation.end_offset,
    textHash: annotation.text_hash,
    segments,
  };
}

function groupByArticle(
  assets: LearningAssetItemVm[],
  recordsById: Map<string, RecordResponseDto>,
): LearningAssetArticleVm[] {
  const groups = new Map<string, LearningAssetArticleVm>();
  assets.forEach((asset) => {
    const record = recordsById.get(asset.recordId);
    const current = groups.get(asset.recordId);
    groups.set(asset.recordId, {
      recordId: asset.recordId,
      title: current?.title ?? titleFromRecord(record),
      subtitle: current?.subtitle ?? subtitleFromRecord(record),
      updatedAt:
        current && new Date(current.updatedAt).getTime() > new Date(asset.updatedAt).getTime()
          ? current.updatedAt
          : asset.updatedAt,
      items: [...(current?.items ?? []), asset],
    });
  });

  return Array.from(groups.values())
    .map((article) => ({
      ...article,
      items: article.items.sort((a, b) => {
        if (a.sentenceId && b.sentenceId && a.sentenceId !== b.sentenceId) {
          return a.sentenceId.localeCompare(b.sentenceId, undefined, { numeric: true });
        }
        return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
      }),
    }))
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
}

function emptyResult(
  session: WebSession,
  status: LearningAssetsBffStatus,
  message?: string,
): LearningAssetsBffResult {
  return {
    status,
    articles: [],
    total: 0,
    session,
    message,
  };
}

export async function getLearningAssets(): Promise<LearningAssetsBffResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return emptyResult(
      session,
      session.kind === "mock_phone" ? "mock_session" : "unauthenticated",
      session.kind === "mock_phone"
        ? "当前登录态不能访问真实学习资产，请使用真实登录会话。"
        : "当前会话已过期，请重新登录。",
    );
  }

  const [recordsResult, favoritesResult, annotationsResult] = await Promise.all([
    listRecords(session.sessionToken, { limit: 100, includeRenderScene: true }),
    listFavorites(session.sessionToken),
    listUserAnnotations(session.sessionToken, { limit: 200, offset: 0 }),
  ]);

  const failed = [recordsResult, favoritesResult, annotationsResult].find((item) => !item.ok);
  if (failed && !failed.ok) {
    const unavailable = failed.status === 0 || failed.status >= 500;
    return emptyResult(
      session,
      unavailable ? "upstream_unavailable" : "upstream_error",
      unavailable ? "学习资产服务暂时不可用，请稍后重试。" : failed.message,
    );
  }

  if (!recordsResult.ok || !favoritesResult.ok || !annotationsResult.ok) {
    return emptyResult(session, "upstream_error", "学习资产加载失败。");
  }

  const recordsById = buildRecordMap(recordsResult.data.items);
  const assets = new Map<string, LearningAssetItemVm>();

  favoritesResult.data.items.forEach((favorite) => {
    const item = assetFromFavorite(favorite, recordsById);
    if (item) {
      mergeAsset(assets, item);
    }
  });

  annotationsResult.data.items.forEach((annotation) => {
    const item = assetFromAnnotation(annotation, recordsById);
    if (item) {
      mergeAsset(assets, item);
    }
  });

  const items = Array.from(assets.values());
  return {
    status: "ready",
    articles: groupByArticle(items, recordsById),
    total: items.length,
    session,
  };
}
