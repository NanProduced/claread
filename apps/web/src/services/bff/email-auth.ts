import "server-only";

import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
  completeUpstreamEmailPasswordReset,
  loginUpstreamEmailPassword,
  registerUpstreamEmail,
  requestUpstreamEmailPasswordReset,
  startUpstreamEmailAuth,
  verifyUpstreamEmailOtp,
} from "@/services/api/email-auth";
import {
  WEB_EMAIL_CHALLENGE_COOKIE,
  WEB_EMAIL_FLOW_COOKIE_PATH,
  WEB_EMAIL_TICKET_COOKIE,
  WEB_SESSION_COOKIE,
} from "@/services/bff/session";
import type { UpstreamResult } from "@/services/api/upstream";

type FlowPurpose = "register" | "password_reset";

type ChallengeCookie = {
  v: 1;
  id: string;
  p: FlowPurpose;
  email: string;
  expiresAt: number;
  resendAt: number;
};

type TicketCookie = {
  v: 1;
  id: string;
  p: FlowPurpose;
  email: string;
  expiresAt: number;
};

type CookieOptions = {
  httpOnly: true;
  sameSite: "lax";
  secure: boolean;
  path: string;
  maxAge: number;
};

type SuccessBody =
  | { ok: true; mode: "password" }
  | { ok: true; mode: "register"; resend_after: number }
  | { ok: true; next: "set-password" | "reset" }
  | { ok: true; status: "accepted"; resend_after: number }
  | { ok: true; step: "otp"; flow: FlowPurpose; email: string; resend_after: number }
  | { ok: true; step: "set-password" | "reset"; email: string }
  | { ok: true; step: "idle" }
  | { ok: true };

type FailureBody =
  | { ok: false; message: string; code: string; retry_after: number }
  | { ok: false; message: string };

export type EmailBffResult =
  | { status: number; body: SuccessBody }
  | { status: number; body: FailureBody };

const CHALLENGE_ID_RE = /^[A-Za-z0-9_-]{32}$/;
const TICKET_RE = /^[A-Za-z0-9_-]{43}$/;
const OTP_RE = /^[0-9]{6}$/;
const RATE_LIMIT_CODES = new Set([
  "email_cooldown",
  "email_hourly_limit",
  "ip_hourly_limit",
  "auth_attempt_limit",
]);
const CHALLENGE_COOKIE_KEYS = ["v", "id", "p", "email", "expiresAt", "resendAt"] as const;
const TICKET_COOKIE_KEYS = ["v", "id", "p", "email", "expiresAt"] as const;

const MESSAGES: Record<string, string> = {
  invalid_credentials: "邮箱或密码不正确。",
  invalid_email: "请输入有效邮箱。",
  invalid_password: "密码不符合要求。",
  common: "密码过于常见，请更换。",
  compromised: "该密码曾出现在数据泄露中，请更换。",
  invalid_or_expired_code: "验证码无效或已过期。",
  ticket_invalid_or_expired: "验证已过期，请重新开始。",
  ticket_purpose_mismatch: "当前步骤不匹配，请重新开始。",
  invalid_purpose: "当前步骤不匹配，请重新开始。",
  invalid_client_ip: "请求无效，请稍后重试。",
  email_auth_unavailable: "邮箱登录暂时不可用。",
  email_delivery_rejected: "邮件发送失败，请稍后重试。",
  email_cooldown: "发送过于频繁，请稍后再试。",
  email_hourly_limit: "发送次数过多，请稍后再试。",
  ip_hourly_limit: "发送次数过多，请稍后再试。",
  auth_attempt_limit: "尝试次数过多，请稍后再试。",
  missing_challenge: "请先发送验证码。",
  missing_ticket: "请先完成邮箱验证。",
  expired_flow: "验证已过期，请重新开始。",
  malformed_request: "请求无效。",
};

const STATUS_BY_CODE: Record<string, number> = {
  invalid_credentials: 401,
  invalid_email: 422,
  invalid_password: 422,
  common: 422,
  compromised: 422,
  invalid_client_ip: 422,
  invalid_or_expired_code: 400,
  ticket_invalid_or_expired: 400,
  ticket_purpose_mismatch: 400,
  invalid_purpose: 400,
  missing_challenge: 400,
  missing_ticket: 400,
  expired_flow: 400,
  malformed_request: 400,
  email_cooldown: 429,
  email_hourly_limit: 429,
  ip_hourly_limit: 429,
  auth_attempt_limit: 429,
  email_auth_unavailable: 503,
  email_delivery_rejected: 503,
};

function logSafe(event: string, code?: string): void {
  console.info("[email-auth-bff]", event, code ?? "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isEmailField(value: unknown): value is string {
  return typeof value === "string" && value.length >= 1 && value.length <= 320;
}

function isPasswordField(value: unknown): value is string {
  return typeof value === "string" && value.length >= 1 && value.length <= 512;
}

function isOtpField(value: unknown): value is string {
  return typeof value === "string" && OTP_RE.test(value);
}

function hasExactKeys(
  record: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(record);
  return actual.length === keys.length && keys.every((key) => actual.includes(key));
}

function fail(code: string, retryAfter?: number): EmailBffResult {
  logSafe("email-auth-error", code);
  const status = STATUS_BY_CODE[code] ?? 503;
  const message = MESSAGES[code] ?? MESSAGES.email_auth_unavailable;
  if (status === 429 && retryAfter !== undefined) {
    return {
      status,
      body: { ok: false, message, code, retry_after: retryAfter },
    };
  }
  return { status, body: { ok: false, message } };
}

function unavailable(): EmailBffResult {
  return fail("email_auth_unavailable");
}

function readExactBody(
  body: unknown,
  spec: Record<string, (value: unknown) => string | null>,
): { ok: true; fields: Record<string, string> } | { ok: false; result: EmailBffResult } {
  if (!isRecord(body) || !hasExactKeys(body, Object.keys(spec))) {
    return { ok: false, result: fail("malformed_request") };
  }
  const fields: Record<string, string> = {};
  for (const [key, validate] of Object.entries(spec)) {
    const value = validate(body[key]);
    if (value === null) {
      return { ok: false, result: fail("malformed_request") };
    }
    fields[key] = value;
  }
  return { ok: true, fields };
}

function asEmail(value: unknown): string | null {
  return isEmailField(value) ? value : null;
}

function asPassword(value: unknown): string | null {
  return isPasswordField(value) ? value : null;
}

function asOtp(value: unknown): string | null {
  return isOtpField(value) ? value : null;
}

function flowCookieOptions(maxAge: number): CookieOptions {
  return {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: WEB_EMAIL_FLOW_COOKIE_PATH,
    maxAge,
  };
}

function sessionCookieOptions(maxAge: number): CookieOptions {
  return {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  };
}

function encodeCookie(payload: ChallengeCookie | TicketCookie): string {
  return Buffer.from(JSON.stringify(payload), "utf8").toString("base64url");
}

function decodeCookieJson(value: string | undefined): Record<string, unknown> | null {
  if (!value) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(Buffer.from(value, "base64url").toString("utf8"));
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function decodeChallengeCookie(value: string | undefined): ChallengeCookie | null {
  const parsed = decodeCookieJson(value);
  if (
    !parsed ||
    !hasExactKeys(parsed, CHALLENGE_COOKIE_KEYS) ||
    parsed.v !== 1 ||
    typeof parsed.id !== "string" ||
    !CHALLENGE_ID_RE.test(parsed.id) ||
    (parsed.p !== "register" && parsed.p !== "password_reset") ||
    !isEmailField(parsed.email) ||
    typeof parsed.expiresAt !== "number" ||
    !Number.isFinite(parsed.expiresAt) ||
    typeof parsed.resendAt !== "number" ||
    !Number.isFinite(parsed.resendAt) ||
    parsed.expiresAt <= Date.now()
  ) {
    return null;
  }
  return {
    v: 1,
    id: parsed.id,
    p: parsed.p,
    email: parsed.email,
    expiresAt: parsed.expiresAt,
    resendAt: parsed.resendAt,
  };
}

function decodeTicketCookie(value: string | undefined): TicketCookie | null {
  const parsed = decodeCookieJson(value);
  if (
    !parsed ||
    !hasExactKeys(parsed, TICKET_COOKIE_KEYS) ||
    parsed.v !== 1 ||
    typeof parsed.id !== "string" ||
    !TICKET_RE.test(parsed.id) ||
    (parsed.p !== "register" && parsed.p !== "password_reset") ||
    !isEmailField(parsed.email) ||
    typeof parsed.expiresAt !== "number" ||
    !Number.isFinite(parsed.expiresAt) ||
    parsed.expiresAt <= Date.now()
  ) {
    return null;
  }
  return {
    v: 1,
    id: parsed.id,
    p: parsed.p,
    email: parsed.email,
    expiresAt: parsed.expiresAt,
  };
}

async function cookieStore() {
  return cookies();
}

async function readChallengeCookie(): Promise<ChallengeCookie | null> {
  const store = await cookieStore();
  return decodeChallengeCookie(store.get(WEB_EMAIL_CHALLENGE_COOKIE)?.value);
}

async function readTicketCookie(): Promise<TicketCookie | null> {
  const store = await cookieStore();
  return decodeTicketCookie(store.get(WEB_EMAIL_TICKET_COOKIE)?.value);
}

async function clearFlowCookies(): Promise<void> {
  const store = await cookieStore();
  store.delete({ name: WEB_EMAIL_CHALLENGE_COOKIE, path: WEB_EMAIL_FLOW_COOKIE_PATH });
  store.delete({ name: WEB_EMAIL_TICKET_COOKIE, path: WEB_EMAIL_FLOW_COOKIE_PATH });
}

async function writeChallengeCookie(
  id: string,
  purpose: FlowPurpose,
  email: string,
  expiresIn: number,
  resendAfter: number,
): Promise<void> {
  await clearFlowCookies();
  const store = await cookieStore();
  const now = Date.now();
  const payload: ChallengeCookie = {
    v: 1,
    id,
    p: purpose,
    email,
    expiresAt: now + expiresIn * 1000,
    resendAt: now + resendAfter * 1000,
  };
  store.set(
    WEB_EMAIL_CHALLENGE_COOKIE,
    encodeCookie(payload),
    flowCookieOptions(expiresIn),
  );
}

async function writeTicketCookie(
  id: string,
  purpose: FlowPurpose,
  email: string,
  expiresIn: number,
): Promise<void> {
  const store = await cookieStore();
  store.delete({ name: WEB_EMAIL_CHALLENGE_COOKIE, path: WEB_EMAIL_FLOW_COOKIE_PATH });
  const payload: TicketCookie = {
    v: 1,
    id,
    p: purpose,
    email,
    expiresAt: Date.now() + expiresIn * 1000,
  };
  store.set(WEB_EMAIL_TICKET_COOKIE, encodeCookie(payload), flowCookieOptions(expiresIn));
}

function remainingResendAfter(resendAt: number): number {
  return Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
}

function parseUpstreamError<T>(result: Extract<UpstreamResult<T>, { ok: false }>): EmailBffResult {
  const envelope = isRecord(result.body)
    ? result.body
    : isRecord(result.payload)
      ? result.payload
      : null;
  const detail = envelope && isRecord(envelope.detail) ? envelope.detail : null;
  if (result.status === 429) {
    if (
      detail &&
      typeof detail.code === "string" &&
      RATE_LIMIT_CODES.has(detail.code) &&
      isPositiveInt(detail.retry_after)
    ) {
      return fail(detail.code, detail.retry_after);
    }
    return unavailable();
  }
  const code =
    detail && typeof detail.code === "string" && detail.code in MESSAGES
      ? detail.code
      : result.status === 401
        ? "invalid_credentials"
        : result.status === 422
          ? "malformed_request"
          : result.status === 400
            ? "invalid_or_expired_code"
            : "email_auth_unavailable";
  return fail(code);
}

function sessionMaxAge(expiresAt: string): number | null {
  const expires = Date.parse(expiresAt);
  if (!Number.isFinite(expires)) {
    return null;
  }
  const maxAge = Math.floor((expires - Date.now()) / 1000);
  return maxAge >= 1 ? maxAge : null;
}

async function establishSession(
  result: UpstreamResult<{ session_token: string; expires_at: string }>,
): Promise<EmailBffResult> {
  if (!result.ok) {
    return parseUpstreamError(result);
  }
  const token = result.data.session_token;
  const expiresAt = result.data.expires_at;
  if (typeof token !== "string" || token.length === 0 || typeof expiresAt !== "string") {
    return unavailable();
  }
  const maxAge = sessionMaxAge(expiresAt);
  if (maxAge === null) {
    return unavailable();
  }
  const store = await cookieStore();
  store.set(WEB_SESSION_COOKIE, token, sessionCookieOptions(maxAge));
  await clearFlowCookies();
  return { status: 200, body: { ok: true } };
}

export function emailAuthResponse(result: EmailBffResult): NextResponse {
  const headers = new Headers();
  if (!result.body.ok && "retry_after" in result.body && result.body.retry_after != null) {
    headers.set("Retry-After", String(result.body.retry_after));
  }
  return NextResponse.json(result.body, { status: result.status, headers });
}

export async function startEmailAuth(body: unknown): Promise<EmailBffResult> {
  const parsed = readExactBody(body, { email: asEmail });
  if (!parsed.ok) {
    return parsed.result;
  }
  const upstream = await startUpstreamEmailAuth(parsed.fields.email);
  if (!upstream.ok) {
    return parseUpstreamError(upstream);
  }
  if (upstream.data.mode === "password") {
    await clearFlowCookies();
    return { status: 200, body: { ok: true, mode: "password" } };
  }
  if (upstream.data.mode !== "register") {
    return unavailable();
  }
  const challengeId = upstream.data.challenge_id;
  const expiresIn = upstream.data.expires_in;
  const resendAfter = upstream.data.resend_after;
  if (
    typeof challengeId !== "string" ||
    !CHALLENGE_ID_RE.test(challengeId) ||
    !isPositiveInt(expiresIn) ||
    !isPositiveInt(resendAfter)
  ) {
    return unavailable();
  }
  await writeChallengeCookie(
    challengeId,
    "register",
    parsed.fields.email,
    expiresIn,
    resendAfter,
  );
  return { status: 200, body: { ok: true, mode: "register", resend_after: resendAfter } };
}

export async function verifyEmailOtp(body: unknown): Promise<EmailBffResult> {
  const parsed = readExactBody(body, { code: asOtp });
  if (!parsed.ok) {
    return parsed.result;
  }
  const challenge = await readChallengeCookie();
  if (!challenge) {
    return fail("missing_challenge");
  }
  const upstream = await verifyUpstreamEmailOtp(challenge.id, parsed.fields.code);
  if (!upstream.ok) {
    return parseUpstreamError(upstream);
  }
  const ticket = upstream.data.ticket;
  const expiresIn = upstream.data.expires_in;
  if (typeof ticket !== "string" || !TICKET_RE.test(ticket) || !isPositiveInt(expiresIn)) {
    return unavailable();
  }
  await writeTicketCookie(ticket, challenge.p, challenge.email, expiresIn);
  return {
    status: 200,
    body: { ok: true, next: challenge.p === "register" ? "set-password" : "reset" },
  };
}

export async function registerEmail(body: unknown): Promise<EmailBffResult> {
  const parsed = readExactBody(body, { password: asPassword });
  if (!parsed.ok) {
    return parsed.result;
  }
  const ticket = await readTicketCookie();
  if (!ticket) {
    return fail("missing_ticket");
  }
  if (ticket.p !== "register") {
    return fail("ticket_purpose_mismatch");
  }
  return establishSession(await registerUpstreamEmail(ticket.id, parsed.fields.password));
}

export async function loginEmailPassword(body: unknown): Promise<EmailBffResult> {
  const parsed = readExactBody(body, { email: asEmail, password: asPassword });
  if (!parsed.ok) {
    return parsed.result;
  }
  return establishSession(
    await loginUpstreamEmailPassword(parsed.fields.email, parsed.fields.password),
  );
}

export async function requestEmailPasswordReset(body: unknown): Promise<EmailBffResult> {
  const parsed = readExactBody(body, { email: asEmail });
  if (!parsed.ok) {
    return parsed.result;
  }
  const upstream = await requestUpstreamEmailPasswordReset(parsed.fields.email);
  if (!upstream.ok) {
    return parseUpstreamError(upstream);
  }
  if (upstream.data.status !== "accepted") {
    return unavailable();
  }
  const challengeId = upstream.data.challenge_id;
  const expiresIn = upstream.data.expires_in;
  const resendAfter = upstream.data.resend_after;
  if (
    typeof challengeId !== "string" ||
    !CHALLENGE_ID_RE.test(challengeId) ||
    !isPositiveInt(expiresIn) ||
    !isPositiveInt(resendAfter)
  ) {
    return unavailable();
  }
  await writeChallengeCookie(
    challengeId,
    "password_reset",
    parsed.fields.email,
    expiresIn,
    resendAfter,
  );
  return { status: 200, body: { ok: true, status: "accepted", resend_after: resendAfter } };
}

export async function completeEmailPasswordReset(body: unknown): Promise<EmailBffResult> {
  const parsed = readExactBody(body, { password: asPassword });
  if (!parsed.ok) {
    return parsed.result;
  }
  const ticket = await readTicketCookie();
  if (!ticket) {
    return fail("missing_ticket");
  }
  if (ticket.p !== "password_reset") {
    return fail("ticket_purpose_mismatch");
  }
  return establishSession(
    await completeUpstreamEmailPasswordReset(ticket.id, parsed.fields.password),
  );
}

export async function getEmailAuthFlowStatus(): Promise<EmailBffResult> {
  const challenge = await readChallengeCookie();
  if (challenge) {
    return {
      status: 200,
      body: {
        ok: true,
        step: "otp",
        flow: challenge.p,
        email: challenge.email,
        resend_after: remainingResendAfter(challenge.resendAt),
      },
    };
  }
  const ticket = await readTicketCookie();
  if (ticket) {
    return {
      status: 200,
      body: {
        ok: true,
        step: ticket.p === "register" ? "set-password" : "reset",
        email: ticket.email,
      },
    };
  }
  return { status: 200, body: { ok: true, step: "idle" } };
}

export async function cancelEmailAuthFlow(): Promise<EmailBffResult> {
  await clearFlowCookies();
  return { status: 200, body: { ok: true } };
}
