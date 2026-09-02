import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  EmailOtpVerifyResponseDto,
  EmailPasswordResetResponseDto,
  EmailSessionResponseDto,
  EmailStartResponseDto,
} from "@/types/api/email-auth";

export function startUpstreamEmailAuth(
  email: string,
): Promise<UpstreamResult<EmailStartResponseDto>> {
  return fastApiFetch<EmailStartResponseDto>("/auth/email/start", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function verifyUpstreamEmailOtp(
  challengeId: string,
  code: string,
): Promise<UpstreamResult<EmailOtpVerifyResponseDto>> {
  return fastApiFetch<EmailOtpVerifyResponseDto>("/auth/email/otp/verify", {
    method: "POST",
    body: JSON.stringify({ challenge_id: challengeId, code }),
  });
}

export function registerUpstreamEmail(
  ticket: string,
  password: string,
): Promise<UpstreamResult<EmailSessionResponseDto>> {
  return fastApiFetch<EmailSessionResponseDto>("/auth/email/register", {
    method: "POST",
    body: JSON.stringify({ ticket, password }),
  });
}

export function loginUpstreamEmailPassword(
  email: string,
  password: string,
): Promise<UpstreamResult<EmailSessionResponseDto>> {
  return fastApiFetch<EmailSessionResponseDto>("/auth/email/password/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function requestUpstreamEmailPasswordReset(
  email: string,
): Promise<UpstreamResult<EmailPasswordResetResponseDto>> {
  return fastApiFetch<EmailPasswordResetResponseDto>("/auth/email/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function completeUpstreamEmailPasswordReset(
  ticket: string,
  password: string,
): Promise<UpstreamResult<EmailSessionResponseDto>> {
  return fastApiFetch<EmailSessionResponseDto>("/auth/email/password-reset/complete", {
    method: "POST",
    body: JSON.stringify({ ticket, password }),
  });
}
