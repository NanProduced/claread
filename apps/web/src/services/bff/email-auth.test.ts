import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

type StoredCookie = {
  value: string;
  options?: Record<string, unknown>;
};

const cookieJar = new Map<string, StoredCookie>();
const logs: unknown[] = [];

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => {
      const stored = cookieJar.get(name);
      return stored ? { name, value: stored.value } : undefined;
    },
    set: (name: string, value: string, options?: Record<string, unknown>) => {
      cookieJar.set(name, { value, options });
    },
    delete: (input: string | { name: string }) => {
      cookieJar.delete(typeof input === "string" ? input : input.name);
    },
  }),
}));

const startUpstreamEmailAuth = vi.fn();
const verifyUpstreamEmailOtp = vi.fn();
const registerUpstreamEmail = vi.fn();
const loginUpstreamEmailPassword = vi.fn();
const requestUpstreamEmailPasswordReset = vi.fn();
const completeUpstreamEmailPasswordReset = vi.fn();

vi.mock("@/services/api/email-auth", () => ({
  startUpstreamEmailAuth: (...args: unknown[]) => startUpstreamEmailAuth(...args),
  verifyUpstreamEmailOtp: (...args: unknown[]) => verifyUpstreamEmailOtp(...args),
  registerUpstreamEmail: (...args: unknown[]) => registerUpstreamEmail(...args),
  loginUpstreamEmailPassword: (...args: unknown[]) => loginUpstreamEmailPassword(...args),
  requestUpstreamEmailPasswordReset: (...args: unknown[]) =>
    requestUpstreamEmailPasswordReset(...args),
  completeUpstreamEmailPasswordReset: (...args: unknown[]) =>
    completeUpstreamEmailPasswordReset(...args),
}));

import {
  WEB_EMAIL_CHALLENGE_COOKIE,
  WEB_EMAIL_FLOW_COOKIE_PATH,
  WEB_EMAIL_TICKET_COOKIE,
  WEB_SESSION_COOKIE,
} from "./session";
import {
  cancelEmailAuthFlow,
  completeEmailPasswordReset,
  emailAuthResponse,
  getEmailAuthFlowStatus,
  loginEmailPassword,
  registerEmail,
  requestEmailPasswordReset,
  startEmailAuth,
  verifyEmailOtp,
} from "./email-auth";

const EMAIL = "User@Example.COM";
const PASSWORD = "correct horse battery staple";
const CODE = "123456";
const CHALLENGE_ID = "C".repeat(32);
const TICKET = "T".repeat(43);
const SESSION_TOKEN = "session-token-secret-value";
const RESEND_AFTER = 73;
const EXPIRES_IN = 600;
const FIXED_NOW = new Date("2026-09-02T00:00:00.000Z");
const EXPIRES_AT = "2026-09-03T00:00:00.000Z";

const AUTH_SECRETS = [CHALLENGE_ID, TICKET, SESSION_TOKEN, PASSWORD];

function assertNoSecrets(value: unknown): void {
  const dumped = JSON.stringify(value);
  for (const secret of AUTH_SECRETS) {
    expect(dumped).not.toContain(secret);
  }
  expect(dumped).not.toMatch(/challenge_id|session_token|"ticket"/);
}

function assertNoEmailInLogs(): void {
  expect(JSON.stringify(logs)).not.toContain(EMAIL);
}

function errorBody(status: number, code: string, retryAfter?: number) {
  return {
    ok: false as const,
    status,
    message: "upstream",
    payload: { detail: { code, retry_after: retryAfter } },
    body: { detail: { code, retry_after: retryAfter } },
  };
}

describe("email auth BFF", () => {
  beforeEach(() => {
    cookieJar.clear();
    logs.length = 0;
    vi.restoreAllMocks();
    vi.spyOn(console, "info").mockImplementation((...args: unknown[]) => {
      logs.push(args);
    });
    vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
      logs.push(args);
    });
    vi.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
      logs.push(args);
    });
    startUpstreamEmailAuth.mockReset();
    verifyUpstreamEmailOtp.mockReset();
    registerUpstreamEmail.mockReset();
    loginUpstreamEmailPassword.mockReset();
    requestUpstreamEmailPasswordReset.mockReset();
    completeUpstreamEmailPasswordReset.mockReset();
    vi.useFakeTimers();
    vi.setSystemTime(FIXED_NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
  });

  it("start never reveals account state or a mode", async () => {
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: { challenge_id: CHALLENGE_ID, expires_in: EXPIRES_IN, resend_after: RESEND_AFTER },
    });

    const result = await startEmailAuth({ email: EMAIL });

    expect(result.status).toBe(200);
    expect(result.body).toEqual({ ok: true, resend_after: RESEND_AFTER });
    expect(result.body).not.toHaveProperty("mode");
    expect(cookieJar.has(WEB_EMAIL_CHALLENGE_COOKIE)).toBe(true);
    assertNoSecrets(result.body);
    expect(startUpstreamEmailAuth).toHaveBeenCalledWith(EMAIL);
  });

  it("start register stores challenge cookie and returns configured resend_after", async () => {
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });

    const result = await startEmailAuth({ email: EMAIL });

    expect(result.status).toBe(200);
    expect(result.body).toEqual({
      ok: true,
      resend_after: RESEND_AFTER,
    });
    const stored = cookieJar.get(WEB_EMAIL_CHALLENGE_COOKIE);
    expect(stored?.value).toBeTruthy();
    expect(stored?.options).toMatchObject({
      httpOnly: true,
      sameSite: "lax",
      secure: false,
      path: WEB_EMAIL_FLOW_COOKIE_PATH,
      maxAge: EXPIRES_IN,
    });
    assertNoSecrets(result.body);
  });

  it("OTP verify reads challenge from cookie and returns next set-password", async () => {
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    verifyUpstreamEmailOtp.mockResolvedValue({
      ok: true,
      data: { ticket: TICKET, expires_in: 900, purpose: "register" },
    });
    await startEmailAuth({ email: EMAIL });

    const result = await verifyEmailOtp({ code: CODE });

    expect(verifyUpstreamEmailOtp).toHaveBeenCalledWith(CHALLENGE_ID, CODE);
    expect(result.body).toEqual({ ok: true, next: "set-password" });
    expect(result.body).not.toHaveProperty("purpose");
    expect(cookieJar.has(WEB_EMAIL_CHALLENGE_COOKIE)).toBe(false);
    expect(cookieJar.get(WEB_EMAIL_TICKET_COOKIE)?.options).toMatchObject({
      httpOnly: true,
      sameSite: "lax",
      path: WEB_EMAIL_FLOW_COOKIE_PATH,
      maxAge: 900,
    });
    assertNoSecrets(result.body);
  });

  it("OTP verify reflects converted upstream purpose without exposing it to the browser", async () => {
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    verifyUpstreamEmailOtp.mockResolvedValue({
      ok: true,
      data: { ticket: TICKET, expires_in: 900, purpose: "password_reset" },
    });
    await startEmailAuth({ email: EMAIL });

    const result = await verifyEmailOtp({ code: CODE });

    expect(result.body).toEqual({ ok: true, next: "reset" });
    expect(result.body).not.toHaveProperty("purpose");
    expect(cookieJar.get(WEB_EMAIL_TICKET_COOKIE)?.value).toBeTruthy();
    expect((await getEmailAuthFlowStatus()).body).toEqual({
      ok: true,
      step: "reset",
      email: EMAIL,
    });
    assertNoSecrets(result.body);
  });

  it("flow-status keeps register contract uniform before verification", async () => {
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    await startEmailAuth({ email: EMAIL });

    const status = await getEmailAuthFlowStatus();

    expect(status.body).toEqual({
      ok: true,
      step: "otp",
      flow: "register",
      email: EMAIL,
      resend_after: RESEND_AFTER,
    });
    expect(JSON.stringify(status.body)).not.toContain("password_reset");
    assertNoSecrets(status.body);
  });

  it("register, password login and reset complete write session cookie only", async () => {
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    verifyUpstreamEmailOtp.mockResolvedValue({
      ok: true,
      data: { ticket: TICKET, expires_in: 900, purpose: "register" },
    });
    registerUpstreamEmail.mockResolvedValue({
      ok: true,
      data: { session_token: SESSION_TOKEN, expires_at: EXPIRES_AT },
    });
    await startEmailAuth({ email: EMAIL });
    await verifyEmailOtp({ code: CODE });

    const registered = await registerEmail({ password: PASSWORD });
    expect(registered.body).toEqual({ ok: true });
    expect(registerUpstreamEmail).toHaveBeenCalledWith(TICKET, PASSWORD);
    expect(cookieJar.get(WEB_SESSION_COOKIE)?.value).toBe(SESSION_TOKEN);
    expect(cookieJar.get(WEB_SESSION_COOKIE)?.options).toMatchObject({
      httpOnly: true,
      sameSite: "lax",
      path: "/",
      maxAge: 86400,
    });
    expect(cookieJar.has(WEB_EMAIL_CHALLENGE_COOKIE)).toBe(false);
    expect(cookieJar.has(WEB_EMAIL_TICKET_COOKIE)).toBe(false);
    assertNoSecrets(registered.body);

    cookieJar.clear();
    loginUpstreamEmailPassword.mockResolvedValue({
      ok: true,
      data: { session_token: SESSION_TOKEN, expires_at: EXPIRES_AT },
    });
    const loggedIn = await loginEmailPassword({ email: EMAIL, password: PASSWORD });
    expect(loggedIn.body).toEqual({ ok: true });
    expect(cookieJar.get(WEB_SESSION_COOKIE)?.value).toBe(SESSION_TOKEN);
    assertNoSecrets(loggedIn.body);

    cookieJar.clear();
    requestUpstreamEmailPasswordReset.mockResolvedValue({
      ok: true,
      data: {
        status: "accepted",
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    verifyUpstreamEmailOtp.mockResolvedValue({
      ok: true,
      data: { ticket: TICKET, expires_in: 900, purpose: "password_reset" },
    });
    completeUpstreamEmailPasswordReset.mockResolvedValue({
      ok: true,
      data: { session_token: SESSION_TOKEN, expires_at: EXPIRES_AT },
    });
    await requestEmailPasswordReset({ email: EMAIL });
    const otp = await verifyEmailOtp({ code: CODE });
    expect(otp.body).toEqual({ ok: true, next: "reset" });
    expect((await getEmailAuthFlowStatus()).body).toEqual({
      ok: true,
      step: "reset",
      email: EMAIL,
    });
    const reset = await completeEmailPasswordReset({ password: PASSWORD });
    expect(reset.body).toEqual({ ok: true });
    expect(completeUpstreamEmailPasswordReset).toHaveBeenCalledWith(TICKET, PASSWORD);
    expect(cookieJar.has(WEB_EMAIL_TICKET_COOKIE)).toBe(false);
    assertNoSecrets(reset.body);
  });

  it("reset request stays generic accepted and propagates resend_after", async () => {
    requestUpstreamEmailPasswordReset.mockResolvedValue({
      ok: true,
      data: {
        status: "accepted",
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });

    const result = await requestEmailPasswordReset({ email: EMAIL });

    expect(result.body).toEqual({
      ok: true,
      status: "accepted",
      resend_after: RESEND_AFTER,
    });
    expect(cookieJar.has(WEB_EMAIL_CHALLENGE_COOKIE)).toBe(true);
    assertNoSecrets(result.body);
  });

  it("maps 400/401/422/429/503 without leaking upstream payloads", async () => {
    startUpstreamEmailAuth.mockResolvedValue(errorBody(422, "invalid_email"));
    const invalidEmail = await startEmailAuth({ email: EMAIL });
    expect(invalidEmail.status).toBe(422);
    expect(invalidEmail.body.ok).toBe(false);
    if (invalidEmail.body.ok) {
      throw new Error("expected failure arm");
    }
    expect("code" in invalidEmail.body).toBe(false);
    expect(invalidEmail.body.message).toBe("请输入有效邮箱。");
    assertNoSecrets(invalidEmail.body);

    loginUpstreamEmailPassword.mockResolvedValue(errorBody(401, "invalid_credentials"));
    const unauthorized = await loginEmailPassword({ email: EMAIL, password: PASSWORD });
    expect(unauthorized.status).toBe(401);
    expect(unauthorized.body.ok).toBe(false);
    if (unauthorized.body.ok) {
      throw new Error("expected failure arm");
    }
    expect(unauthorized.body.message).toBe("邮箱或密码不正确。");

    verifyUpstreamEmailOtp.mockResolvedValue(errorBody(400, "invalid_or_expired_code"));
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    await startEmailAuth({ email: EMAIL });
    const badCode = await verifyEmailOtp({ code: CODE });
    expect(badCode.status).toBe(400);

    startUpstreamEmailAuth.mockResolvedValue(
      errorBody(429, "email_cooldown", 23),
    );
    const limited = await startEmailAuth({ email: EMAIL });
    expect(limited.status).toBe(429);
    expect(limited.body).toMatchObject({
      ok: false,
      code: "email_cooldown",
      retry_after: 23,
    });
    const response = emailAuthResponse(limited);
    expect(response.headers.get("Retry-After")).toBe("23");
    assertNoSecrets(limited.body);

    startUpstreamEmailAuth.mockResolvedValue(errorBody(503, "email_auth_unavailable"));
    const unavailable = await startEmailAuth({ email: EMAIL });
    expect(unavailable.status).toBe(503);
    expect(unavailable.body.ok).toBe(false);
    if (unavailable.body.ok) {
      throw new Error("expected failure arm");
    }
    expect(unavailable.body.message).toBe("邮箱登录暂时不可用。");
  });

  it("fail-closes malformed success payloads", async () => {
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: { challenge_id: CHALLENGE_ID, expires_in: EXPIRES_IN },
    });
    const missingCooldown = await startEmailAuth({ email: EMAIL });
    expect(missingCooldown.status).toBe(503);
    expect(cookieJar.has(WEB_EMAIL_CHALLENGE_COOKIE)).toBe(false);

    loginUpstreamEmailPassword.mockResolvedValue({
      ok: true,
      data: { session_token: SESSION_TOKEN, expires_at: "not-a-date" },
    });
    const badExpiry = await loginEmailPassword({ email: EMAIL, password: PASSWORD });
    expect(badExpiry.status).toBe(503);
    expect(cookieJar.has(WEB_SESSION_COOKIE)).toBe(false);

    loginUpstreamEmailPassword.mockResolvedValue({
      ok: true,
      data: { session_token: SESSION_TOKEN, expires_at: "2020-01-01T00:00:00.000Z" },
    });
    const pastExpiry = await loginEmailPassword({ email: EMAIL, password: PASSWORD });
    expect(pastExpiry.status).toBe(503);
  });

  it("rejects missing challenge, expired cookie and wrong purpose", async () => {
    const missing = await verifyEmailOtp({ code: CODE });
    expect(missing.status).toBe(400);
    expect(verifyUpstreamEmailOtp).not.toHaveBeenCalled();

    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    await startEmailAuth({ email: EMAIL });
    vi.advanceTimersByTime((EXPIRES_IN + 1) * 1000);
    const expired = await verifyEmailOtp({ code: CODE });
    expect(expired.status).toBe(400);
    expect(verifyUpstreamEmailOtp).not.toHaveBeenCalled();

    vi.setSystemTime(FIXED_NOW);
    cookieJar.clear();
    await startEmailAuth({ email: EMAIL });
    verifyUpstreamEmailOtp.mockResolvedValue({
      ok: true,
      data: { ticket: TICKET, expires_in: 900, purpose: "register" },
    });
    await verifyEmailOtp({ code: CODE });
    const wrongPurpose = await completeEmailPasswordReset({ password: PASSWORD });
    expect(wrongPurpose.status).toBe(400);
    expect(completeUpstreamEmailPasswordReset).not.toHaveBeenCalled();
  });

  it("restores flow-status and cancel clears temp cookies", async () => {
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    await startEmailAuth({ email: EMAIL });
    expect((await getEmailAuthFlowStatus()).body).toEqual({
      ok: true,
      step: "otp",
      flow: "register",
      email: EMAIL,
      resend_after: RESEND_AFTER,
    });

    vi.advanceTimersByTime(10_000);
    expect((await getEmailAuthFlowStatus()).body).toEqual({
      ok: true,
      step: "otp",
      flow: "register",
      email: EMAIL,
      resend_after: 63,
    });

    verifyUpstreamEmailOtp.mockResolvedValue({
      ok: true,
      data: { ticket: TICKET, expires_in: 900, purpose: "register" },
    });
    await verifyEmailOtp({ code: CODE });
    expect((await getEmailAuthFlowStatus()).body).toEqual({
      ok: true,
      step: "set-password",
      email: EMAIL,
    });

    const canceled = await cancelEmailAuthFlow();
    expect(canceled.body).toEqual({ ok: true });
    expect((await getEmailAuthFlowStatus()).body).toEqual({ ok: true, step: "idle" });
    expect(cookieJar.has(WEB_EMAIL_CHALLENGE_COOKIE)).toBe(false);
    expect(cookieJar.has(WEB_EMAIL_TICKET_COOKIE)).toBe(false);
  });

  it("rejects client bodies that carry challenge_id, ticket or session_token", async () => {
    const rejected = await startEmailAuth({
      email: EMAIL,
      challenge_id: CHALLENGE_ID,
    });
    expect(rejected.status).toBe(400);
    expect(startUpstreamEmailAuth).not.toHaveBeenCalled();
  });

  it("does not log secrets, emails, passwords or upstream payloads", async () => {
    startUpstreamEmailAuth.mockResolvedValue(
      errorBody(429, "email_cooldown", 23),
    );
    await startEmailAuth({ email: EMAIL });
    loginUpstreamEmailPassword.mockResolvedValue(errorBody(401, "invalid_credentials"));
    await loginEmailPassword({ email: EMAIL, password: PASSWORD });
    assertNoSecrets(logs);
    assertNoEmailInLogs();
  });

  it("sets Secure on flow cookies in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    await startEmailAuth({ email: EMAIL });
    expect(cookieJar.get(WEB_EMAIL_CHALLENGE_COOKIE)?.options?.secure).toBe(true);
    vi.unstubAllEnvs();
  });

  it("replaces old ticket flow when starting register or reset", async () => {
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: CHALLENGE_ID,
        expires_in: EXPIRES_IN,
        resend_after: RESEND_AFTER,
      },
    });
    verifyUpstreamEmailOtp.mockResolvedValue({
      ok: true,
      data: { ticket: TICKET, expires_in: 900, purpose: "register" },
    });
    await startEmailAuth({ email: EMAIL });
    await verifyEmailOtp({ code: CODE });
    expect((await getEmailAuthFlowStatus()).body).toEqual({
      ok: true,
      step: "set-password",
      email: EMAIL,
    });

    const nextChallenge = "N".repeat(32);
    startUpstreamEmailAuth.mockResolvedValue({
      ok: true,
      data: {
        challenge_id: nextChallenge,
        expires_in: EXPIRES_IN,
        resend_after: 41,
      },
    });
    await startEmailAuth({ email: EMAIL });
    expect(cookieJar.has(WEB_EMAIL_TICKET_COOKIE)).toBe(false);
    expect((await getEmailAuthFlowStatus()).body).toEqual({
      ok: true,
      step: "otp",
      flow: "register",
      email: EMAIL,
      resend_after: 41,
    });

    requestUpstreamEmailPasswordReset.mockResolvedValue({
      ok: true,
      data: {
        status: "accepted",
        challenge_id: "R".repeat(32),
        expires_in: EXPIRES_IN,
        resend_after: 19,
      },
    });
    await requestEmailPasswordReset({ email: EMAIL });
    expect((await getEmailAuthFlowStatus()).body).toEqual({
      ok: true,
      step: "otp",
      flow: "password_reset",
      email: EMAIL,
      resend_after: 19,
    });
  });

  it("returns idle for malformed, expired, or incomplete flow cookies", async () => {
    cookieJar.set(WEB_EMAIL_CHALLENGE_COOKIE, {
      value: Buffer.from(
        JSON.stringify({
          v: 1,
          id: CHALLENGE_ID,
          p: "register",
          exp: Date.now() + 600_000,
        }),
        "utf8",
      ).toString("base64url"),
    });
    expect((await getEmailAuthFlowStatus()).body).toEqual({ ok: true, step: "idle" });
    expect((await verifyEmailOtp({ code: CODE })).status).toBe(400);
    expect(verifyUpstreamEmailOtp).not.toHaveBeenCalled();

    cookieJar.set(WEB_EMAIL_CHALLENGE_COOKIE, { value: "not-valid-json" });
    expect((await getEmailAuthFlowStatus()).body).toEqual({ ok: true, step: "idle" });
  });

  it("rejects extra keys, wrong types and overlong values before upstream", async () => {
    expect((await startEmailAuth({ email: EMAIL, locale: "zh" })).status).toBe(400);
    expect((await startEmailAuth({ email: "a".repeat(321) })).status).toBe(400);
    expect((await startEmailAuth({ email: 1 })).status).toBe(400);
    expect((await verifyEmailOtp({ code: "12345" })).status).toBe(400);
    expect((await verifyEmailOtp({ code: "1234567" })).status).toBe(400);
    expect((await verifyEmailOtp({ code: "１２３４５６" })).status).toBe(400);
    expect((await registerEmail({ password: PASSWORD, hint: "x" })).status).toBe(400);
    expect((await registerEmail({ password: "p".repeat(513) })).status).toBe(400);
    expect(
      (await loginEmailPassword({ email: EMAIL, password: PASSWORD, remember: true })).status,
    ).toBe(400);
    expect(startUpstreamEmailAuth).not.toHaveBeenCalled();
    expect(verifyUpstreamEmailOtp).not.toHaveBeenCalled();
    expect(registerUpstreamEmail).not.toHaveBeenCalled();
    expect(loginUpstreamEmailPassword).not.toHaveBeenCalled();
  });

  it("returns 503 for malformed or unknown 429 instead of guessing cooldown", async () => {
    startUpstreamEmailAuth.mockResolvedValue(errorBody(429, "email_cooldown"));
    const missingRetry = await startEmailAuth({ email: EMAIL });
    expect(missingRetry.status).toBe(503);
    expect(missingRetry.body.ok).toBe(false);
    if (missingRetry.body.ok) {
      throw new Error("expected failure arm");
    }
    expect("retry_after" in missingRetry.body).toBe(false);
    expect(emailAuthResponse(missingRetry).headers.get("Retry-After")).toBeNull();

    startUpstreamEmailAuth.mockResolvedValue(errorBody(429, "not_a_rate_limit", 9));
    const unknown = await startEmailAuth({ email: EMAIL });
    expect(unknown.status).toBe(503);
    expect(unknown.body.ok).toBe(false);
    if (unknown.body.ok) {
      throw new Error("expected failure arm");
    }
    expect("code" in unknown.body).toBe(false);
  });
});
