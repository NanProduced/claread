import "server-only";

import { getUpstreamSessionMe } from "@/services/api/auth";
import { getUpstreamQuota } from "@/services/api/quota";
import { getWebSession, projectSession, type WebSession } from "@/services/bff/session";
import type { SessionInfoResponseDto } from "@/types/api/auth";
import type { QuotaResponseDto } from "@/types/api/quota";
import type { QuotaVm } from "@/types/view/QuotaVm";

export type ProfileBffStatus =
  | "ready"
  | "unauthenticated"
  | "mock_session"
  | "upstream_unavailable"
  | "upstream_error";

export interface ProfileVm {
  userId: string;
  sessionId: string;
  nickname: string;
  avatarUrl: string;
  cumulativeArticleCount: number;
  settings: Record<string, unknown>;
}

export interface ProfileSettingsVm {
  status: ProfileBffStatus;
  session: ReturnType<typeof projectSession>;
  profile: ProfileVm | null;
  quota: QuotaVm | null;
  message?: string;
}

function upstreamStatus(status: number): ProfileBffStatus {
  return status === 0 ? "upstream_unavailable" : "upstream_error";
}

function projectProfile(dto: SessionInfoResponseDto): ProfileVm {
  return {
    userId: dto.user_id,
    sessionId: dto.session_id,
    nickname: dto.nickname,
    avatarUrl: dto.avatar_url,
    cumulativeArticleCount: dto.cumulative_article_count,
    settings: dto.settings,
  };
}

function projectQuota(dto: QuotaResponseDto, userId: string): QuotaVm {
  return {
    profileId: userId,
    quotaUsed: dto.daily_used_points,
    quotaLimit: dto.daily_free_points,
    quotaType: "daily",
    dailyFreePoints: dto.daily_free_points,
    dailyUsedPoints: dto.daily_used_points,
    bonusPoints: dto.bonus_points,
    remainingPoints: dto.remaining_points,
    unit: "points",
  };
}

function unauthenticatedResult(session: WebSession): ProfileSettingsVm {
  return {
    status: session.kind === "mock_phone" ? "mock_session" : "unauthenticated",
    session: projectSession(session),
    profile: null,
    quota: null,
    message:
      session.kind === "mock_phone"
        ? "当前登录态未连接真实账户，请使用真实登录会话后查看账户和额度。"
        : "请先登录后查看账户和额度。",
  };
}

export async function getProfileSettings(): Promise<ProfileSettingsVm> {
  const webSession = await getWebSession();

  if (webSession.kind === "anonymous" || webSession.kind === "mock_phone") {
    return unauthenticatedResult(webSession);
  }

  const [sessionResult, quotaResult] = await Promise.all([
    getUpstreamSessionMe(webSession.sessionToken),
    getUpstreamQuota(webSession.sessionToken),
  ]);

  if (!sessionResult.ok) {
    return {
      status: upstreamStatus(sessionResult.status),
      session: projectSession(webSession),
      profile: null,
      quota: null,
      message: `FastAPI session/me failed (${sessionResult.status}): ${sessionResult.message}`,
    };
  }

  const profile = projectProfile(sessionResult.data);

  if (!quotaResult.ok) {
    return {
      status: upstreamStatus(quotaResult.status),
      session: projectSession(webSession),
      profile,
      quota: null,
      message: `FastAPI me/quota failed (${quotaResult.status}): ${quotaResult.message}`,
    };
  }

  return {
    status: "ready",
    session: projectSession(webSession),
    profile,
    quota: projectQuota(quotaResult.data, sessionResult.data.user_id),
  };
}
