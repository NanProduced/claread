import "server-only";

import { readReadingDefaultsFromSettings } from "@/lib/reading-defaults";
import type { SettingsDialogData } from "@/lib/settings-dialog-data";
import { getUpstreamSessionMe, patchUpstreamProfile } from "@/services/api/auth";
import { getUpstreamQuota } from "@/services/api/quota";
import { getWebSession, projectSession, type WebSession } from "@/services/bff/session";
import type { SessionInfoResponseDto } from "@/types/api/auth";
import type { QuotaResponseDto } from "@/types/api/quota";
import type { QuotaVm } from "@/types/view/QuotaVm";

export type ProfileBffStatus =
  | "ready"
  | "unauthenticated"
  | "limited_debug"
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
    status: session.kind === "mock_phone" ? "limited_debug" : "unauthenticated",
    session: projectSession(session),
    profile: null,
    quota: null,
    message:
      session.kind === "mock_phone"
        ? "当前登录态未连接真实账户，请使用真实登录会话后查看账户和额度。"
        : "当前会话已过期，请重新登录。",
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

export interface UpdateNicknameBffResult {
  ok: boolean;
  httpStatus: number;
  message?: string;
}

export async function updateProfileNickname(
  nickname: string,
): Promise<UpdateNicknameBffResult> {
  const webSession = await getWebSession();

  if (webSession.kind === "anonymous" || webSession.kind === "mock_phone") {
    return {
      ok: false,
      httpStatus: 401,
      message: "当前会话无法修改资料，请重新登录。",
    };
  }

  const trimmed = nickname.trim();
  if (!trimmed || trimmed.length > 50) {
    return {
      ok: false,
      httpStatus: 400,
      message: trimmed ? "昵称不能超过 50 个字符。" : "昵称不能为空。",
    };
  }

  const result = await patchUpstreamProfile(webSession.sessionToken, {
    nickname: trimmed,
  });

  if (!result.ok) {
    return {
      ok: false,
      httpStatus: result.status === 0 ? 503 : result.status,
      message:
        result.status === 0 || result.status >= 500
          ? "服务暂时不可用，请稍后重试。"
          : result.message,
    };
  }

  return { ok: true, httpStatus: 200 };
}

export interface CloudSettingsBffResult {
  ok: boolean;
  httpStatus: number;
  settings: Record<string, unknown> | null;
  message?: string;
}

export async function getCloudSettings(): Promise<CloudSettingsBffResult> {
  const webSession = await getWebSession();

  if (webSession.kind === "anonymous" || webSession.kind === "mock_phone") {
    return { ok: false, httpStatus: 401, settings: null };
  }

  const result = await getUpstreamSessionMe(webSession.sessionToken);

  if (!result.ok) {
    return {
      ok: false,
      httpStatus: result.status === 0 ? 503 : result.status,
      settings: null,
      message: result.status === 0 || result.status >= 500
        ? "服务暂时不可用。"
        : result.message,
    };
  }

  return {
    ok: true,
    httpStatus: 200,
    settings: (result.data.settings as Record<string, unknown>) ?? {},
  };
}

export async function updateProfileSettings(
  settings: Record<string, unknown>,
): Promise<UpdateNicknameBffResult> {
  const webSession = await getWebSession();

  if (webSession.kind === "anonymous" || webSession.kind === "mock_phone") {
    return {
      ok: false,
      httpStatus: 401,
      message: "当前会话无法同步偏好，请重新登录。",
    };
  }

  if (!settings || typeof settings !== "object") {
    return {
      ok: false,
      httpStatus: 400,
      message: "settings 格式无效。",
    };
  }

  const result = await patchUpstreamProfile(webSession.sessionToken, { settings });

  if (!result.ok) {
    return {
      ok: false,
      httpStatus: result.status === 0 ? 503 : result.status,
      message:
        result.status === 0 || result.status >= 500
          ? "服务暂时不可用，请稍后重试。"
          : result.message,
    };
  }

  return { ok: true, httpStatus: 200 };
}

/**
 * Result of projecting the minimal Settings Dialog data.
 *
 * Strict discriminated union:
 *   - `ok: true`  → `httpStatus` is `200` and `data` is a non-null
 *                    `SettingsDialogData`. There is no `message` arm.
 *   - `ok: false` → `data` is `null` and `message` is a required,
 *                    user-facing Chinese string. Raw upstream error
 *                    details, tokens, or response bodies are never
 *                    surfaced.
 *
 * The success arm carries `data: SettingsDialogData` (not
 * `SettingsDialogData | null`), so route handlers and other consumers
 * can branch on `result.ok` and access `result.data` without any
 * nullable tolerance at the type level.
 */
export type SettingsDialogProjectionResult =
  | { ok: true; httpStatus: 200; data: SettingsDialogData }
  | { ok: false; httpStatus: number; data: null; message: string };

/**
 * Lazy, narrow BFF projection for the AppShell Settings Dialog.
 *
 * Returns the minimal DTO consumed by the Settings Dialog content
 * components (`accountData` + `preferencesData`). It reuses the same
 * session / `getUpstreamSessionMe` upstream path and the same status
 * semantics as `getProfileSettings`, but deliberately does NOT call
 * `getUpstreamQuota` — the Settings Dialog must not issue a quota
 * request just to render the "用量与积分" placeholder.
 *
 * Behavior is aligned with `loadSettingsData()`:
 *   - anonymous / mock_phone → 401 with safe message, no upstream call;
 *   - missing nickname → falls back to session phone, then "Web User";
 *   - missing settings → falls back to default reading defaults;
 *   - `canEdit` is `true` only when status is "ready".
 *
 * Upstream failure responses never echo the raw upstream message; a
 * fixed Chinese fallback is used to avoid leaking internal details.
 */
export async function getSettingsDialogProjection(): Promise<SettingsDialogProjectionResult> {
  const webSession = await getWebSession();

  if (webSession.kind === "anonymous" || webSession.kind === "mock_phone") {
    return {
      ok: false,
      httpStatus: 401,
      data: null,
      message:
        webSession.kind === "mock_phone"
          ? "当前登录态未连接真实账户，请使用真实登录会话后查看账户。"
          : "当前会话已过期，请重新登录。",
    };
  }

  const sessionResult = await getUpstreamSessionMe(webSession.sessionToken);

  if (!sessionResult.ok) {
    const isUnavailable = sessionResult.status === 0 || sessionResult.status >= 500;
    return {
      ok: false,
      httpStatus: isUnavailable ? 503 : sessionResult.status,
      data: null,
      message: isUnavailable
        ? "服务暂时不可用，请稍后重试。"
        : "账户信息暂时不可用。",
    };
  }

  const profile = projectProfile(sessionResult.data);
  const status: ProfileBffStatus = "ready";

  const sessionPhone = "phone" in webSession ? webSession.phone : undefined;
  const displayName = profile.nickname || sessionPhone || "Web User";
  const realNickname = profile.nickname || "";
  const avatarText = displayName.trim().slice(0, 1).toUpperCase() || "U";

  const readingDefaults = readReadingDefaultsFromSettings(profile.settings);
  const canEdit = status === "ready";

  const data: SettingsDialogData = {
    accountData: {
      nickname: realNickname,
      displayFallback: displayName,
      phone: sessionPhone,
      status,
      avatarText,
    },
    preferencesData: {
      readingGoal: readingDefaults.readingGoal,
      readingVariant: readingDefaults.readingVariant,
      canEdit,
    },
  };

  return { ok: true, httpStatus: 200, data };
}
