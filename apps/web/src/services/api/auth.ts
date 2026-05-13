import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  LogoutResponseDto,
  PhoneCodeResponseDto,
  PhoneVerifyResponseDto,
} from "@/types/api/auth";

export function requestUpstreamPhoneCode(
  phone: string,
): Promise<UpstreamResult<PhoneCodeResponseDto>> {
  return fastApiFetch<PhoneCodeResponseDto>("/auth/phone/request-code", {
    method: "POST",
    body: JSON.stringify({ phone }),
  });
}

export function verifyUpstreamPhoneCode(
  phone: string,
  code: string,
): Promise<UpstreamResult<PhoneVerifyResponseDto>> {
  return fastApiFetch<PhoneVerifyResponseDto>("/auth/phone/verify-code", {
    method: "POST",
    body: JSON.stringify({ phone, code }),
  });
}

export function logoutUpstreamSession(
  sessionToken: string,
): Promise<UpstreamResult<LogoutResponseDto>> {
  return fastApiFetch<LogoutResponseDto>("/auth/session/logout", {
    method: "POST",
    body: JSON.stringify({ session_token: sessionToken }),
  });
}
