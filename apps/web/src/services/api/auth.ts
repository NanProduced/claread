import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  LogoutResponseDto,
  ProfileUpdateRequestDto,
  ProfileUpdateResponseDto,
  SessionInfoResponseDto,
} from "@/types/api/auth";

export function logoutUpstreamSession(
  sessionToken: string,
): Promise<UpstreamResult<LogoutResponseDto>> {
  return fastApiFetch<LogoutResponseDto>("/auth/session/logout", {
    method: "POST",
    body: JSON.stringify({ session_token: sessionToken }),
  });
}

export function getUpstreamSessionMe(
  sessionToken: string,
): Promise<UpstreamResult<SessionInfoResponseDto>> {
  return fastApiFetch<SessionInfoResponseDto>("/auth/session/me", {
    sessionToken,
  });
}

export function patchUpstreamProfile(
  sessionToken: string,
  body: ProfileUpdateRequestDto,
): Promise<UpstreamResult<ProfileUpdateResponseDto>> {
  return fastApiFetch<ProfileUpdateResponseDto>("/auth/profile", {
    method: "PATCH",
    sessionToken,
    body: JSON.stringify(body),
  });
}
