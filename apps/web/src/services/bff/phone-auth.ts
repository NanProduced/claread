import "server-only";

import { cookies } from "next/headers";

import {
  logoutUpstreamSession,
  requestUpstreamPhoneCode,
  verifyUpstreamPhoneCode,
} from "@/services/api/auth";
import {
  WEB_PHONE_COOKIE,
  WEB_PHONE_CHALLENGE_COOKIE,
  WEB_SESSION_COOKIE,
} from "@/services/bff/session";

export interface PhoneAuthResult {
  ok: boolean;
  message: string;
}

export interface PhoneVerifyResult extends PhoneAuthResult {
  phone?: string;
  upstreamSessionReady?: boolean;
}

const MOCK_CODE = "888888";
const CHALLENGE_TTL_MS = 5 * 60 * 1000;
const PHONE_RE = /^1[3-9]\d{9}$/;
const PHONE_AUTH_PROVIDER = process.env.CLAREAD_PHONE_AUTH_PROVIDER ?? "fastapi";
const FASTAPI_PHONE_AUTH_PROVIDERS = new Set(["fastapi", "aliyun-dypnsapi", "aliyun_dypnsapi"]);

function isMockProviderEnabled(): boolean {
  if (PHONE_AUTH_PROVIDER !== "mock") {
    return false;
  }

  return process.env.NODE_ENV !== "production" || process.env.CLAREAD_ALLOW_MOCK_PHONE_AUTH === "true";
}

function providerUnavailableMessage(): string {
  if (FASTAPI_PHONE_AUTH_PROVIDERS.has(PHONE_AUTH_PROVIDER)) {
    return "FastAPI phone auth 当前不可用；请检查上游 API、Dypnsapi provider 或短信配置。";
  }

  return "手机号验证码 provider 尚未启用。";
}

function shouldUseFastApiPhoneAuth(): boolean {
  return FASTAPI_PHONE_AUTH_PROVIDERS.has(PHONE_AUTH_PROVIDER);
}

function normalizePhone(phone: unknown): string {
  return typeof phone === "string" ? phone.trim().replace(/\s+/g, "") : "";
}

function validateMainlandPhone(phone: string): PhoneAuthResult | null {
  if (!PHONE_RE.test(phone)) {
    return { ok: false, message: "请输入 11 位中国大陆手机号。" };
  }

  return null;
}

function encodeChallenge(phone: string): string {
  return Buffer.from(
    JSON.stringify({
      phone,
      expiresAt: Date.now() + CHALLENGE_TTL_MS,
    }),
    "utf8",
  ).toString("base64url");
}

function decodeChallenge(value: string | undefined): { phone: string; expiresAt: number } | null {
  if (!value) {
    return null;
  }

  try {
    const parsed = JSON.parse(Buffer.from(value, "base64url").toString("utf8")) as unknown;

    if (
      parsed &&
      typeof parsed === "object" &&
      typeof (parsed as { phone?: unknown }).phone === "string" &&
      typeof (parsed as { expiresAt?: unknown }).expiresAt === "number"
    ) {
      return parsed as { phone: string; expiresAt: number };
    }
  } catch {
    return null;
  }

  return null;
}

export async function requestPhoneCode(phoneInput: unknown): Promise<PhoneAuthResult> {
  const phone = normalizePhone(phoneInput);
  const validation = validateMainlandPhone(phone);

  if (validation) {
    return validation;
  }

  if (shouldUseFastApiPhoneAuth()) {
    const upstreamResult = await requestUpstreamPhoneCode(phone);

    if (!upstreamResult.ok) {
      return {
        ok: false,
        message: `${providerUnavailableMessage()} (${upstreamResult.status}: ${upstreamResult.message})`,
      };
    }

    return {
      ok: true,
      message: upstreamResult.data.message || "验证码已发送。",
    };
  }

  if (!isMockProviderEnabled()) {
    return {
      ok: false,
      message: providerUnavailableMessage(),
    };
  }

  const cookieStore = await cookies();
  cookieStore.set(WEB_PHONE_CHALLENGE_COOKIE, encodeChallenge(phone), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: CHALLENGE_TTL_MS / 1000,
  });

  return {
    ok: true,
    message: "本地调试验证码已生成，请使用 888888。",
  };
}

export async function verifyPhoneCode(
  phoneInput: unknown,
  codeInput: unknown,
): Promise<PhoneVerifyResult> {
  const phone = normalizePhone(phoneInput);
  const code = typeof codeInput === "string" ? codeInput.trim() : "";
  const validation = validateMainlandPhone(phone);

  if (validation) {
    return validation;
  }

  if (shouldUseFastApiPhoneAuth()) {
    const upstreamResult = await verifyUpstreamPhoneCode(phone, code);

    if (!upstreamResult.ok) {
      return {
        ok: false,
        message: `${providerUnavailableMessage()} (${upstreamResult.status}: ${upstreamResult.message})`,
      };
    }

    const cookieStore = await cookies();
    cookieStore.set(WEB_SESSION_COOKIE, upstreamResult.data.session_token, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 60 * 60 * 24 * 30,
    });
    cookieStore.set(WEB_PHONE_COOKIE, phone, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 60 * 60 * 24 * 30,
    });

    return {
      ok: true,
      phone,
      upstreamSessionReady: true,
      message: "手机号登录成功。",
    };
  }

  if (!isMockProviderEnabled()) {
    return {
      ok: false,
      message: providerUnavailableMessage(),
    };
  }

  const cookieStore = await cookies();
  const challenge = decodeChallenge(cookieStore.get(WEB_PHONE_CHALLENGE_COOKIE)?.value);

  if (!challenge || challenge.phone !== phone || challenge.expiresAt < Date.now()) {
    return {
      ok: false,
      message: "验证码已过期，请重新发送。",
    };
  }

  if (code !== MOCK_CODE) {
    return {
      ok: false,
      message: "验证码不正确。本地调试请使用 888888。",
    };
  }

  const debugToken = process.env.CLAREAD_WEB_DEBUG_SESSION_TOKEN;

  cookieStore.set(WEB_PHONE_COOKIE, phone, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
  });
  cookieStore.delete(WEB_PHONE_CHALLENGE_COOKIE);

  if (debugToken && process.env.NODE_ENV !== "production") {
    cookieStore.set(WEB_SESSION_COOKIE, debugToken, {
      httpOnly: true,
      sameSite: "lax",
      secure: false,
      path: "/",
      maxAge: 60 * 60 * 24 * 7,
    });
  }

  return {
    ok: true,
    phone,
    upstreamSessionReady: Boolean(debugToken),
    message: debugToken
      ? "已进入本地调试登录态，并写入 FastAPI debug session。"
      : "已进入本地调试登录态；未配置 FastAPI debug session，真实账户数据不可用。",
  };
}

export async function clearWebAuthCookies(): Promise<void> {
  const cookieStore = await cookies();
  const sessionToken = cookieStore.get(WEB_SESSION_COOKIE)?.value;

  if (sessionToken) {
    await logoutUpstreamSession(sessionToken);
  }

  cookieStore.delete(WEB_SESSION_COOKIE);
  cookieStore.delete(WEB_PHONE_COOKIE);
  cookieStore.delete(WEB_PHONE_CHALLENGE_COOKIE);
}
